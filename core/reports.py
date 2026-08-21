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
    ("violation_type", "Violation Type", 52),
    ("vehicle_class", "Vehicle Class", 38),
    ("confidence", "Confidence", 24),
    ("detected_at", "Detected At", 40),
    ("status", "Status", 24),
)
_MAX_ROWS = 5000


def _expand_filters(filters: dict[str, Any]) -> dict[str, Any]:
    """Expand violation_type filters to include legacy aliases for DB reads."""
    expanded = dict(filters)
    vtype = expanded.get("violation_type")
    if isinstance(vtype, str) and vtype:
        expanded["violation_type"] = list(violation_type_query_names(vtype))
    return expanded


def _fetch_rows(filters: dict[str, Any]) -> list[dict[str, Any]]:
    rows, _total = db.list_violations(filters=_expand_filters(filters), page=1, per_page=_MAX_ROWS)
    return rows


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
    counts: dict[str, int] = {}
    for row in rows:
        canon = canonicalize_violation(row["violation_type"])
        counts[canon] = counts.get(canon, 0) + 1
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
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {len(rows)} record(s)",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Summary by Violation Type", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for vtype, count in _summary_counts(rows):
        pdf.cell(0, 5, f"  {vtype}: {count}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    for _key, header, width in _COLUMNS:
        pdf.cell(width, 7, header, border=1, fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for row in rows:
        for key, _header, width in _COLUMNS:
            pdf.cell(width, 6, _cell_value(row, key)[:40], border=1)
        pdf.ln()

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
    summary.append(["Violation Type", "Count"])
    summary["A1"].font = Font(bold=True)
    summary["B1"].font = Font(bold=True)
    for vtype, count in _summary_counts(rows):
        summary.append([vtype, count])

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
