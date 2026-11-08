"""
API xác thực `/api/v2/auth` và quản lý người dùng `/api/v2/users` (06 §1, §3, §4; bước 0.6).

Không cần access token: `login`, `refresh`, `logout` (xác thực bằng mật khẩu / refresh token).
"""

from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.core.versioning import etag, parse_if_match
from app.shared.identity import service
from app.shared.identity.dependencies import get_current_user
from app.shared.identity.models import SysUser
from app.shared.identity.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)

auth_router = APIRouter(prefix="/api/v2/auth", tags=["Auth"])
users_router = APIRouter(prefix="/api/v2/users", tags=["Users"])

_admin = require_roles(Role.ADMIN)


def _client(request: Request) -> service.ClientInfo:
    return service.ClientInfo(
        ip=request.client.host if request.client else None, user_agent=request.headers.get("user-agent")
    )


def _tokens(t: service.IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=t.access_token,
        expires_in=t.expires_in,
        refresh_token=t.refresh_token,
        refresh_expires_in=t.refresh_expires_in,
    )


def _no_store(response: Response) -> None:
    # Phản hồi có token: không cho proxy/trình duyệt lưu đệm.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


# --- /api/v2/auth -----------------------------------------------------------------------------------------


@auth_router.post("/login", response_model=TokenResponse, summary="Đăng nhập, nhận access token + refresh token")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):  # noqa: B008
    _no_store(response)
    return _tokens(service.login(db, body.username, body.password, _client(request)))


@auth_router.post("/refresh", response_model=TokenResponse, summary="Đổi refresh token lấy cặp token mới")
def refresh(body: RefreshRequest, request: Request, response: Response, db: Session = Depends(get_db)):  # noqa: B008
    _no_store(response)
    return _tokens(service.refresh(db, body.refresh_token, _client(request)))


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Thu hồi phiên (refresh token)")
def logout(body: RefreshRequest, db: Session = Depends(get_db)):  # noqa: B008
    service.logout(db, body.refresh_token)


@auth_router.get("/me", response_model=UserOut, summary="Người dùng hiện tại")
def me(user: SysUser = Depends(get_current_user)):  # noqa: B008
    return user


@auth_router.post(
    "/change-password", status_code=status.HTTP_204_NO_CONTENT, summary="Đổi mật khẩu (thu hồi mọi phiên)"
)
def change_password(
    body: ChangePasswordRequest,
    user: SysUser = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    service.change_password(db, user, body.current_password, body.new_password)


# --- /api/v2/users (admin) --------------------------------------------------------------------------------


@users_router.get("", response_model=Page[UserOut], summary="Danh sách người dùng")
def list_users(
    params: PageParams = Depends(),  # noqa: B008
    q: str | None = Query(None, min_length=1, max_length=100, description="Tìm theo tên đăng nhập, email, họ tên"),
    role: Role | None = None,
    status_: Literal["active", "inactive", "all"] = Query("active", alias="status"),
    _: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_users(db, params, q=q, role=role, status=status_)
    return Page[UserOut](
        items=[UserOut.model_validate(u) for u in items], total=total, page=params.page, page_size=params.page_size
    )


@users_router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED, summary="Tạo người dùng")
def create_user(
    body: UserCreate,
    response: Response,
    actor: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    user = service.create_user(db, body, actor.id)
    response.headers["Location"] = f"/api/v2/users/{user.id}"
    response.headers["ETag"] = etag(user.row_version)
    return user


@users_router.get("/{user_id:int}", response_model=UserOut, summary="Chi tiết người dùng")
def get_user(
    user_id: int,
    response: Response,
    _: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    user = service.get_user(db, user_id)
    response.headers["ETag"] = etag(user.row_version)
    return user


@users_router.patch("/{user_id:int}", response_model=UserOut, summary="Cập nhật người dùng (JSON Merge Patch)")
def update_user(
    user_id: int,
    body: UserUpdate,
    response: Response,
    if_match: str | None = Header(None, description="ETag nhận từ GET; lệch → 409 VERSION_CONFLICT"),
    actor: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    user = service.update_user(db, service.get_user(db, user_id), body, actor, parse_if_match(if_match))
    response.headers["ETag"] = etag(user.row_version)
    return user


@users_router.post("/{user_id:int}:unlock", response_model=UserOut, summary="Mở khóa tạm do đăng nhập sai")
def unlock_user(
    user_id: int,
    response: Response,
    actor: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    user = service.unlock_user(db, service.get_user(db, user_id), actor)
    response.headers["ETag"] = etag(user.row_version)
    return user
