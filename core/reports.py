"""Violation report generation — PDF (fpdf2) and Excel (openpyxl).

Manuscript Ch3 Reporting Module: summarized violation reports filtered by
date range, violation type, and location/source, exportable as PDF or Excel.
"""

from __future__ import annotations

import json
import struct
import uuid
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.drawing.image import Image as SpreadsheetImage
from openpyxl.styles import Font, PatternFill

from config import REPORTS_FOLDER
from core.detection_config import canonicalize_violation, violation_type_query_names
from database import db

_REPORT_LOGO = Path(__file__).resolve().parents[1] / "static" / "images" / "tavidm-report-logo.png"


class ReportLogoError(RuntimeError):
    """The approved report brand image is unavailable or unreadable."""


def _validated_report_logo() -> Path:
    try:
        if _REPORT_LOGO.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("PNG logo exceeds the file-size limit")
        data = _REPORT_LOGO.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid PNG signature")
        offset = 8
        width = height = bit_depth = color_type = interlace = None
        compressed: list[bytes] = []
        saw_end = False
        while offset + 12 <= len(data):
            length = struct.unpack(">I", data[offset:offset + 4])[0]
            chunk_type = data[offset + 4:offset + 8]
            end = offset + 12 + length
            if end > len(data):
                raise ValueError("truncated PNG chunk")
            chunk_data = data[offset + 8:offset + 8 + length]
            expected_crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
            if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
                raise ValueError("invalid PNG chunk checksum")
            if offset == 8 and chunk_type != b"IHDR":
                raise ValueError("PNG header is not the first chunk")
            if chunk_type == b"IHDR":
                if width is not None or length != 13:
                    raise ValueError("invalid PNG header")
                width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
                    ">IIBBBBB", chunk_data
                )
                if not width or not height or width * height > 16_000_000:
                    raise ValueError("invalid PNG dimensions")
                if compression != 0 or filter_method != 0 or interlace != 0:
                    raise ValueError("unsupported PNG encoding")
            elif chunk_type == b"IDAT":
                if width is None or saw_end:
                    raise ValueError("PNG image data is out of order")
                compressed.append(chunk_data)
            elif chunk_type == b"IEND":
                if length != 0 or not compressed:
                    raise ValueError("invalid PNG end chunk")
                saw_end = True
                offset = end
                break
            offset = end
        if not saw_end or width is None or not compressed or offset != len(data):
            raise ValueError("incomplete PNG image")
        channels_by_type = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
        channels = channels_by_type.get(color_type)
        valid_depths = {
            0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8},
            4: {8, 16}, 6: {8, 16},
        }
        if channels is None or bit_depth not in valid_depths.get(color_type, set()):
            raise ValueError("unsupported PNG pixel format")
        expected_bytes = height * (1 + (width * channels * bit_depth + 7) // 8)
        if expected_bytes > 64 * 1024 * 1024:
            raise ValueError("PNG decompressed data exceeds the logo limit")
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(b"".join(compressed), expected_bytes + 1)
        if (
            len(decoded) != expected_bytes
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
            or offset != len(data)
        ):
            raise ValueError("invalid PNG pixel data")
        row_bytes = (width * channels * bit_depth + 7) // 8
        if any(decoded[row * (row_bytes + 1)] > 4 for row in range(height)):
            raise ValueError("invalid PNG row filter")
    except ReportLogoError:
        raise
    except (OSError, ValueError, struct.error, zlib.error) as exc:
        raise ReportLogoError(
            "The approved TAVIDM report logo is missing or unreadable. Restore "
            "static/images/tavidm-report-logo.png, then retry."
        ) from exc
    return _REPORT_LOGO


_COLUMNS = (
    ("id", "ID", 12),
    ("violation_type", "Canonical Behavior", 48),
    ("official_category", "Proposed / Verified Category", 52),
    ("legal_status", "Legal Mapping Status", 32),
    ("plate_display", "Plate (verification)", 68),
    ("case_outcome", "Case Outcome", 28),
    ("vehicle_class", "Vehicle Class", 30),
    ("detection_confidence", "Detector Model Score", 18),
    ("violation_confidence", "Rule Evidence Score", 18),
    ("detected_at", "Detected At", 36),
    ("status", "Status", 20),
)


def _wrap_pdf_cell_text(pdf: FPDF, value: str, cell_width: float) -> list[str]:
    """Wrap table text to the actual printable width of a PDF cell."""
    inner_width = cell_width - 2 - 2 * pdf.c_margin
    output: list[str] = []
    for paragraph in value.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if pdf.get_string_width(candidate) <= inner_width:
                line = candidate
                continue
            if line:
                output.append(line)
                line = ""
            if pdf.get_string_width(word) <= inner_width:
                line = word
                continue
            piece = ""
            for char in word:
                if piece and pdf.get_string_width(piece + char) > inner_width:
                    output.append(piece)
                    piece = char
                else:
                    piece += char
            line = piece
        output.append(line)
    return output or [""]
_MAX_ROWS = 5000


class ReportTooLargeError(ValueError):
    """The complete selection exceeds the explicit bounded export size."""

    def __init__(self, total_cases: int) -> None:
        self.total_cases = int(total_cases)
        super().__init__(
            f"This selection contains {self.total_cases} cases; exports are limited to "
            f"{_MAX_ROWS:,}. Narrow the filters and try again."
        )


def _expand_filters(filters: dict[str, Any]) -> dict[str, Any]:
    """Expand violation_type filters to include legacy aliases for DB reads."""
    expanded = dict(filters)
    vtype = expanded.get("violation_type")
    if isinstance(vtype, str) and vtype:
        expanded["violation_type"] = list(violation_type_query_names(vtype))
    return expanded


def preview_video(
    video_id: int,
    filters: dict[str, Any],
    *,
    page: int,
    per_page: int,
    owner_user_id: int | None = None,
    uploader_id: int | None = None,
) -> dict[str, Any] | None:
    """Return one video page plus complete, case-ID-based totals."""
    from core.detection_config import canonicalize_violation
    from core.violation_policy import (
        legal_status_for,
        proposed_official_category_for,
        verified_official_category_for,
    )

    result = db.report_video_preview(
        int(video_id),
        _expand_filters(filters),
        page=page,
        per_page=per_page,
        owner_user_id=owner_user_id,
        uploader_id=uploader_id,
    )
    if result is None:
        return None

    counts: dict[str, int] = {}
    for entry in result["type_counts"]:
        canonical = canonicalize_violation(str(entry["violation_type"]))
        counts[canonical] = counts.get(canonical, 0) + int(entry["count"])
    result["category_counts"] = [
        {"violation_type": name, "count": count}
        for name, count in sorted(counts.items())
    ]

    output_items = []
    for row in result["items"]:
        vtype = str(row.get("violation_type") or "")
        policy_rows = row.pop("policy_records", [])
        if policy_rows:
            pairs = []
            for policy in policy_rows:
                pair = (
                    str(policy.get("canonical_rule") or vtype),
                    str(policy.get("official_category") or "No official category"),
                    str(policy.get("legal_status") or "unknown"),
                )
                if pair not in pairs:
                    pairs.append(pair)
            official_category = "; ".join(
                f"{rule}: {category} [{status}]"
                for rule, category, status in pairs
            )
            legal_status = "; ".join(
                f"{rule}: {status}" for rule, _category, status in pairs
            )
            observation_count = len(policy_rows)
        else:
            official_category = (
                verified_official_category_for(vtype)
                or proposed_official_category_for(vtype)
                or ""
            )
            status = legal_status_for(vtype)
            legal_status = status.value if status else ""
            observation_count = int(row.get("observation_count") or 1)
        output_items.append(
            {
                "id": int(row["id"]),
                "violation_type": canonicalize_violation(vtype),
                "detected_at": row.get("detected_at"),
                "status": row.get("status"),
                "track_id": row.get("track_id"),
                "processing_run_id": row.get("processing_run_id"),
                "run_status": row.get("run_status"),
                "observation_count": observation_count,
                "official_category": official_category,
                "legal_status": legal_status,
                "case_outcome": row.get("case_outcome"),
            }
        )

    video = result["video"]
    latest = result.get("latest_run")
    total = int(result["total"])
    if latest and latest.get("status") in {"queued", "running"}:
        result_state = "processing"
    elif latest and latest.get("status") == "completed":
        result_state = "completed_with_cases" if total else "completed_empty"
    elif latest and latest.get("status") in {"failed", "cancelled"}:
        result_state = "previous_results_available" if total else "failed_no_results"
    elif total:
        result_state = "legacy_results_available"
    elif video.get("status") == "processed" or video.get("processed"):
        result_state = "legacy_processed_no_run"
    else:
        result_state = "not_processed"

    return {
        "video": video,
        "latest_run": latest,
        "result_state": result_state,
        "items": output_items,
        "total_cases": total,
        "category_counts": result["category_counts"],
        "pagination": {
            "page": int(page),
            "per_page": int(per_page),
            "pages": (total + int(per_page) - 1) // int(per_page),
        },
        "export_limit": {
            "max_rows": _MAX_ROWS,
            "exceeded": total > _MAX_ROWS,
        },
    }


def _enrich_report_row(row: dict[str, Any]) -> dict[str, Any]:
    """Attach legal-policy fields for reporting without mutating storage."""
    from core.violation_policy import (
        legal_status_for,
        proposed_official_category_for,
        verified_official_category_for,
    )

    enriched = dict(row)
    vid = row.get("id")
    snapshot_actions = row.get("case_action_records")
    policy_rows = (
        row.get("policy_records", [])
        if "policy_records" in row
        else db.get_case_policy_records(vid) if vid is not None else []
    )
    if policy_rows:
        policy_pairs = []
        for policy in policy_rows:
            behavior = str(policy.get("canonical_rule") or "Behavior")
            category = str(policy.get("official_category") or "No official category")
            status = str(policy.get("legal_status") or "unknown")
            pair = (behavior, category, status)
            if pair not in policy_pairs:
                policy_pairs.append(pair)
        enriched["official_category"] = "; ".join(
            f"{behavior}: {category} [{status}]"
            for behavior, category, status in policy_pairs
        )
        enriched["legal_status"] = "; ".join(
            f"{behavior}: {status}"
            for behavior, _category, status in policy_pairs
        )
        enriched["observation_count"] = len(policy_rows)
    else:
        vtype = row.get("violation_type") or ""
        verified = verified_official_category_for(vtype)
        proposed = proposed_official_category_for(vtype)
        status = legal_status_for(vtype)
        enriched["official_category"] = verified or proposed or ""
        enriched["legal_status"] = status.value if status else ""
        enriched["observation_count"] = 1

    if "case_action_types" in row:
        actions = set(row.get("case_action_types") or [])
        case_confirmed = "case_confirmed" in actions
        notice_printed = "notice_printed" in actions
    else:
        case_confirmed = db.is_case_confirmed(vid) if vid is not None else False
        notice_printed = db.is_notice_printed(vid) if vid is not None else False
    if notice_printed:
        enriched["case_outcome"] = "notice_printed"
    elif case_confirmed:
        enriched["case_outcome"] = "case_confirmed"
    else:
        enriched["case_outcome"] = "observation_only"

    # A report may include an OCR string for research visibility, but it must
    # remain explicitly distinguished from a human-verified plate identity.
    plate_status = str(row.get("plate_status") or "").strip()
    plate_text = str(row.get("plate_text") or "").strip()
    if plate_text and plate_status in {"recognized", "verified_readable"}:
        enriched["plate_display"] = f"{plate_text} (verified)"
    else:
        enriched["plate_display"] = "N/A"
        if vid is not None:
            try:
                from core.plate_review import machine_result_for_violation

                if snapshot_actions is not None:
                    if any(action.get("review_id") is not None for action in snapshot_actions):
                        class SnapshotActions:
                            @staticmethod
                            def get_case_actions(_violation_id: int) -> list[dict[str, Any]]:
                                return snapshot_actions

                        machine = machine_result_for_violation(SnapshotActions(), int(vid))
                    else:
                        machine = None
                else:
                    machine = machine_result_for_violation(db, int(vid))
                candidates = (machine or {}).get("candidates") or []
                primary = next(
                    (c for c in candidates if c.get("candidate_id") == machine.get("primary_candidate_id")),
                    candidates[0] if candidates else None,
                )
                candidate_text = str((primary or {}).get("ocr_raw") or "").strip()
                if candidate_text:
                    suffix = "; association uncertain" if machine.get("association_uncertain") else ""
                    enriched["plate_display"] = f"{candidate_text} (OCR candidate; unverified{suffix})"
            except Exception:  # noqa: BLE001 - report still works if machine evidence is unavailable
                enriched["plate_display"] = "N/A"

    # Never truncate category wording in the enriched value itself.
    return enriched


def _fetch_rows(
    filters: dict[str, Any],
    *,
    owner_user_id: int | None = None,
    uploader_id: int | None = None,
) -> list[dict[str, Any]]:
    snapshot = db.report_export_snapshot(
        _expand_filters(filters),
        limit=_MAX_ROWS,
        owner_user_id=owner_user_id,
        uploader_id=uploader_id,
        source_video_only=owner_user_id is not None or uploader_id is not None,
    )
    if snapshot["total"] > _MAX_ROWS:
        raise ReportTooLargeError(snapshot["total"])
    rows = snapshot["rows"]
    return [_enrich_report_row(row) for row in rows]


def _filters_label(filters: dict[str, Any]) -> str:
    parts: list[str] = []
    if filters.get("date_from") or filters.get("date_to"):
        parts.append(f"{filters.get('date_from', '...')} to {filters.get('date_to', '...')}")
    if filters.get("violation_type"):
        parts.append(filters["violation_type"])
    if filters.get("status"):
        parts.append(filters["status"].title())
    return " / ".join(parts) if parts else "All violations"


def _cell_value(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if key == "violation_confidence" and value is None:
        value = row.get("confidence")
    if value is None:
        return "-"
    if key in {"confidence", "detection_confidence", "violation_confidence"}:
        return f"{float(value) * 100:.0f}%"
    # Core PDF fonts are latin-1 only; strip anything outside that range.
    return str(value).encode("latin-1", "replace").decode("latin-1")


def _summary_counts(rows: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """Observation counts by canonical behavior (not grouped-case counts)."""
    counts: dict[str, int] = {}
    for row in rows:
        canon = canonicalize_violation(row["violation_type"])
        # Prefer observation_count when a fused case retained multiple behaviors.
        weight = int(row.get("observation_count") or 1)
        # For report rows, each violation row is one grouped case; contributing
        # behaviors are listed separately in the Official Category column.
        counts[canon] = counts.get(canon, 0) + 1
        _ = weight  # retained for callers inspecting enriched rows
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)


def _case_outcome_counts(rows: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        key = row.get("case_outcome") or "observation_only"
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)


def _generate_pdf(rows: list[dict[str, Any]], title: str, filters: dict[str, Any], out_path: Path) -> None:
    pdf = FPDF(orientation="L")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    heading_y = pdf.get_y()
    logo_path = _validated_report_logo()
    with open(logo_path, "rb") as logo_file:
        logo_file.seek(16)
        logo_width, logo_height = struct.unpack(">II", logo_file.read(8))
    pdf.image(
        str(logo_path), x=pdf.l_margin, y=heading_y, w=8,
        h=8 * logo_height / logo_width,
    )
    pdf.set_xy(pdf.l_margin + 11, heading_y)
    pdf.cell(0, 10, "TAVIDM - Traffic Violation Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Filters: {_filters_label(filters)}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        0, 6,
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {len(rows)} grouped case record(s)",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Summary by Canonical Behavior (grouped cases)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for vtype, count in _summary_counts(rows):
        pdf.cell(0, 5, f"  {vtype}: {count}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Summary by Case Outcome", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for outcome, count in _case_outcome_counts(rows):
        pdf.cell(0, 5, f"  {outcome}: {count}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Narrower landscape table; policy associations and plate labels use
    # multi-cell rows so fused records do not lose legal/evidence wording.
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(
        0, 4,
        "Legal mapping status describes whether the behavior-to-law classification "
        "has been verified against authoritative sources; it is separate from case "
        "review and plate verification. Detector Model Score is the detector output; "
        "Rule Evidence Score is the rule engine evidence score. Neither is a "
        "probability of guilt. Plate values marked OCR candidate are machine "
        "suggestions and are not verified identities.",
    )
    pdf.ln(2)
    col_widths = [9, 33, 45, 24, 35, 20, 22, 14, 14, 27, 17]
    headers = [h for _k, h, _w in _COLUMNS]

    def draw_table_header() -> None:
        header_font_size = 7.5
        header_line_height = 4.0
        pdf.set_font("Helvetica", "B", header_font_size)
        pdf.set_fill_color(230, 230, 230)
        wrapped_headers = [
            # Header text cells have no border (the background rectangle is
            # drawn separately), so they can use the full width minus margins.
            _wrap_pdf_cell_text(pdf, header, width + 2) for header, width in zip(headers, col_widths)
        ]
        header_height = max(8.0, max(len(lines) for lines in wrapped_headers) * header_line_height + 2.0)
        y = pdf.get_y()
        x = pdf.l_margin
        for width, lines in zip(col_widths, wrapped_headers):
            pdf.set_xy(x, y)
            pdf.cell(width, header_height, "", border=1, fill=True)
            content_y = y + (header_height - len(lines) * header_line_height) / 2.0
            for line_index, line in enumerate(lines):
                pdf.set_xy(x, content_y + line_index * header_line_height)
                pdf.cell(width, header_line_height, line, align="C")
            x += width
        pdf.set_xy(pdf.l_margin, y + header_height)
        pdf.set_font("Helvetica", "", 7)

    draw_table_header()

    pdf.set_font("Helvetica", "", 7)
    pdf.set_auto_page_break(auto=False)
    for row in rows:
        values = [_cell_value(row, key) for key, _h, _w in _COLUMNS]
        wrapped = [_wrap_pdf_cell_text(pdf, value, width) for value, width in zip(values, col_widths)]
        total_lines = max(len(lines) for lines in wrapped)
        y = pdf.get_y()
        line_capacity = max(1, int((pdf.h - pdf.b_margin - y) // 5))
        if total_lines > line_capacity:
            pdf.add_page()
            draw_table_header()
            y = pdf.get_y()
            line_capacity = max(1, int((pdf.h - pdf.b_margin - y) // 5))

        offset = 0
        while offset < total_lines:
            y = pdf.get_y()
            capacity = max(1, int((pdf.h - pdf.b_margin - y) // 5))
            chunk_lines = min(capacity, total_lines - offset)
            if chunk_lines <= 0:
                pdf.add_page()
                draw_table_header()
                continue
            row_h = chunk_lines * 5
            x = pdf.l_margin
            for idx, (lines, width) in enumerate(zip(wrapped, col_widths)):
                pdf.rect(x, y, width, row_h)
                chunk = lines[offset:offset + chunk_lines]
                if offset and idx == 0:
                    chunk = [values[0]] + chunk[1:]
                for line_index, text_line in enumerate(chunk):
                    pdf.set_xy(x + 1, y + line_index * 5)
                    pdf.cell(width - 2, 5, text_line)
                x += width
            pdf.set_xy(pdf.l_margin, y + row_h)
            offset += chunk_lines
            if offset < total_lines:
                pdf.add_page()
                draw_table_header()
    pdf.set_auto_page_break(auto=True, margin=12)

    pdf.output(str(out_path))


def _generate_excel(rows: list[dict[str, Any]], title: str, filters: dict[str, Any], out_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Violations"

    ws.append([None, f"TAVIDM Traffic Violation Report — {title}"])
    ws.merge_cells("B1:K1")
    logo_path = _validated_report_logo()
    logo = SpreadsheetImage(str(logo_path))
    native_width, native_height = logo.width, logo.height
    logo.width = 34
    logo.height = 34 * native_height / native_width
    ws.add_image(logo, "A1")
    ws.row_dimensions[1].height = 28
    ws.append([f"Filters: {_filters_label(filters)}"])
    ws.append([f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — {len(rows)} record(s)"])
    ws.append([])
    ws["B1"].font = Font(bold=True, size=13)

    header_row = ws.max_row + 1
    ws.append([header for _key, header, _w in _COLUMNS])
    fill = PatternFill(start_color="E8E8E8", end_color="E8E8E8", fill_type="solid")
    for cell in ws[header_row]:
        cell.font = Font(bold=True)
        cell.fill = fill

    for row in rows:
        ws.append([_cell_value(row, key) for key, _header, _w in _COLUMNS])

    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = "1:5"
    ws.print_area = f"A1:K{max(ws.max_row, header_row)}"
    ws.sheet_view.showGridLines = False

    for i, (_key, _header, width) in enumerate(_COLUMNS, start=1):
        ws.column_dimensions[chr(64 + i)].width = max(width // 2, 12)

    summary = wb.create_sheet("Summary")
    summary.append(["Canonical Behavior (grouped cases)", "Count"])
    summary["A1"].font = Font(bold=True)
    summary["B1"].font = Font(bold=True)
    for vtype, count in _summary_counts(rows):
        summary.append([vtype, count])
    summary.append([])
    summary.append(["Case Outcome", "Count"])
    for outcome, count in _case_outcome_counts(rows):
        summary.append([outcome, count])
    summary.append([])
    summary.append(
        [
            "Note",
            "Official/proposed category wording is stored on the Violations sheet. "
            "Legal Mapping Status concerns validation of behavior-to-law mappings. "
            "Detector Model Score and Rule Evidence Score are distinct scores, not "
            "probabilities of guilt. "
            "it is separate from human case review and plate verification. Plate "
            "values marked OCR candidate are unverified machine suggestions. "
            "Observation counts in case_policy_records may exceed grouped-case counts "
            "when parking/obstruction behaviors are fused. Currency values are not "
            "auto-applied from unverified schedules.",
        ]
    )

    wb.save(str(out_path))


def generate_report(
    report_format: str,
    filters: dict[str, Any] | None = None,
    title: str | None = None,
    generated_by: int | None = None,
    *,
    owner_user_id: int | None = None,
    uploader_id: int | None = None,
) -> dict[str, Any]:
    """Build the report file, record it in the database, return the record."""
    if report_format not in ("pdf", "excel"):
        raise ValueError(f"Unsupported report format: {report_format}")

    filters = filters or {}
    if owner_user_id is None and uploader_id is None:
        rows = _fetch_rows(filters)
    else:
        rows = _fetch_rows(
            filters, owner_user_id=owner_user_id, uploader_id=uploader_id
        )
    title = title or f"Violation Report {datetime.now().strftime('%Y-%m-%d')}"

    _validated_report_logo()

    out_dir = Path(REPORTS_FOLDER)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = "pdf" if report_format == "pdf" else "xlsx"
    out_path = out_dir / f"violation_report_{stamp}_{uuid.uuid4().hex[:8]}.{ext}"

    try:
        if report_format == "pdf":
            _generate_pdf(rows, title, filters, out_path)
        else:
            _generate_excel(rows, title, filters, out_path)
    except Exception:
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    base = Path(__file__).resolve().parents[1]
    try:
        rel_path = str(out_path.resolve().relative_to(base)).replace("\\", "/")
    except ValueError:
        rel_path = str(out_path)

    try:
        report_id = db.insert_report(
            title=title,
            report_format=report_format,
            file_path=rel_path,
            filters_json=json.dumps(filters),
            generated_by=generated_by,
        )
    except Exception:
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return db.get_report(report_id)
