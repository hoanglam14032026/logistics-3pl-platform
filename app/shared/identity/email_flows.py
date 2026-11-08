"""
Thư mời đặt mật khẩu, quên/đặt lại mật khẩu qua email (Giai đoạn 5).

Token: ngẫu nhiên 256 bit, CSDL chỉ lưu SHA-256 (`sys_user_tokens`), có hạn (`INVITE_TOKEN_TTL_HOURS`,
`PASSWORD_RESET_TOKEN_TTL_MIN`), **dùng một lần**; cấp token mới cùng mục đích → token cũ chưa dùng bị vô hiệu.
Đường dẫn đặt token sau `#` (fragment): trình duyệt không gửi lên máy chủ – không lọt vào log máy chủ, header Referer.

Quên mật khẩu luôn trả cùng một kết quả (không lộ tài khoản/email có tồn tại không); đặt lại xong: thu hồi mọi phiên,
mở khóa tạm. 2FA không đổi – lần đăng nhập sau vẫn cần mã TOTP.
"""

import logging
import secrets
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.core.errors import AppError, ErrorCode
from app.core.security import hash_refresh_token
from app.core.timeutil import now_utc
from app.shared.audit import service as audit
from app.shared.identity import service
from app.shared.identity.models import SysUser, SysUserToken, TokenPurpose
from app.shared.notifications import mail

logger = logging.getLogger("app.identity.email")

INVALID_LINK = "Đường dẫn không hợp lệ, đã dùng hoặc đã hết hạn"


def _issue(db: Session, user: SysUser, purpose: TokenPurpose, ttl: timedelta, actor_id: int | None) -> str:
    now = now_utc()
    db.execute(
        update(SysUserToken)
        .where(
            SysUserToken.user_id == user.id,
            SysUserToken.purpose == purpose.value,
            SysUserToken.used_at.is_(None),
            SysUserToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    raw = secrets.token_urlsafe(32)
    db.add(
        SysUserToken(
            user_id=user.id,
            purpose=purpose.value,
            token_hash=hash_refresh_token(raw),
            expires_at=now + ttl,
            created_by=actor_id,
        )
    )
    return raw


def _consume(db: Session, raw: str, purpose: TokenPurpose) -> SysUser:
    record = db.scalar(select(SysUserToken).where(SysUserToken.token_hash == hash_refresh_token(raw)).with_for_update())
    now = now_utc()
    if (
        record is None
        or record.purpose != purpose.value
        or record.used_at is not None
        or record.revoked_at is not None
        or record.expires_at <= now
        or not record.user.is_active
    ):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            INVALID_LINK,
            [{"field": "body.token", "issue": "invalid", "message": INVALID_LINK}],
        )
    record.used_at = now
    return record.user


def _link(path: str, raw: str) -> str:
    return f"{get_settings().public_base_url}/{path}#token={raw}"


def _send(user: SysUser, subject: str, body: str) -> bool:
    try:
        mail.send(mail.Mail(to=user.email, subject=subject, body=body))
        return True
    except mail.MailError as exc:
        logger.error("Không gửi được thư tới user_id=%s: %s", user.id, exc)
        return False


# --- Thư mời -----------------------------------------------------------------------------------------------------


def send_invitation(db: Session, user: SysUser, actor_id: int | None) -> bool:
    """Cấp token mời, commit, rồi gửi thư. Trả False nếu gửi lỗi (tài khoản vẫn tạo; gửi lại qua `:resend-invite`)."""
    settings = get_settings()
    raw = _issue(db, user, TokenPurpose.INVITE, timedelta(hours=settings.invite_token_ttl_hours), actor_id)
    audit.record(db, "user.invite_sent", "user", user.id, actor_id, after={"email": user.email})
    db.commit()
    body = (
        f"Xin chào {user.full_name},\n\n"
        f"Tài khoản cổng khách hàng của bạn đã được tạo (tên đăng nhập: {user.username}).\n"
        f"Đặt mật khẩu tại đường dẫn sau (hiệu lực {settings.invite_token_ttl_hours} giờ, dùng một lần):\n\n"
        f"{_link('accept-invite', raw)}\n\n"
        "Lần đăng nhập đầu tiên bạn sẽ thiết lập xác thực 2 lớp bằng ứng dụng xác thực "
        "(Google Authenticator, Microsoft Authenticator…).\n"
        "Nếu bạn không chờ thư này, hãy bỏ qua.\n"
    )
    sent = _send(user, "Mời đặt mật khẩu tài khoản cổng khách hàng", body)
    if not sent:
        with SessionLocal() as log_db:
            audit.record(log_db, "user.invite_failed", "user", user.id, actor_id)
            log_db.commit()
    return sent


def invitation_pending(db: Session, user: SysUser) -> bool:
    """Chưa nhận lời mời (chưa từng dùng token mời) – chỉ khi đó mới gửi lại thư mời."""
    used = db.scalar(
        select(SysUserToken.id).where(
            SysUserToken.user_id == user.id,
            SysUserToken.purpose == TokenPurpose.INVITE.value,
            SysUserToken.used_at.is_not(None),
        )
    )
    return used is None


def accept_invitation(db: Session, raw: str, new_password: str) -> SysUser:
    user = _consume(db, raw, TokenPurpose.INVITE)
    _check_password(user, new_password)
    service._set_password(db, user, new_password, user.id)
    user.failed_login_count = 0
    user.locked_until = None
    audit.record(db, "user.invite_accepted", "user", user.id, user.id)
    db.commit()
    return user


# --- Quên / đặt lại mật khẩu ---------------------------------------------------------------------------------------


def check_forgot_rate(identifier: str, ip: str | None) -> None:
    settings = get_settings()
    window = settings.auth_login_rate_window_sec
    hits = [
        rate_limit.hit(engine, "pwd_forgot_ip", ip or "unknown", settings.auth_login_rate_limit_per_ip, window),
        rate_limit.hit(
            engine, "pwd_forgot_id", identifier.strip().lower(), settings.auth_login_rate_limit_per_username, window
        ),
    ]
    exceeded = [h for h in hits if h.exceeded]
    if exceeded:
        raise rate_limit.too_many_requests(max(h.retry_after for h in exceeded))


def request_password_reset(db: Session, identifier: str) -> None:
    """Tên đăng nhập hoặc email. Không tìm thấy / không có email / bị vô hiệu hóa: im lặng (không lộ thông tin)."""
    settings = get_settings()
    key = identifier.strip().lower()
    user = db.scalar(select(SysUser).where(or_(SysUser.username == key, SysUser.email == key)))
    if user is None or not user.is_active or not user.email:
        return
    raw = _issue(db, user, TokenPurpose.PASSWORD_RESET, timedelta(minutes=settings.password_reset_token_ttl_min), None)
    audit.record(db, "user.password_reset_requested", "user", user.id, None)
    db.commit()
    body = (
        f"Xin chào {user.full_name},\n\n"
        "Có yêu cầu đặt lại mật khẩu cho tài khoản của bạn. Đặt mật khẩu mới tại đường dẫn sau "
        f"(hiệu lực {settings.password_reset_token_ttl_min} phút, dùng một lần):\n\n"
        f"{_link('reset-password', raw)}\n\n"
        "Nếu không phải bạn yêu cầu, hãy bỏ qua thư này – mật khẩu hiện tại vẫn giữ nguyên.\n"
    )
    _send(user, "Đặt lại mật khẩu", body)


def reset_password(db: Session, raw: str, new_password: str) -> SysUser:
    user = _consume(db, raw, TokenPurpose.PASSWORD_RESET)
    _check_password(user, new_password)
    service._set_password(db, user, new_password, user.id)  # thu hồi mọi phiên
    user.failed_login_count = 0
    user.locked_until = None
    audit.record(db, "user.password_reset", "user", user.id, user.id, after={"method": "email_link"})
    db.commit()
    return user


def _check_password(user: SysUser, password: str) -> None:
    if password.lower() == user.username:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            "Mật khẩu không được trùng tên đăng nhập",
            [{"field": "body.new_password", "issue": "invalid", "message": "Mật khẩu không được trùng tên đăng nhập"}],
        )
