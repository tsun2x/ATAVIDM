from core import reports
import re
import zlib


def _stub_report_database(monkeypatch):
    monkeypatch.setattr(reports.db, "get_case_policy_records", lambda _vid: [])
    monkeypatch.setattr(reports.db, "is_case_confirmed", lambda _vid: True)
    monkeypatch.setattr(reports.db, "is_notice_printed", lambda _vid: False)


def test_report_includes_verified_plate_text(monkeypatch):
    _stub_report_database(monkeypatch)

    row = reports._enrich_report_row(
        {
            "id": 12,
            "violation_type": "Illegal Parking",
            "plate_status": "recognized",
            "plate_text": "JAD7419",
        }
    )

    assert row["plate_display"] == "JAD7419 (verified)"


def test_report_labels_machine_text_as_unverified_candidate(monkeypatch):
    _stub_report_database(monkeypatch)
    monkeypatch.setattr(
        "core.plate_review.machine_result_for_violation",
        lambda _db, _vid: {
            "primary_candidate_id": "candidate-1",
            "candidates": [{"candidate_id": "candidate-1", "ocr_raw": "JAD7419"}],
            "association_uncertain": False,
        },
    )

    row = reports._enrich_report_row(
        {
            "id": 12,
            "violation_type": "Illegal Parking",
            "plate_status": "not_attempted",
            "plate_text": None,
        }
    )

    assert row["plate_display"] == "JAD7419 (OCR candidate; unverified)"
    assert "plate_display" in [key for key, _header, _width in reports._COLUMNS]
    assert dict((key, header) for key, header, _width in reports._COLUMNS)["legal_status"] == (
        "Legal Mapping Status"
    )


def test_report_marks_uncertain_candidate_association(monkeypatch):
    _stub_report_database(monkeypatch)
    monkeypatch.setattr(
        "core.plate_review.machine_result_for_violation",
        lambda _db, _vid: {
            "primary_candidate_id": "candidate-1",
            "candidates": [{"candidate_id": "candidate-1", "ocr_raw": "JAD7419"}],
            "association_uncertain": True,
        },
    )

    row = reports._enrich_report_row(
        {"id": 12, "violation_type": "Illegal Parking", "plate_status": "not_attempted"}
    )

    assert row["plate_display"] == "JAD7419 (OCR candidate; unverified; association uncertain)"


def test_report_keeps_category_and_legal_status_paired_per_contributing_behavior(monkeypatch):
    _stub_report_database(monkeypatch)
    monkeypatch.setattr(
        reports.db,
        "get_case_policy_records",
        lambda _vid: [
            {
                "canonical_rule": "Illegal Parking",
                "official_category": "Parking Offense",
                "legal_status": "unverified",
            },
            {
                "canonical_rule": "Obstruction",
                "official_category": "Obstruction of Traffic Flow",
                "legal_status": "verified",
            },
        ],
    )

    row = reports._enrich_report_row({"id": 12, "violation_type": "Illegal Parking"})

    assert row["official_category"] == (
        "Illegal Parking: Parking Offense [unverified]; "
        "Obstruction: Obstruction of Traffic Flow [verified]"
    )
    assert row["legal_status"] == (
        "Illegal Parking: unverified; Obstruction: verified"
    )


def test_excel_export_contains_plate_column_and_candidate_label(tmp_path):
    row = {
        "id": 12,
        "violation_type": "Illegal Parking",
        "official_category": "Obstruction of Traffic Flow",
        "legal_status": "unverified",
        "plate_display": "JAD7419 (OCR candidate; unverified)",
        "case_outcome": "case_confirmed",
        "vehicle_class": "Passenger Vehicle",
        "confidence": 0.87,
        "detected_at": "2026-10-04 07:26:10",
        "status": "confirmed",
    }
    output = tmp_path / "report.xlsx"

    reports._generate_excel([row], "test", {}, output)

    from openpyxl import load_workbook

    worksheet = load_workbook(output, read_only=True)["Violations"]
    headers = [cell.value for cell in worksheet[5]]
    plate_column = headers.index("Plate (verification)") + 1
    assert worksheet.cell(row=6, column=plate_column).value == row["plate_display"]


def test_pdf_export_generates_with_long_unverified_plate_label(tmp_path):
    row = {
        "id": 12,
        "violation_type": "Illegal Parking",
        "official_category": "Obstruction of Traffic Flow",
        "legal_status": "unverified",
        "plate_display": "JAD7419 (OCR candidate; unverified; association uncertain)",
        "case_outcome": "case_confirmed",
        "vehicle_class": "Passenger Vehicle",
        "confidence": 0.87,
        "detected_at": "2026-10-04 07:26:10",
        "status": "confirmed",
    }
    output = tmp_path / "report.pdf"

    reports._generate_pdf([row], "test", {}, output)

    assert output.is_file()
    assert output.stat().st_size > 0


def test_pdf_keeps_fused_rows_together_and_splits_oversized_rows_without_losing_text(tmp_path):
    rows = [
        {
            "id": 100 + index,
            "violation_type": "Illegal Parking + Obstruction",
            "official_category": (
                "Illegal Parking: Parking Offense [unverified]; "
                "Obstruction: Obstruction of Traffic Flow [verified]"
            ),
            "legal_status": "Illegal Parking: unverified; Obstruction: verified",
            "plate_display": "JAD7419 (OCR candidate; unverified; association uncertain)",
            "case_outcome": "case_confirmed",
            "vehicle_class": "Passenger Vehicle",
            "confidence": 0.87,
            "detected_at": "2026-10-04 07:26:10",
            "status": "confirmed",
        }
        for index in range(12)
    ]
    rows.append({
        **rows[0],
        "id": 999,
        "official_category": "Evidence phrase " * 180,
        "legal_status": "Detailed status phrase " * 120,
        "plate_display": "Long verification detail " * 100,
    })
    output = tmp_path / "fused-pagination.pdf"

    reports._generate_pdf(rows, "pagination", {}, output)

    data = output.read_bytes()
    streams = re.findall(rb"stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL)
    rendered_text = b"\n".join(
        zlib.decompress(stream) for stream in streams
        if stream and stream.startswith(b"x")
    )
    page_count = data.count(b"/Type /Page\n") + data.count(b"/Type /Page ")
    assert data.startswith(b"%PDF")
    assert page_count < 20
    for case_id in [str(100 + index) for index in range(12)] + ["999"]:
        assert f"({case_id}) Tj".encode("ascii") in rendered_text
    assert b"Parking Offense" in rendered_text
    assert b"association" in rendered_text
    assert b"uncertain" in rendered_text
    assert b"Long" in rendered_text
    assert b"verification" in rendered_text
    assert b"detail" in rendered_text


def test_pdf_export_handles_empty_report(tmp_path):
    output = tmp_path / "empty-report.pdf"

    reports._generate_pdf([], "empty", {}, output)

    assert output.is_file()
    assert output.read_bytes().startswith(b"%PDF")
