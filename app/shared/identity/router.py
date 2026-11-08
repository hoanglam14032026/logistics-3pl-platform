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
from app.core.security import MFA_SETUP, MFA_VERIFY
from app.core.versioning import etag, parse_if_match
from app.shared.identity import email_flows, service, twofactor
from app.shared.identity.dependencies import get_current_user
from app.shared.identity.models import SysUser
from app.shared.identity.schemas import (
    ChangePasswordRequest,
    CodeIn,
    ForgotPasswordIn,
    ForgotPasswordOut,
    LoginRequest,
    MfaChallengeOut,
    MfaCodeIn,
    MfaTokenIn,
    RefreshRequest,
    SetPasswordIn,
    TokenResponse,
    TotpDisableIn,
    TotpSetupOut,
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


@auth_router.post(
    "/login",
    response_model=TokenResponse | MfaChallengeOut,
    summary="Đăng nhập (bước 1): token, hoặc yêu cầu mã xác thực 2 lớp",
    description=(
        "Giới hạn tần suất theo IP và tên đăng nhập; vượt ngưỡng → 429 RATE_LIMITED + Retry-After. "
        "Đã bật 2FA → `mfa_required` + `mfa_token` (gọi /login/totp). Vai trò bắt buộc 2FA mà chưa bật → "
        "`mfa_setup_required` + `mfa_token` (gọi /login/2fa-setup, /login/2fa-enable)."
    ),
)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):  # noqa: B008
    _no_store(response)
    client = _client(request)
    service.check_login_rate(body.username, client.ip)  # 429 trước khi băm mật khẩu
    result = service.login(db, body.username, body.password, client)
    if isinstance(result, service.MfaChallenge):
        return MfaChallengeOut(
            mfa_required=result.kind == MFA_VERIFY,
            mfa_setup_required=result.kind == MFA_SETUP,
            mfa_token=result.mfa_token,
            expires_in=result.expires_in,
        )
    return _tokens(result)


@auth_router.post("/login/totp", response_model=TokenResponse, summary="Đăng nhập (bước 2): mã TOTP")
def login_totp(body: MfaCodeIn, request: Request, response: Response, db: Session = Depends(get_db)):  # noqa: B008
    _no_store(response)
    return _tokens(twofactor.login_totp(db, body.mfa_token, body.code, _client(request)))


@auth_router.post(
    "/login/2fa-setup", response_model=TotpSetupOut, summary="Đăng nhập: thiết lập 2FA bắt buộc (lấy secret, mã QR)"
)
def login_2fa_setup(body: MfaTokenIn, response: Response, db: Session = Depends(get_db)):  # noqa: B008
    _no_store(response)
    result = twofactor.login_setup(db, body.mfa_token)
    return TotpSetupOut(secret=result.secret, otpauth_uri=result.otpauth_uri)


@auth_router.post("/login/2fa-enable", response_model=TokenResponse, summary="Đăng nhập: bật 2FA bằng mã đầu tiên")
def login_2fa_enable(body: MfaCodeIn, request: Request, response: Response, db: Session = Depends(get_db)):  # noqa: B008
    _no_store(response)
    return _tokens(twofactor.login_enable(db, body.mfa_token, body.code, _client(request)))


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


# --- Xác thực 2 lớp của người đã đăng nhập ----------------------------------------------------------------------


@auth_router.post("/2fa/setup", response_model=TotpSetupOut, summary="Thiết lập 2FA (lấy secret, mã QR)")
def setup_2fa(response: Response, user: SysUser = Depends(get_current_user), db: Session = Depends(get_db)):  # noqa: B008
    _no_store(response)
    result = twofactor.setup(db, user)
    return TotpSetupOut(secret=result.secret, otpauth_uri=result.otpauth_uri)


@auth_router.post(
    "/2fa/enable", response_model=TokenResponse, summary="Bật 2FA bằng mã đầu tiên (thu hồi phiên cũ, trả token mới)"
)
def enable_2fa(
    body: CodeIn,
    request: Request,
    response: Response,
    user: SysUser = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    _no_store(response)
    return _tokens(twofactor.enable(db, user, body.code, _client(request)))


@auth_router.post("/2fa/disable", status_code=status.HTTP_204_NO_CONTENT, summary="Tắt 2FA (cần mật khẩu + mã)")
def disable_2fa(
    body: TotpDisableIn,
    user: SysUser = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    twofactor.disable(db, user, body.password, body.code)


# --- Thư mời, quên / đặt lại mật khẩu (không cần đăng nhập) -----------------------------------------------------


@auth_router.post(
    "/invitations/accept", status_code=status.HTTP_204_NO_CONTENT, summary="Nhận lời mời: đặt mật khẩu lần đầu"
)
def accept_invitation(body: SetPasswordIn, db: Session = Depends(get_db)):  # noqa: B008
    email_flows.accept_invitation(db, body.token, body.new_password)


@auth_router.post(
    "/password/forgot",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ForgotPasswordOut,
    summary="Quên mật khẩu: gửi đường dẫn đặt lại tới email của tài khoản",
    description="Luôn trả 202 cùng nội dung – không cho biết tài khoản/email có tồn tại không.",
)
def forgot_password(body: ForgotPasswordIn, request: Request, db: Session = Depends(get_db)):  # noqa: B008
    email_flows.check_forgot_rate(body.identifier, _client(request).ip)
    email_flows.request_password_reset(db, body.identifier)
    return ForgotPasswordOut()


@auth_router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT, summary="Đặt lại mật khẩu bằng đường dẫn")
def reset_password(body: SetPasswordIn, db: Session = Depends(get_db)):  # noqa: B008
    email_flows.reset_password(db, body.token, body.new_password)


@users_router.post("/{user_id:int}:reset-2fa", response_model=UserOut, summary="Xóa 2FA của người dùng mất thiết bị")
def reset_2fa(
    user_id: int,
    response: Response,
    actor: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    user = twofactor.admin_reset(db, service.get_user(db, user_id), actor)
    response.headers["ETag"] = etag(user.row_version)
    return user
