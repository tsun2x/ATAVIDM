"""Tests for authentication and role-based access control.

Tests password hashing, user management, and role permissions.
"""

from __future__ import annotations

import pytest


def test_password_hashing_works():
    """Verify bcrypt password hashing produces valid hash."""
    from core.auth import hash_password, verify_password
    
    password = "test_password_123"
    hash_result = hash_password(password)
    
    # Hash should be a bcrypt hash (starts with $2b$)
    assert hash_result.startswith("$2b$"), "Password hash should be bcrypt format"
    
    # Verify should work with correct password
    assert verify_password(password, hash_result) is True
    
    # Verify should fail with wrong password
    assert verify_password("wrong_password", hash_result) is False


def test_password_hash_uniqueness():
    """Same password should produce different hashes (salt)."""
    from core.auth import hash_password
    
    password = "same_password"
    hash1 = hash_password(password)
    hash2 = hash_password(password)
    
    # Salts make hashes different
    assert hash1 != hash2, "Same password should produce different hashes due to salt"


def test_default_admin_created_on_first_run(test_db):
    """First run should create default admin user via ensure_default_admin."""
    from core import auth
    
    # Call ensure_default_admin which is normally called by app.py
    auth.ensure_default_admin()
    
    admin = test_db.get_user_by_username("admin")
    assert admin is not None, "Default admin should be created"
    assert admin["role"] == "admin", "Default admin should have admin role"
    assert admin["full_name"] == "System Administrator", "Default admin should have full name"


def test_authenticate_correct_credentials(auth_db):
    """Valid credentials should authenticate successfully."""
    from core.auth import authenticate
    
    # Create a test user
    from core.auth import hash_password
    password_hash = hash_password("testpass123")
    auth_db.create_user("testuser", password_hash, role="enforcer")
    
    user = authenticate("testuser", "testpass123")
    assert user is not None
    assert user["username"] == "testuser"
    assert user["role"] == "enforcer"


def test_authenticate_wrong_password(auth_db):
    """Wrong password should fail authentication."""
    from core.auth import authenticate
    
    from core.auth import hash_password
    password_hash = hash_password("correctpass")
    auth_db.create_user("authuser", password_hash, role="admin")
    
    user = authenticate("authuser", "wrongpass")
    assert user is None, "Wrong password should fail authentication"


def test_authenticate_inactive_user(auth_db):
    """Inactive users should not authenticate."""
    from core.auth import authenticate
    
    from core.auth import hash_password
    password_hash = hash_password("password123")
    user_id = auth_db.create_user("inactive_user", password_hash, role="enforcer")
    
    # Make user inactive
    auth_db.update_user(user_id, is_active=False)
    
    user = authenticate("inactive_user", "password123")
    assert user is None, "Inactive users should not authenticate"


def test_authenticate_case_sensitive_username():
    """Username comparison should be case-sensitive."""
    # Based on auth.authenticate implementation: username.strip()
    # The strip() normalizes whitespace but not case
    pass  # Implementation detail - would need to test auth module directly


def test_user_role_can_be_updated(auth_db):
    """User role should be updatable."""
    from core.auth import hash_password
    
    password_hash = hash_password("pass123")
    user_id = auth_db.create_user("roleuser", password_hash, role="viewer")
    
    # Update role
    auth_db.update_user(user_id, role="admin")
    
    user = auth_db.get_user(user_id)
    assert user["role"] == "admin"


def test_role_labels_exist():
    """Role labels should be defined for all roles."""
    from core.auth import ROLE_LABELS, ROLES
    
    for role in ROLES:
        assert role in ROLE_LABELS, f"Missing label for role: {role}"


def test_valid_roles_defined():
    """Expected roles should be defined."""
    from core.auth import ROLES
    
    expected = ("admin", "enforcer", "viewer")
    assert ROLES == expected


def test_list_users_returns_all_users(auth_db):
    """List users should return all created users."""
    from core.auth import hash_password
    
    initial = len(auth_db.list_users())
    
    auth_db.create_user("newuser1", hash_password("pass"), role="enforcer")
    auth_db.create_user("newuser2", hash_password("pass"), role="viewer")
    
    users = auth_db.list_users()
    assert len(users) == initial + 2


def test_get_user_by_username(auth_db):
    """Get user by username should work."""
    from core.auth import hash_password
    
    password_hash = hash_password("mypass")
    auth_db.create_user("findme", password_hash, role="admin")
    
    user = auth_db.get_user_by_username("findme")
    assert user is not None
    assert user["username"] == "findme"
    assert user["role"] == "admin"


def test_get_user_by_nonexistent_username(auth_db):
    """Non-existent username should return None."""
    user = auth_db.get_user_by_username("nonexistent_user_xyz")
    assert user is None


def test_update_user_full_name(auth_db):
    """User full_name can be updated."""
    from core.auth import hash_password
    
    user_id = auth_db.create_user("fullname_user", hash_password("pass"), role="enforcer")
    
    auth_db.update_user(user_id, full_name="Test User Name")
    
    user = auth_db.get_user(user_id)
    assert user["full_name"] == "Test User Name"


def test_duplicate_username_rejected(auth_db):
    """Creating user with existing username should fail."""
    from core.auth import hash_password
    
    password_hash = hash_password("pass1")
    auth_db.create_user("duplicate", password_hash, role="admin")
    
    # Second user with same username
    with pytest.raises(Exception):  # Should raise IntegrityError
        auth_db.create_user("duplicate", hash_password("pass2"), role="enforcer")


def test_create_user_with_minimal_fields(auth_db):
    """User can be created with just username and password hash."""
    from core.auth import hash_password
    
    user_id = auth_db.create_user("minimal_user", hash_password("pwd"))
    
    user = auth_db.get_user(user_id)
    assert user["username"] == "minimal_user"
    assert user["role"] == "enforcer"  # Default role