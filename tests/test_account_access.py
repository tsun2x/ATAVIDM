"""Account-access presentation and role-gated review list APIs."""

from __future__ import annotations

import bcrypt
import pytest

from database import db


@pytest.fixture(autouse=True)
def isolated_bootstrap_password(monkeypatch):
    monkeypatch.setenv("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "account-access-test-only-2026")


@pytest.fixture
def admin_client(client):
    client.post(
        "/login",
        data={"username": "admin", "password": "account-access-test-only-2026"},
    )
    return client


@pytest.fixture
def viewer_client(client):
    db.create_user(
        "access_viewer",
        bcrypt.hashpw(b"viewer-pass", bcrypt.gensalt()).decode("utf-8"),
        role="viewer",
    )
    client.post("/login", data={"username": "access_viewer", "password": "viewer-pass"})
    return client


def test_review_list_apis_match_enforcer_page_access(viewer_client, test_db):
    viewer_response = viewer_client.get("/api/review-queue")
    viewer_decisions = viewer_client.get("/api/review-queue/decisions")
    viewer_motorcycles = viewer_client.get("/api/motorcycle-detail-review")
    assert viewer_client.get("/settings").status_code == 302
    assert viewer_response.status_code == 403
    assert viewer_decisions.status_code == 403
    assert viewer_motorcycles.status_code == 403
    viewer_case_actions = viewer_client.get("/api/violations").get_json()["case_actions"]
    assert viewer_case_actions["confirm_case"] is False
    assert viewer_case_actions["prepare_notice"] is False

    test_db.create_user(
        "access_enforcer",
        bcrypt.hashpw(b"enforcer-pass", bcrypt.gensalt()).decode("utf-8"),
        role="enforcer",
    )
    viewer_client.post("/login", data={"username": "access_enforcer", "password": "enforcer-pass"})
    assert viewer_client.get("/settings").status_code == 302
    assert viewer_client.get("/api/review-queue").status_code == 200
    assert viewer_client.get("/api/review-queue/decisions").status_code == 200
    assert viewer_client.get("/api/motorcycle-detail-review").status_code == 200
    enforcer_case_actions = viewer_client.get("/api/violations").get_json()["case_actions"]
    assert enforcer_case_actions["confirm_case"] is True
    assert enforcer_case_actions["attest_print"] is True
    assert enforcer_case_actions["prepare_notice"] is True
    assert enforcer_case_actions["confirm_plate_identity"] is False


def test_admin_user_access_view_shows_effective_and_explicit_policy_permissions(
    admin_client, test_db
):
    viewer_id = test_db.create_user(
        "access_target",
        bcrypt.hashpw(b"viewer-pass", bcrypt.gensalt()).decode("utf-8"),
        role="viewer",
    )
    test_db.assign_policy_permission(viewer_id, "confirm_case", 1, reason="Delegated review")

    response = admin_client.get("/settings")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Role Access Defaults" in html
    assert "Individual Account Access" in html
    access_response = admin_client.get(f"/api/users/{viewer_id}/access")
    assert access_response.status_code == 200
    access = access_response.get_json()["access"]
    assert access["role_defaults"]["approve_policy"] is False
    assert access["effective_permissions"]["confirm_case"] is True
    assert access["effective_permissions"]["approve_policy"] is False
    assert access["explicit_policy_grants"][0]["reason"] == "Delegated review"
    assert access["explicit_policy_grants"][0]["label"] == "Confirm enforcement case"


def test_user_access_api_is_admin_only_and_does_not_allow_client_grants(client, test_db):
    user_id = test_db.create_user(
        "access_target",
        bcrypt.hashpw(b"viewer-pass", bcrypt.gensalt()).decode("utf-8"),
        role="viewer",
    )
    test_db.create_user(
        "access_enforcer",
        bcrypt.hashpw(b"enforcer-pass", bcrypt.gensalt()).decode("utf-8"),
        role="enforcer",
    )
    client.post("/login", data={"username": "access_enforcer", "password": "enforcer-pass"})
    assert client.get(f"/api/users/{user_id}/access").status_code == 403
    assert client.put(
        f"/api/users/{user_id}/access", json={"grants": [{"permission": "approve_policy"}]}
    ).status_code == 403
    assert client.put(
        f"/api/users/{user_id}/access", json={"revokes": ["confirm_case"]}
    ).status_code == 403


def test_admin_can_grant_and_revoke_special_permission_without_removing_role_access(
    admin_client, test_db
):
    enforcer_id = test_db.create_user(
        "access_enforcer",
        bcrypt.hashpw(b"enforcer-pass", bcrypt.gensalt()).decode("utf-8"),
        role="enforcer",
    )
    actor_id = test_db.get_user_by_username("admin")["id"]
    granted = admin_client.put(
        f"/api/users/{enforcer_id}/access",
        json={"grants": [{"permission": "confirm_case", "reason": "Coverage"}]},
    )
    assert granted.status_code == 200
    assert test_db.get_user_permissions(enforcer_id) == ["confirm_case"]
    assert granted.get_json()["access"]["effective_permissions"]["confirm_case"] is True

    revoked = admin_client.put(
        f"/api/users/{enforcer_id}/access", json={"revokes": ["confirm_case"]}
    )
    assert revoked.status_code == 200
    access = revoked.get_json()["access"]
    assert test_db.get_user_permissions(enforcer_id) == []
    assert access["role_defaults"]["confirm_case"] is True
    assert access["effective_permissions"]["confirm_case"] is True
    assert test_db.list_active_policy_permission_assignments(enforcer_id) == []
    assert actor_id != enforcer_id


def test_admin_role_alone_does_not_approve_policy_but_explicit_grant_does(admin_client, test_db):
    first_admin = test_db.get_user_by_username("admin")
    second_admin_id = test_db.create_user(
        "second_admin",
        bcrypt.hashpw(b"second-admin-pass", bcrypt.gensalt()).decode("utf-8"),
        role="admin",
    )
    assert test_db.can_approve_policy(first_admin["id"]) is False
    grant = admin_client.put(
        f"/api/users/{second_admin_id}/access",
        json={"grants": [{"permission": "approve_policy"}]},
    )
    assert grant.status_code == 200
    assert grant.get_json()["access"]["effective_permissions"]["approve_policy"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"grants": [{"permission": "confirm_case"}], "revokes": ["not_a_permission"]},
        {"grants": [{"permission": "confirm_case"}], "actor_user_id": 1},
        {"grants": [{"permission": "approve_policy", "reason": 7}]},
        {"grants": [{"permission": "approve_policy"}], "revokes": ["approve_policy"]},
    ],
)
def test_malformed_access_updates_are_rejected_without_partial_mutation(
    payload, admin_client, test_db
):
    target_id = test_db.create_user(
        "access_target",
        bcrypt.hashpw(b"viewer-pass", bcrypt.gensalt()).decode("utf-8"),
        role="viewer",
    )
    response = admin_client.put(f"/api/users/{target_id}/access", json=payload)
    assert response.status_code == 400
    assert test_db.get_user_permissions(target_id) == []


def test_self_grant_non_admin_and_inactive_target_rules(client, test_db):
    admin_id = test_db.get_user_by_username("admin")["id"]
    client.post("/login", data={"username": "admin", "password": "account-access-test-only-2026"})
    self_grant = client.put(
        f"/api/users/{admin_id}/access",
        json={"grants": [{"permission": "approve_policy"}]},
    )
    assert self_grant.status_code == 403
    assert test_db.can_approve_policy(admin_id) is False

    inactive_id = test_db.create_user(
        "inactive_target",
        bcrypt.hashpw(b"inactive-pass", bcrypt.gensalt()).decode("utf-8"),
        role="viewer",
    )
    test_db.update_user(inactive_id, is_active=False)
    response = client.put(
        f"/api/users/{inactive_id}/access",
        json={"grants": [{"permission": "confirm_case"}]},
    )
    assert response.status_code == 200
    access = response.get_json()["access"]
    assert access["is_active"] is False
    assert access["effective_permissions"]["confirm_case"] is False
    assert test_db.can_confirm_case(inactive_id) is False


def test_database_permission_helpers_enforce_active_admin_and_known_names(test_db):
    enforcer_id = test_db.create_user(
        "helper_enforcer",
        bcrypt.hashpw(b"enforcer-pass", bcrypt.gensalt()).decode("utf-8"),
        role="enforcer",
    )
    viewer_id = test_db.create_user(
        "helper_viewer",
        bcrypt.hashpw(b"viewer-pass", bcrypt.gensalt()).decode("utf-8"),
        role="viewer",
    )
    with pytest.raises(PermissionError):
        test_db.assign_policy_permission(viewer_id, "confirm_case", granted_by=enforcer_id)
    with pytest.raises(PermissionError):
        test_db.revoke_policy_permission(viewer_id, "confirm_case", revoked_by=enforcer_id)
    with pytest.raises(ValueError, match="Unknown policy permission"):
        test_db.assign_policy_permission(viewer_id, "manage_users", granted_by=1)

    inactive_admin_id = test_db.create_user(
        "inactive_admin",
        bcrypt.hashpw(b"admin-pass", bcrypt.gensalt()).decode("utf-8"),
        role="admin",
    )
    test_db.update_user(inactive_admin_id, is_active=False)
    with pytest.raises(PermissionError):
        test_db.assign_policy_permission(viewer_id, "confirm_case", granted_by=inactive_admin_id)


def test_grant_and_revoke_apply_to_an_existing_authenticated_session(admin_client, test_db):
    viewer_id = test_db.create_user(
        "session_viewer",
        bcrypt.hashpw(b"viewer-pass", bcrypt.gensalt()).decode("utf-8"),
        role="viewer",
    )
    video_id = test_db.insert_video("access-session.mp4", "/tmp/access-session.mp4", status="ready")
    permitted_case_id = test_db.insert_violation(
        video_id, 1, "Illegal Parking", 0.9, 1, 1.0, status="confirmed"
    )
    revoked_case_id = test_db.insert_violation(
        video_id, 2, "Illegal Parking", 0.9, 2, 2.0, status="confirmed"
    )
    import app as flask_app

    viewer_session = flask_app.app.test_client()
    viewer_session.post("/login", data={"username": "session_viewer", "password": "viewer-pass"})
    assert viewer_session.post(f"/api/cases/{permitted_case_id}/confirm-case").status_code == 403
    path = f"/api/users/{viewer_id}/access"
    granted = admin_client.put(
        path, json={"grants": [{"permission": "confirm_case"}]}
    )
    assert granted.status_code == 200
    assert viewer_session.get("/api/violations").get_json()["case_actions"]["confirm_case"] is True
    # The same already-authenticated session immediately receives the grant.
    assert viewer_session.post(f"/api/cases/{permitted_case_id}/confirm-case").status_code == 200

    revoked = admin_client.put(path, json={"revokes": ["confirm_case"]})
    assert revoked.status_code == 200
    assert viewer_session.get("/api/violations").get_json()["case_actions"]["confirm_case"] is False
    # A fresh request re-reads persisted permissions; the old cookie cannot retain access.
    denied = viewer_session.post(f"/api/cases/{revoked_case_id}/confirm-case")
    assert denied.status_code == 403
