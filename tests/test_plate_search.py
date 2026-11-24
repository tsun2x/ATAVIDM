import pytest

from core.case_review_service import stage_review_plate_choice


@pytest.fixture(autouse=True)
def isolated_admin_password(monkeypatch):
    monkeypatch.setenv("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "plate-search-test-only-2026")


def test_plate_filter_and_groups_span_only_accessible_verified_cases(enforcer_client, test_db):
    owner = test_db.get_user_by_username("enforcer_test")["id"]
    other = test_db.create_user("other_owner", "unused", role="enforcer")

    def case(filename, uploader, plate, verified):
        video = test_db.insert_video(filename, f"/tmp/{filename}", uploaded_by=uploader)
        case_id = test_db.insert_violation(
            video, 7, "Counterflow", 0.9, 30, 1.0,
            plate_text=plate, plate_status="recognized",
        )
        if verified:
            test_db.record_plate_verification(
                case_id, accepted_plate_text=plate, plate_status="verified_readable"
            )
        return case_id

    first = case("first.mp4", owner, "ABC 1234", True)
    second = case("second.mp4", owner, "abc-1234", True)
    case("unverified.mp4", owner, "ABC 1234", False)
    case("private.mp4", other, "ABC 1234", True)

    result = enforcer_client.get("/api/violations", query_string={"plate": "abc-1234", "per_page": 1}).get_json()
    assert result["total"] == 2
    assert len(result["items"]) == 1

    result = enforcer_client.get(
        "/api/violations/vehicle-groups", query_string={"group_by": "plate", "plate": "abc-1234"},
    ).get_json()
    assert result["group_total"] == 1
    group = result["groups"][0]
    assert group["plate_key"] == "ABC1234"
    assert {item["db_id"] for item in group["events"]} == {first, second}


def test_review_plate_search_excludes_raw_and_superseded_plate_text(enforcer_client, test_db):
    owner = test_db.get_user_by_username("enforcer_test")["id"]
    admin = test_db.get_user_by_username("admin")["id"]
    other = test_db.create_user("other_owner", "unused", role="enforcer")

    def review(filename, uploader):
        video = test_db.insert_video(filename, f"/tmp/{filename}", uploaded_by=uploader)
        return test_db.insert_review_queue(video, 7, "Counterflow", 0.9, 30, plate_text="ABC1234")

    matching = review("matching.mp4", owner)
    second_match = review("second_match.mp4", owner)
    corrected = review("corrected.mp4", owner)
    raw = review("raw.mp4", owner)
    private = review("private.mp4", other)
    for item in (matching, second_match, corrected, private):
        stage_review_plate_choice(test_db, item, admin, accepted_plate_text="ABC 1234")
    stage_review_plate_choice(test_db, corrected, admin, accepted_plate_text="XYZ 9999")

    response = enforcer_client.get("/review-queue", query_string={"plate": "abc-1234"})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f"RQ-{matching:05d}" in html
    assert f"RQ-{second_match:05d}" in html
    for item in (corrected, raw, private):
        assert f"RQ-{item:05d}" not in html

    first_page = enforcer_client.get(
        "/review-queue", query_string={"plate": "abc-1234", "per_page": 1}
    ).get_data(as_text=True)
    assert 'page=2&amp;per_page=1&amp;plate=abc-1234' in first_page
    assert f"RQ-{second_match:05d}" in first_page
    assert f"RQ-{matching:05d}" not in first_page
