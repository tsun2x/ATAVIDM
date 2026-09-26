"""Authentication and role-based access control (manuscript Ch3 user roles).

Roles:
- admin    -> System Administrator (full access, user management, settings)
- enforcer -> Traffic Enforcement Officer (review queue, violations, reports)
- viewer   -> Guest Viewer (read-only dashboards and analytics)

Passwords are hashed with bcrypt; signed Flask cookies are checked against the
current database user on each authenticated request.
"""

from __future__ import annotations

from functools import wraps
import hashlib
import hmac
import os
from typing import Any, Callable

import bcrypt
from flask import current_app, flash, jsonify, redirect, request, session, url_for

from database import db

ROLES = ("admin", "enforcer", "viewer")

ROLE_LABELS = {
    "admin": "System Administrator",
    "enforcer": "Traffic Enforcement Officer",
    "viewer": "Guest Viewer",
}

# Read-only pages every authenticated role can open.
VIEWER_PAGES = ("dashboard", "analytics", "live_monitor", "violations")

DEFAULT_ADMIN_USERNAME = "admin"
LEGACY_ADMIN_PASSWORD = "admin123"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def ensure_default_admin() -> None:
    """Create or replace the legacy bootstrap account using an explicit secret."""
    existing = db.get_user_by_username(DEFAULT_ADMIN_USERNAME)
    bootstrap_password = os.environ.get("TAVIDM_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    is_legacy_default = bool(
        existing
        and str(existing.get("password_hash", "")).startswith("$2")
        and verify_password(LEGACY_ADMIN_PASSWORD, existing["password_hash"])
    )
    needs_bootstrap = existing is None or is_legacy_default or (
        existing and not str(existing.get("password_hash", "")).startswith("$2")
    )
    if needs_bootstrap and len(bootstrap_password) < 12:
        raise RuntimeError(
            "Set TAVIDM_BOOTSTRAP_ADMIN_PASSWORD to a unique password of at least 12 characters "
            "to create or secure the admin account."
        )
    if existing is None:
        db.create_user(
            DEFAULT_ADMIN_USERNAME,
            hash_password(bootstrap_password),
            role="admin",
            full_name="System Administrator",
        )
    elif needs_bootstrap:
        db.update_user(existing["id"], password_hash=hash_password(bootstrap_password))


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    user = db.get_user_by_username(username.strip())
    if user is None or not user.get("is_active", 1):
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def login_user(user: dict[str, Any]) -> None:
    session.clear()
    session["user_id"] = user["id"]
    session["auth_stamp"] = _auth_stamp(user.get("password_hash", ""))


def _auth_stamp(password_hash: str) -> str:
    key = str(current_app.secret_key).encode("utf-8")
    return hmac.new(key, password_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def logout_user() -> None:
    session.clear()


def current_user() -> dict[str, Any] | None:
    user_id = session.get("user_id")
    if user_id is None:
        return None
    user = db.get_user(int(user_id))
    if (
        user is None
        or not user.get("is_active", 1)
        or not hmac.compare_digest(session.get("auth_stamp", ""), _auth_stamp(user.get("password_hash", "")))
    ):
        session.clear()
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "full_name": user.get("full_name") or user["username"],
        "role_label": ROLE_LABELS.get(user.get("role", ""), user.get("role")),
    }


def _api_request() -> bool:
    return request.path == "/api" or request.path.startswith("/api/")


def _unauthorized_response():
    if _api_request():
        return jsonify({"success": False, "error": "Authentication required.", "code": "unauthenticated"}), 401
    return redirect(url_for("login", next=request.full_path.rstrip("?")))


def login_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if current_user() is None:
            return _unauthorized_response()
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles: str) -> Callable:
    """Restrict a view to the given roles (admin always allowed)."""

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any):
            user = current_user()
            if user is None:
                return _unauthorized_response()
            if user["role"] != "admin" and user["role"] not in roles:
                if _api_request():
                    return jsonify({"success": False, "error": "Permission denied.", "code": "forbidden"}), 403
                flash("You do not have permission to access that page.", "danger")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator
