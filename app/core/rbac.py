"""
Phân quyền theo vai trò (06 §4, 00 §4).

`require_roles(...)` kiểm tra vai trò ở router; phạm vi dữ liệu (row-level, BR-01) kiểm tra trong `services`.
"""

from collections.abc import Callable, Iterable
from enum import StrEnum

from fastapi import Depends

from app.core.errors import AppError, ErrorCode


class Role(StrEnum):
    OPS_STAFF = "ops_staff"
    MANAGER = "manager"
    BUSINESS_MANAGER = "business_manager"
    LEARNER = "learner"
    INSTRUCTOR = "instructor"
    CONTENT_EDITOR = "content_editor"
    CUSTOMER = "customer"
    ADMIN = "admin"


ALL_ROLES: frozenset[str] = frozenset(r.value for r in Role)
# Vai trò nội bộ (nhân viên, người học, người hướng dẫn…) – mọi vai trò trừ `customer` (06 §4: `customer` chỉ dùng
# cổng khách hàng, không đọc danh mục nội bộ).
INTERNAL_ROLES: tuple[Role, ...] = tuple(r for r in Role if r is not Role.CUSTOMER)


def forbidden(message: str | None = None) -> AppError:
    return AppError(ErrorCode.FORBIDDEN, 403, message)


def ensure_any_role(user_roles: Iterable[str], allowed: Iterable[str]) -> None:
    if not set(user_roles) & set(allowed):
        raise forbidden()


def require_roles(*roles: Role) -> Callable:
    """Dependency: người dùng hiện tại phải có ít nhất một trong các vai trò; trả về người dùng."""
    if not roles:
        raise ValueError("require_roles cần ít nhất một vai trò")
    # Import trong hàm: app.shared.identity phụ thuộc app.core – tránh import vòng.
    from app.shared.identity.dependencies import get_current_user

    def _dependency(user=Depends(get_current_user)):  # noqa: B008
        ensure_any_role(user.role_codes, roles)
        return user

    return _dependency
