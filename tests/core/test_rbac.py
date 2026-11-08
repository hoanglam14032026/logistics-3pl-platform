"""Test đơn vị app/core/rbac.py (06 §4, 00 §4)."""

import pytest

from app.core.errors import AppError
from app.core.rbac import ALL_ROLES, Role, ensure_any_role, require_roles


def test_eight_roles_from_spec():
    assert ALL_ROLES == {
        "ops_staff",
        "manager",
        "business_manager",
        "learner",
        "instructor",
        "content_editor",
        "customer",
        "admin",
    }


def test_ensure_any_role():
    ensure_any_role(["learner", "manager"], [Role.MANAGER])
    with pytest.raises(AppError) as exc:
        ensure_any_role(["learner"], [Role.ADMIN, Role.MANAGER])
    assert (exc.value.status_code, exc.value.code) == (403, "FORBIDDEN")
    with pytest.raises(AppError):
        ensure_any_role([], [Role.ADMIN])


def test_require_roles_needs_at_least_one_role():
    with pytest.raises(ValueError):
        require_roles()
