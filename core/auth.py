"""Authentication and role-based access control (manuscript Ch3 user roles).

Roles:
- admin    -> System Administrator (full access, user management, settings)
- enforcer -> Traffic Enforcement Officer (review queue, violations, reports)
- viewer   -> Guest Viewer (read-only dashboards and analytics)

Passwords are hashed with bcrypt; sessions are Flask server-side cookies.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

import bcrypt
from flask import flash, redirect, request, session, url_for

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
DEFAULT_ADMIN_PASSWORD = "admin123"  # dev bootstrap; must be changed after first login


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def ensure_default_admin() -> None:
    """Bootstrap a System Administrator account on first run."""
    existing = db.get_user_by_username(DEFAULT_ADMIN_USERNAME)
    if existing is None:
        db.create_user(
            DEFAULT_ADMIN_USERNAME,
            hash_password(DEFAULT_ADMIN_PASSWORD),
            role="admin",
            full_name="System Administrator",
        )
    elif not str(existing.get("password_hash", "")).startswith("$2"):
        # Legacy placeholder hash from the prototype seed — reset to bootstrap.
        db.update_user(existing["id"], password_hash=hash_password(DEFAULT_ADMIN_PASSWORD))


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    user = db.get_user_by_username(username.strip())
    if user is None or not user.get("is_active", 1):
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def login_user(user: dict[str, Any]) -> None:
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["full_name"] = user.get("full_name") or user["username"]


def logout_user() -> None:
    session.clear()


def current_user() -> dict[str, Any] | None:
    if "user_id" not in session:
        return None
    return {
        "id": session["user_id"],
        "username": session.get("username"),
        "role": session.get("role"),
        "full_name": session.get("full_name"),
        "role_label": ROLE_LABELS.get(session.get("role", ""), session.get("role")),
    }


def login_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles: str) -> Callable:
    """Restrict a view to the given roles (admin always allowed)."""

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any):
            if "user_id" not in session:
                return redirect(url_for("login", next=request.path))
            role = session.get("role")
            if role != "admin" and role not in roles:
                flash("You do not have permission to access that page.", "danger")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator
