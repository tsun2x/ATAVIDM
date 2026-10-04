from core import reports


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
