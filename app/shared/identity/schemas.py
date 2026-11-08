"""Schema API của xác thực và quản lý người dùng. Không có trường `password_hash`, `totp_secret` ở đầu ra."""

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

from app.core.rbac import Role
from app.core.timeutil import UtcDatetime

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128  # chặn chuỗi rất dài làm chậm Argon2 (DoS)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _check_email(value: str) -> str:
    if not _EMAIL_RE.match(value):
        raise ValueError("Email không hợp lệ")
    return value


def _normalize(value: object) -> object:
    # Chuẩn hóa TRƯỚC khi kiểm ràng buộc: StringConstraints kiểm `pattern` trước `to_lower`.
    return value.strip().lower() if isinstance(value, str) else value


Username = Annotated[
    str, BeforeValidator(_normalize), StringConstraints(min_length=3, max_length=64, pattern=r"^[a-z0-9._-]+$")
]
Email = Annotated[str, BeforeValidator(_normalize), StringConstraints(max_length=254), AfterValidator(_check_email)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Password = Annotated[str, StringConstraints(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]
RoleList = Annotated[list[Role], Field(min_length=1)]


def _unique_roles(roles: list[Role] | None) -> list[Role] | None:
    return sorted(set(roles), key=lambda r: r.value) if roles is not None else None


# --- Xác thực --------------------------------------------------------------------------------------------


class LoginRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"username": "nguyenvana", "password": "MatKhau@2026"}})

    username: Annotated[str, BeforeValidator(_normalize), StringConstraints(min_length=1, max_length=64)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class RefreshRequest(BaseModel):
    refresh_token: Annotated[str, StringConstraints(min_length=1, max_length=512)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Số giây access token còn hiệu lực")
    refresh_token: str
    refresh_expires_in: int = Field(description="Số giây refresh token còn hiệu lực")


class ChangePasswordRequest(BaseModel):
    current_password: Annotated[str, StringConstraints(min_length=1, max_length=PASSWORD_MAX_LENGTH)]
    new_password: Password

    @model_validator(mode="after")
    def _different(self):
        if self.current_password == self.new_password:
            raise ValueError("Mật khẩu mới phải khác mật khẩu hiện tại")
        return self


# --- Người dùng ------------------------------------------------------------------------------------------


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None
    full_name: str
    roles: list[str] = Field(validation_alias="role_codes")
    party_id: int | None
    is_customer_admin: bool
    is_active: bool
    is_2fa_enabled: bool
    locked_until: UtcDatetime | None
    last_login_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    row_version: int


class UserCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "nguyenvana",
                "email": "nguyenvana@example.vn",
                "full_name": "Nguyễn Văn A",
                "password": "MatKhau@2026",
                "roles": ["ops_staff"],
            }
        }
    )

    username: Username
    email: Email | None = None
    full_name: FullName
    password: Password
    roles: RoleList
    is_active: bool = True
    # Bắt buộc khi roles = ["customer"] (02 §9.2); tài khoản nội bộ không có.
    party_id: int | None = Field(None, gt=0)
    is_customer_admin: bool = False

    @model_validator(mode="after")
    def _validate(self):
        self.roles = _unique_roles(self.roles)
        if self.password.lower() == self.username:
            raise ValueError("Mật khẩu không được trùng tên đăng nhập")
        return self


class UserUpdate(BaseModel):
    """PATCH (JSON Merge Patch): chỉ trường được gửi mới đổi; `null` chỉ hợp lệ với `email`."""

    model_config = ConfigDict(extra="forbid")

    email: Email | None = None
    full_name: FullName | None = None
    roles: RoleList | None = None
    is_active: bool | None = None
    password: Password | None = Field(None, description="Đặt lại mật khẩu (thu hồi mọi phiên của người dùng)")
    is_customer_admin: bool | None = Field(None, description="Chỉ với tài khoản customer")

    @model_validator(mode="after")
    def _validate(self):
        for field in ("full_name", "roles", "is_active", "password", "is_customer_admin"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"Trường '{field}' không được null")
        # Chỉ gán khi client gửi roles: gán thuộc tính trong validator đánh dấu trường là "đã gửi" (model_fields_set).
        if self.roles is not None:
            self.roles = _unique_roles(self.roles)
        return self


class CustomerAccountCreate(BaseModel):
    """Tài khoản `customer` cho một khách hàng (`POST /api/v2/md/parties/{id}/users`): vai trò luôn là `customer`.

    Không có mật khẩu: hệ thống gửi thư mời kèm đường dẫn đặt mật khẩu tới `email` (bắt buộc)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "username": "minhan.lan",
                "email": "lan@minhan.vn",
                "full_name": "Nguyễn Thị Lan",
                "is_customer_admin": True,
            }
        },
    )

    username: Username
    email: Email
    full_name: FullName
    is_customer_admin: bool = False


class PortalAccountCreate(BaseModel):
    """Customer admin mời tài khoản mới cho khách hàng của mình (`POST /api/v2/portal/users`) – thư mời qua email.

    Không có `is_customer_admin`: chỉ `manager`/`admin` cấp quyền quản trị tài khoản khách hàng."""

    model_config = ConfigDict(extra="forbid")

    username: Username
    email: Email
    full_name: FullName


class InvitationQueued(BaseModel):
    """Thư mời gửi nền (Giai đoạn 6): kết quả gửi xem ở nhật ký (`user.mail_failed` nếu lỗi)."""

    message: str = "Thư mời đã được xếp vào hàng gửi"


# --- Xác thực 2 lớp, thư mời, đặt lại mật khẩu (Giai đoạn 5) -----------------------------------------------------

TotpCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{6}$")]
MfaToken = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
EmailToken = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class MfaChallengeOut(BaseModel):
    """Đúng mật khẩu, còn thiếu lớp 2. `mfa_required`: gọi /login/totp. `mfa_setup_required`: gọi /login/2fa-setup
    rồi /login/2fa-enable."""

    mfa_required: bool = False
    mfa_setup_required: bool = False
    mfa_token: str
    expires_in: int


class MfaTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mfa_token: MfaToken


class MfaCodeIn(MfaTokenIn):
    code: TotpCode


class CodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: TotpCode


class TotpDisableIn(CodeIn):
    password: Annotated[str, StringConstraints(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class TotpSetupOut(BaseModel):
    """Chỉ trả một lần lúc thiết lập: người dùng quét `otpauth_uri` (mã QR) hoặc nhập `secret` vào ứng dụng xác thực."""

    secret: str
    otpauth_uri: str


class SetPasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: EmailToken
    new_password: Password


class ForgotPasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=254)] = Field(
        description="Tên đăng nhập hoặc email"
    )


class ForgotPasswordOut(BaseModel):
    message: str = "Nếu tài khoản tồn tại và có email, đường dẫn đặt lại mật khẩu đã được gửi"
