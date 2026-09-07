"""Violation report generation — PDF (fpdf2) and Excel (openpyxl).

Manuscript Ch3 Reporting Module: summarized violation reports filtered by
date range, violation type, and location/source, exportable as PDF or Excel.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from config import REPORTS_FOLDER
from core.detection_config import canonicalize_violation, violation_type_query_names
from database import db


_COLUMNS = (
    ("id", "ID", 12),
    ("violation_type", "Canonical Behavior", 48),
    ("official_category", "Official Category", 52),
    ("legal_status", "Legal Status", 28),
    ("case_outcome", "Case Outcome", 28),
    ("vehicle_class", "Vehicle Class", 30),
    ("confidence", "Confidence", 20),
    ("detected_at", "Detected At", 36),
    ("status", "Status", 20),
)
_MAX_ROWS = 5000


def _expand_filters(filters: dict[str, Any]) -> dict[str, Any]:
    """Expand violation_type filters to include legacy aliases for DB reads."""
    expanded = dict(filters)
    vtype = expanded.get("violation_type")
    if isinstance(vtype, str) and vtype:
        expanded["violation_type"] = list(violation_type_query_names(vtype))
    return expanded


def _enrich_report_row(row: dict[str, Any]) -> dict[str, Any]:
    """Attach legal-policy fields for reporting without mutating storage."""
    from core.violation_policy import (
        legal_status_for,
        proposed_official_category_for,
        verified_official_category_for,
    )

    enriched = dict(row)
    vid = row.get("id")
    policy_rows = db.get_case_policy_records(vid) if vid is not None else []
    if policy_rows:
        verified = next(
            (
                p.get("official_category")
                for p in policy_rows
                if p.get("legal_status") == "verified" and p.get("official_category")
            ),
            None,
        )
        proposed = next(
            (p.get("official_category") for p in policy_rows if p.get("official_category")),
            None,
        )
        legal = next((p.get("legal_status") for p in policy_rows if p.get("legal_status")), None)
        enriched["official_category"] = verified or proposed or ""
        enriched["legal_status"] = legal or ""
        enriched["observation_count"] = len(policy_rows)
    else:
        vtype = row.get("violation_type") or ""
        verified = verified_official_category_for(vtype)
        proposed = proposed_official_category_for(vtype)
        status = legal_status_for(vtype)
        enriched["official_category"] = verified or proposed or ""
        enriched["legal_status"] = status.value if status else ""
        enriched["observation_count"] = 1

    case_confirmed = db.is_case_confirmed(vid) if vid is not None else False
    notice_printed = db.is_notice_printed(vid) if vid is not None else False
    if notice_printed:
        enriched["case_outcome"] = "notice_printed"
    elif case_confirmed:
        enriched["case_outcome"] = "case_confirmed"
    else:
        enriched["case_outcome"] = "observation_only"
    # Never truncate official wording in the enriched value itself.
    return enriched


def _fetch_rows(filters: dict[str, Any]) -> list[dict[str, Any]]:
    rows, _total = db.list_violations(filters=_expand_filters(filters), page=1, per_page=_MAX_ROWS)
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
    if value is None:
        return "-"
    if key == "confidence":
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

    # Narrower landscape table; official category uses multi-cell rows to avoid
    # silent truncation of legal wording.
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(230, 230, 230)
    col_widths = [10, 40, 55, 24, 24, 28, 16, 32, 18]
    headers = [h for _k, h, _w in _COLUMNS]
    for header, width in zip(headers, col_widths):
        pdf.cell(width, 7, header, border=1, fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 7)
    for row in rows:
        values = [_cell_value(row, key) for key, _h, _w in _COLUMNS]
        # Compute row height from the longest wrapped official category.
        official = values[2]
        lines = max(1, (len(official) // 40) + 1)
        row_h = 5 * lines
        x_start = pdf.get_x()
        y_start = pdf.get_y()
        for idx, (value, width) in enumerate(zip(values, col_widths)):
            x = x_start + sum(col_widths[:idx])
            pdf.set_xy(x, y_start)
            if idx == 2:
                pdf.multi_cell(width, 5, value, border=1)
            else:
                pdf.cell(width, row_h, value[:60], border=1)
        pdf.set_xy(x_start, y_start + row_h)

    pdf.output(str(out_path))


def _generate_excel(rows: list[dict[str, Any]], title: str, filters: dict[str, Any], out_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Violations"

    ws.append([f"TAVIDM Traffic Violation Report — {title}"])
    ws.append([f"Filters: {_filters_label(filters)}"])
    ws.append([f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — {len(rows)} record(s)"])
    ws.append([])
    ws["A1"].font = Font(bold=True, size=13)

    header_row = ws.max_row + 1
    ws.append([header for _key, header, _w in _COLUMNS])
    fill = PatternFill(start_color="E8E8E8", end_color="E8E8E8", fill_type="solid")
    for cell in ws[header_row]:
        cell.font = Font(bold=True)
        cell.fill = fill

    for row in rows:
        ws.append([_cell_value(row, key) for key, _header, _w in _COLUMNS])

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
            "Official category text is stored in full on the Violations sheet. "
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
) -> dict[str, Any]:
    """Build the report file, record it in the database, return the record."""
    if report_format not in ("pdf", "excel"):
        raise ValueError(f"Unsupported report format: {report_format}")

    filters = filters or {}
    rows = _fetch_rows(filters)
    title = title or f"Violation Report {datetime.now().strftime('%Y-%m-%d')}"

    out_dir = Path(REPORTS_FOLDER)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = "pdf" if report_format == "pdf" else "xlsx"
    out_path = out_dir / f"violation_report_{stamp}.{ext}"

    if report_format == "pdf":
        _generate_pdf(rows, title, filters, out_path)
    else:
        _generate_excel(rows, title, filters, out_path)

    base = Path(REPORTS_FOLDER).resolve().parent.parent  # project root
    try:
        rel_path = str(out_path.resolve().relative_to(base)).replace("\\", "/")
    except ValueError:
        rel_path = str(out_path)

    report_id = db.insert_report(
        title=title,
        report_format=report_format,
        file_path=rel_path,
        filters_json=json.dumps(filters),
        generated_by=generated_by,
    )
    return db.get_report(report_id)
