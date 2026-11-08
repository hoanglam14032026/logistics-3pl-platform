"""
Gửi email (06 §6 `SMTP_*`): thư mời đặt mật khẩu, đặt lại mật khẩu.

Backend theo `MAIL_BACKEND`:
- `smtp` (mặc định khi có `SMTP_HOST`): `smtplib`, STARTTLS (587) / SSL (465), timeout `SMTP_TIMEOUT_SEC`.
- `console` (mặc định khi chưa cấu hình SMTP – **chỉ dùng cho máy dev**): ghi thư ra log, kể cả đường dẫn.
- `memory`: giữ thư trong `OUTBOX` – dùng cho test.

Gửi đồng bộ, **sau khi commit**; lỗi gửi không làm hỏng thao tác chính (nơi gọi ghi nhật ký, cho gửi lại). Chuyển sang
hàng đợi tác vụ nền khi có (`JOB_QUEUE_URL`).
"""

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.core.config import Settings, get_settings

logger = logging.getLogger("app.mail")


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    body: str


# Thư đã "gửi" khi MAIL_BACKEND=memory (test đọc lại để lấy đường dẫn)
OUTBOX: list[Mail] = []


class MailError(Exception):
    pass


def _backend(settings: Settings) -> str:
    if settings.mail_backend != "auto":
        return settings.mail_backend
    return "smtp" if settings.smtp_host else "console"


def _send_smtp(mail: Mail, settings: Settings) -> None:
    if not settings.smtp_from:
        raise MailError("Chưa cấu hình SMTP_FROM")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = formataddr(("", mail.to))
    msg["Subject"] = mail.subject
    msg["Message-ID"] = make_msgid()
    msg.set_content(mail.body)
    context = ssl.create_default_context()
    try:
        if settings.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_sec, context=context
            )
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_sec)
        with server:
            if settings.smtp_security == "starttls":
                server.starttls(context=context)
            if settings.smtp_username:
                password = settings.smtp_password.get_secret_value() if settings.smtp_password else ""
                server.login(settings.smtp_username, password)
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"Gửi thư thất bại: {type(exc).__name__}") from exc


def send(mail: Mail, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    backend = _backend(settings)
    if backend == "memory":
        OUTBOX.append(mail)
    elif backend == "console":
        logger.warning(
            "MAIL_BACKEND=console – thư không được gửi thật:\nTo: %s\nSubject: %s\n\n%s",
            mail.to,
            mail.subject,
            mail.body,
        )
    else:
        _send_smtp(mail, settings)
