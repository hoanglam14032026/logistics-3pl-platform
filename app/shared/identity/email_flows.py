"""
Thư mời đặt mật khẩu, quên/đặt lại mật khẩu qua email (Giai đoạn 5).

Token: ngẫu nhiên 256 bit, CSDL chỉ lưu SHA-256 (`sys_user_tokens`), có hạn (`INVITE_TOKEN_TTL_HOURS`,
`PASSWORD_RESET_TOKEN_TTL_MIN`), **dùng một lần**; cấp token mới cùng mục đích → token cũ chưa dùng bị vô hiệu.
Đường dẫn đặt token sau `#` (fragment): trình duyệt không gửi lên máy chủ – không lọt vào log máy chủ, header Referer.

Quên mật khẩu luôn trả cùng một kết quả (không lộ tài khoản/email có tồn tại không); đặt lại xong: thu hồi mọi phiên,
mở khóa tạm, gửi thư báo mật khẩu đã đổi. 2FA không đổi – lần đăng nhập sau vẫn cần mã TOTP.

Gửi thư là **tác vụ nền** (Giai đoạn 6–7, `app.core.jobs`): request ghi nhật ký, commit rồi xếp tác vụ
`deliver_invitation` / `deliver_password_reset` / `deliver_password_changed` – tham số chỉ là id. Tác vụ đọc CSDL,
**cấp token lúc gửi** (Redis không chứa token) rồi gửi. Có `JOB_QUEUE_URL`: hàng đợi rq bền vững, gửi lỗi thì thử lại
(mỗi lần thử cấp token mới, token chưa gửi được bị vô hiệu); không có: chạy nền trong tiến trình API, một lần.
Request không chờ SMTP; quên mật khẩu trả về cùng thời gian dù tài khoản có tồn tại hay không. Lần thử cuối vẫn lỗi:
log + nhật ký `user.mail_failed` (purpose, lỗi, số lần thử) – request không lỗi; gửi lại bằng `:resend-invite` /
quên mật khẩu lần nữa.
"""

import logging
import secrets
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core import jobs, rate_limit
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


TaskQueue = jobs.TaskQueue


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


def _invitation_mail(db: Session, user: SysUser, actor_id: int | None) -> mail.Mail | None:
    if not user.is_active or not invitation_pending(db, user):
        return None  # đã nhận lời mời / bị vô hiệu hóa trong lúc chờ gửi
    settings = get_settings()
    raw = _issue(db, user, TokenPurpose.INVITE, timedelta(hours=settings.invite_token_ttl_hours), actor_id)
    body = (
        f"Xin chào {user.full_name},\n\n"
        f"Tài khoản cổng khách hàng của bạn đã được tạo (tên đăng nhập: {user.username}).\n"
        f"Đặt mật khẩu tại đường dẫn sau (hiệu lực {settings.invite_token_ttl_hours} giờ, dùng một lần):\n\n"
        f"{_link('accept-invite', raw)}\n\n"
        "Lần đăng nhập đầu tiên bạn sẽ thiết lập xác thực 2 lớp bằng ứng dụng xác thực "
        "(Google Authenticator, Microsoft Authenticator…).\n"
        "Nếu bạn không chờ thư này, hãy bỏ qua.\n"
    )
    return mail.Mail(to=user.email, subject="Mời đặt mật khẩu tài khoản cổng khách hàng", body=body)


def _reset_mail(db: Session, user: SysUser, actor_id: int | None) -> mail.Mail | None:
    if not user.is_active:
        return None
    settings = get_settings()
    raw = _issue(db, user, TokenPurpose.PASSWORD_RESET, timedelta(minutes=settings.password_reset_token_ttl_min), None)
    body = (
        f"Xin chào {user.full_name},\n\n"
        "Có yêu cầu đặt lại mật khẩu cho tài khoản của bạn. Đặt mật khẩu mới tại đường dẫn sau "
        f"(hiệu lực {settings.password_reset_token_ttl_min} phút, dùng một lần):\n\n"
        f"{_link('reset-password', raw)}\n\n"
        "Nếu không phải bạn yêu cầu, hãy bỏ qua thư này – mật khẩu hiện tại vẫn giữ nguyên.\n"
    )
    return mail.Mail(to=user.email, subject="Đặt lại mật khẩu", body=body)


def _changed_mail(db: Session, user: SysUser, actor_id: int | None) -> mail.Mail | None:
    body = (
        f"Xin chào {user.full_name},\n\n"
        f"Mật khẩu tài khoản {user.username} vừa được đặt lại qua đường dẫn gửi tới email này; "
        "mọi phiên đăng nhập cũ đã bị đăng xuất.\n"
        "Nếu không phải bạn thực hiện, hãy dùng chức năng quên mật khẩu ngay và báo quản trị hệ thống.\n"
    )
    return mail.Mail(to=user.email, subject="Mật khẩu của bạn đã được đặt lại", body=body)


MailBuilder = Callable[[Session, SysUser, int | None], mail.Mail | None]


def _deliver(purpose: str, user_id: int, actor_id: int | None, build: MailBuilder) -> bool:
    """Thân chung của tác vụ gửi thư. Trả True nếu đã gửi, False nếu bỏ qua / lỗi ở lần thử cuối ngoài worker.

    Lỗi (SMTP, CSDL…): trong worker rq → ném lại để rq thử lại / chuyển FailedJobRegistry; lần thử cuối ghi nhật ký
    `user.mail_failed`. Ngoài worker (chạy nền trong tiến trình API) → một lần, ghi nhật ký, không ném (response
    đã trả).
    """
    try:
        with SessionLocal() as db:
            user = db.get(SysUser, user_id)
            message = build(db, user, actor_id) if user is not None and user.email else None
            if message is None:
                logger.info("Bỏ qua thư %s cho user_id=%s (không còn cần gửi)", purpose, user_id)
                return False
            db.commit()  # token (nếu có) lưu trước khi gửi
        mail.send(message)
        return True
    except mail.MailError as exc:
        reason = str(exc)
    except Exception as exc:  # tác vụ nền không được làm sập tiến trình
        reason = f"Lỗi không xác định: {type(exc).__name__}"
        logger.exception("Lỗi không xác định khi gửi thư %s tới user_id=%s", purpose, user_id)
    number, last = jobs.attempt()
    logger.error("Không gửi được thư %s tới user_id=%s (lần thử %s): %s", purpose, user_id, number, reason)
    if last:
        try:
            with SessionLocal() as log_db:
                after = {"purpose": purpose, "error": reason, "attempts": number}
                audit.record(log_db, "user.mail_failed", "user", user_id, actor_id, after=after)
                log_db.commit()
        except Exception:
            logger.exception("Không ghi được nhật ký lỗi gửi thư user_id=%s", user_id)
    if jobs.in_worker():
        raise mail.MailError(reason)
    return False


# Tác vụ nền (rq lưu đường dẫn hàm: đổi tên/chuyển chỗ thì tác vụ đang chờ trong Redis không chạy được)


def deliver_invitation(user_id: int, actor_id: int | None) -> bool:
    return _deliver(TokenPurpose.INVITE.value, user_id, actor_id, _invitation_mail)


def deliver_password_reset(user_id: int) -> bool:
    return _deliver(TokenPurpose.PASSWORD_RESET.value, user_id, None, _reset_mail)


def deliver_password_changed(user_id: int) -> bool:
    return _deliver("password_changed", user_id, user_id, _changed_mail)


# --- Thư mời -----------------------------------------------------------------------------------------------------


def send_invitation(db: Session, user: SysUser, actor_id: int | None, tasks: TaskQueue) -> None:
    """Ghi nhật ký `user.invite_sent` (= đã xếp thư), commit, xếp tác vụ gửi. Gửi lỗi hết lượt thử → thêm
    `user.mail_failed`; gửi lại qua `:resend-invite`."""
    audit.record(db, "user.invite_sent", "user", user.id, actor_id, after={"email": user.email})
    db.commit()
    jobs.enqueue(tasks, deliver_invitation, user.id, actor_id)


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


def request_password_reset(db: Session, identifier: str, tasks: TaskQueue) -> None:
    """Tên đăng nhập hoặc email. Không tìm thấy / không có email / bị vô hiệu hóa: im lặng (không lộ thông tin)."""
    key = identifier.strip().lower()
    user = db.scalar(select(SysUser).where(or_(SysUser.username == key, SysUser.email == key)))
    if user is None or not user.is_active or not user.email:
        return
    audit.record(db, "user.password_reset_requested", "user", user.id, None)
    db.commit()
    jobs.enqueue(tasks, deliver_password_reset, user.id)  # token cấp lúc gửi


def reset_password(db: Session, raw: str, new_password: str, tasks: TaskQueue) -> SysUser:
    user = _consume(db, raw, TokenPurpose.PASSWORD_RESET)
    _check_password(user, new_password)
    service._set_password(db, user, new_password, user.id)  # thu hồi mọi phiên
    user.failed_login_count = 0
    user.locked_until = None
    audit.record(db, "user.password_reset", "user", user.id, user.id, after={"method": "email_link"})
    db.commit()
    if user.email:
        jobs.enqueue(tasks, deliver_password_changed, user.id)
    return user


def _check_password(user: SysUser, password: str) -> None:
    if password.lower() == user.username:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            "Mật khẩu không được trùng tên đăng nhập",
            [{"field": "body.new_password", "issue": "invalid", "message": "Mật khẩu không được trùng tên đăng nhập"}],
        )
