"""
Xác thực 2 lớp TOTP (06 §3, 02 §9.2 – Giai đoạn 5).

Đăng nhập (đúng mật khẩu → `MfaChallenge`):
- `verify`: đã bật 2FA → `login_totp(mfa_token, code)` → token (`amr` = pwd + otp).
- `setup`: vai trò bắt buộc 2FA (mặc định `customer`) nhưng chưa bật → `login_setup(mfa_token)` (lấy secret, mã QR) →
  `login_enable(mfa_token, code)` → bật 2FA + token.

Người đã đăng nhập: `setup` → `enable` (thu hồi các phiên cũ chưa qua 2 lớp, trả token mới) → `disable` (cần mật khẩu +
mã; tài khoản bắt buộc 2FA không tắt được). `admin` xóa 2FA của người dùng mất thiết bị: `admin_reset` (thu hồi phiên;
lần đăng nhập sau thiết lập lại).

`totp_secret` lưu bản mã Fernet (`app.core.secretbox`, Giai đoạn 6): chỉ giải mã lúc kiểm mã.

Mã sai được tính như đăng nhập sai (khóa tạm sau `AUTH_MAX_FAILED_LOGINS` lần) – 10⁶ khả năng không dò được. Mã đã
dùng không dùng lại được (`totp_last_step`).
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import secretbox, totp
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.rbac import forbidden
from app.core.security import MFA_SETUP, MFA_VERIFY, decode_mfa_token, unauthenticated, verify_password
from app.core.timeutil import now_utc
from app.shared.identity import service
from app.shared.identity.models import SysUser

logger = logging.getLogger("app.identity.2fa")

INVALID_CODE = "Mã xác thực không đúng hoặc đã dùng"


@dataclass(frozen=True)
class TotpSetup:
    secret: str
    otpauth_uri: str


def _code_error() -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR,
        422,
        INVALID_CODE,
        [{"field": "body.code", "issue": "invalid", "message": INVALID_CODE}],
    )


def _new_secret(db: Session, user: SysUser, settings: Settings) -> TotpSetup:
    secret = totp.generate_secret()
    user.totp_secret = secretbox.encrypt(secret)
    user.totp_last_step = None
    db.commit()
    return TotpSetup(secret, totp.provisioning_uri(secret, user.username, settings.totp_issuer))


def _check_code(user: SysUser, code: str) -> int | None:
    """Chu kỳ của mã nếu đúng (chưa dùng), ngược lại None. Không giải mã được secret (sai khóa) → 500 lỗi hệ thống,
    không coi là mã sai (không tính vào khóa tạm tài khoản)."""
    if not user.totp_secret:
        return None
    try:
        secret = secretbox.decrypt(user.totp_secret)
    except secretbox.SecretDecryptError as exc:
        logger.error("Không giải mã được totp_secret của user_id=%s – kiểm tra TOTP_ENCRYPTION_KEY", user.id)
        raise AppError(
            ErrorCode.INTERNAL_ERROR, 500, "Không kiểm tra được mã xác thực; liên hệ quản trị hệ thống"
        ) from exc
    return totp.verify(secret, code, now_utc(), user.totp_last_step)


def _user_from_mfa_token(db: Session, mfa_token: str, purpose: str) -> SysUser:
    claims = decode_mfa_token(mfa_token, purpose)
    user = db.scalar(select(SysUser).where(SysUser.id == int(claims["sub"])).with_for_update())
    # Đổi mật khẩu / vô hiệu hóa sau bước 1 → mfa_token mất hiệu lực
    if user is None or not user.is_active or claims["pwd_at"] != service.password_stamp(user):
        raise unauthenticated()
    if user.is_locked(now_utc()):
        raise unauthenticated(service.ACCOUNT_LOCKED)
    return user


def _verify_or_count(db: Session, user: SysUser, code: str, settings: Settings) -> int:
    step = _check_code(user, code)
    if step is None:
        just_locked = service._count_failure(db, user, settings)
        service._login_failed(db, user, user.username, "invalid_totp")
        raise unauthenticated(service.ACCOUNT_LOCKED if just_locked else INVALID_CODE)
    return step


# --- Bước 2 của đăng nhập ------------------------------------------------------------------------------------------


def login_totp(db: Session, mfa_token: str, code: str, client: service.ClientInfo) -> service.IssuedTokens:
    settings = get_settings()
    user = _user_from_mfa_token(db, mfa_token, MFA_VERIFY)
    if not user.is_2fa_enabled:
        raise unauthenticated()
    user.totp_last_step = _verify_or_count(db, user, code, settings)
    return service._complete_login(db, user, client, settings, mfa=True)


def login_setup(db: Session, mfa_token: str) -> TotpSetup:
    user = _user_from_mfa_token(db, mfa_token, MFA_SETUP)
    if user.is_2fa_enabled:
        raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, "Đã bật xác thực 2 lớp")
    return _new_secret(db, user, get_settings())


def login_enable(db: Session, mfa_token: str, code: str, client: service.ClientInfo) -> service.IssuedTokens:
    settings = get_settings()
    user = _user_from_mfa_token(db, mfa_token, MFA_SETUP)
    if user.is_2fa_enabled or not user.totp_secret:
        raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, "Chưa thiết lập (gọi 2fa-setup trước) hoặc đã bật")
    user.totp_last_step = _verify_or_count(db, user, code, settings)
    _turn_on(db, user)
    return service._complete_login(db, user, client, settings, mfa=True)


def _turn_on(db: Session, user: SysUser) -> None:
    user.is_2fa_enabled = True
    user.totp_enabled_at = now_utc()
    user.updated_at = now_utc()
    service._audit_user(db, "user.2fa_enable", user, user.id, {"is_2fa_enabled": False}, {"is_2fa_enabled": True})


# --- Người đã đăng nhập ----------------------------------------------------------------------------------------------


def setup(db: Session, user: SysUser) -> TotpSetup:
    if user.is_2fa_enabled:
        raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, "Đã bật xác thực 2 lớp")
    return _new_secret(db, user, get_settings())


def enable(db: Session, user: SysUser, code: str, client: service.ClientInfo) -> service.IssuedTokens:
    settings = get_settings()
    if user.is_2fa_enabled or not user.totp_secret:
        raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, "Chưa thiết lập (gọi /2fa/setup trước) hoặc đã bật")
    step = _check_code(user, code)
    if step is None:
        raise _code_error()
    user.totp_last_step = step
    _turn_on(db, user)
    # Phiên cũ chỉ có mật khẩu: thu hồi (từ giờ mọi phiên phải qua 2 lớp); trả cặp token mới cho phiên hiện tại
    service.revoke_all_user_tokens(db, user.id, "2fa_enabled")
    tokens, _ = service._issue_tokens(db, user, client, settings, mfa=True)
    db.commit()
    return tokens


def disable(db: Session, user: SysUser, password: str, code: str) -> None:
    settings = get_settings()
    if not user.is_2fa_enabled:
        raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, "Chưa bật xác thực 2 lớp")
    if set(user.role_codes) & settings.auth_2fa_required_roles:
        raise forbidden("Vai trò của tài khoản bắt buộc xác thực 2 lớp; không tắt được")
    if not verify_password(user.password_hash, password):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            "Mật khẩu không đúng",
            [{"field": "body.password", "issue": "invalid", "message": "Mật khẩu không đúng"}],
        )
    step = _check_code(user, code)
    if step is None:
        raise _code_error()
    _clear(user)
    service._audit_user(db, "user.2fa_disable", user, user.id, {"is_2fa_enabled": True}, {"is_2fa_enabled": False})
    db.commit()


def _clear(user: SysUser) -> None:
    user.is_2fa_enabled = False
    user.totp_secret = None
    user.totp_last_step = None
    user.totp_enabled_at = None
    user.updated_at = now_utc()


def admin_reset(db: Session, user: SysUser, actor: SysUser) -> SysUser:
    """Người dùng mất thiết bị: xóa 2FA, thu hồi mọi phiên. Tài khoản bắt buộc 2FA thiết lập lại ở lần đăng nhập sau."""
    was = user.is_2fa_enabled
    _clear(user)
    user.updated_by = actor.id
    service.revoke_all_user_tokens(db, user.id, "2fa_reset")
    service._audit_user(db, "user.2fa_reset", user, actor.id, {"is_2fa_enabled": was}, {"is_2fa_enabled": False})
    db.commit()
    db.refresh(user)
    return user
