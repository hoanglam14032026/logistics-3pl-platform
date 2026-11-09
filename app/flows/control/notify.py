"""
Gửi cảnh báo ra ngoài ứng dụng (Giai đoạn 13) – email (mọi người nhận có email) và Zalo OA (khi đặt `ZALO_TOKEN`, người
nhận có `ctl_staff_profiles.zalo_user_id`).

- Người nhận: cảnh báo mới → `recipient_id` (account_manager) hoặc mọi `manager`; leo thang (BR-205) → mọi
  `business_manager` đang hoạt động.
- `queue(db, alert, purpose)`: tạo dòng `ctl_alert_notifications` **cùng giao dịch** với cảnh báo; sau commit xếp tác vụ
  `deliver_notification(id)` vào hàng đợi rq (`app.core.jobs` – tham số chỉ là id; không đưa nội dung, token vào Redis).
  Không có hàng đợi / Redis lỗi lúc xếp → gửi ngay trong tiến trình (như Giai đoạn 6) + log.
- Gửi lỗi → thử lại `JOB_MAX_RETRIES` lần theo `JOB_RETRY_INTERVALS` (như Giai đoạn 7); hết lượt → `failed`, log +
  nhật ký `alert.notify_failed`. Gửi lặp (rq "ít nhất một lần"): dòng đã `sent` bỏ qua.
"""

import logging
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core import jobs
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.rbac import Role
from app.core.timeutil import now_utc
from app.flows.control import assign
from app.flows.control.models import (
    CtlAlert,
    CtlAlertNotification,
    CtlStaffProfile,
    NotificationStatus,
)
from app.shared.audit import service as audit
from app.shared.identity.models import SysUser
from app.shared.notifications import mail, zalo

logger = logging.getLogger("app.control.notify")
PENDING_KEY = "alert_notifications_pending"


class _Inline:
    """Không có hàng đợi: chạy ngay (sau commit)."""

    def add_task(self, func, *args, **kwargs) -> None:
        func(*args, **kwargs)


def recipients(db: Session, alert: CtlAlert, purpose: str) -> list[SysUser]:
    if purpose == "escalated":
        return assign.users_with_role(db, Role.BUSINESS_MANAGER.value)
    if purpose == "kpi":  # Giai đoạn 15: cảnh báo KPI (EV-403) – mọi manager và business_manager
        users = assign.users_with_role(db, Role.MANAGER.value) + assign.users_with_role(db, Role.BUSINESS_MANAGER.value)
        return list({u.id: u for u in users}.values())
    if alert.recipient_id:
        user = db.get(SysUser, alert.recipient_id)
        return [user] if user and user.is_active else []
    return assign.users_with_role(db, Role.MANAGER.value)


def queue(db: Session, alert: CtlAlert, purpose: str) -> list[int]:
    zalo_on = zalo.enabled()
    ids = []
    for user in recipients(db, alert, purpose):
        targets = []
        if user.email:
            targets.append(("email", user.email))
        if zalo_on:
            zid = db.scalar(select(CtlStaffProfile.zalo_user_id).where(CtlStaffProfile.user_id == user.id))
            if zid:
                targets.append(("zalo", zid))
        for channel, address in targets:
            row = CtlAlertNotification(
                alert_id=alert.id,
                purpose=purpose,
                channel=channel,
                user_id=user.id,
                address=address,
                status=NotificationStatus.PENDING.value,
            )
            db.add(row)
            db.flush()
            ids.append(row.id)
    db.info.setdefault(PENDING_KEY, []).extend(ids)
    return ids


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    ids = session.info.pop(PENDING_KEY, None)
    for nid in ids or []:
        jobs.enqueue(_Inline(), deliver_notification, nid)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(PENDING_KEY, None)


def _text(alert: CtlAlert, purpose: str) -> tuple[str, str]:
    prefix = {"escalated": "[LEO THANG] ", "kpi": "[KPI] "}.get(purpose, "")
    subject = f"{prefix}[Cảnh báo {alert.severity}] {alert.message}"[:200]
    link = f"{get_settings().public_base_url}/control/alerts/{alert.id}"
    body = (
        f"{prefix}Cảnh báo {alert.alert_type} – mức {alert.severity}\n\n{alert.message}\n\n"
        + (
            "Cảnh báo chưa được xử lý quá thời hạn – chuyển lên quản lý hệ thống kinh doanh (BR-205).\n\n"
            if purpose == "escalated"
            else ""
        )
        + f"Xem và xử lý: {link}\n"
    )
    return subject, body


def deliver_notification(notification_id: int) -> None:
    """Tác vụ nền: gửi một thông báo. Lỗi → thử lại (rq); hết lượt → failed + nhật ký."""
    with SessionLocal() as db:
        row = db.scalar(
            select(CtlAlertNotification).where(CtlAlertNotification.id == notification_id).with_for_update()
        )
        if row is None or row.status != NotificationStatus.PENDING.value:
            return
        alert = db.get(CtlAlert, row.alert_id)
        subject, body = _text(alert, row.purpose)
        row.attempts += 1
        try:
            if row.channel == "email":
                mail.send(mail.Mail(to=row.address, subject=subject, body=body))
            else:
                zalo.send_text(row.address, f"{subject}\n{body}")
        except (mail.MailError, zalo.ZaloError, OSError) as exc:
            attempt_no, last = jobs.attempt()
            final = last or not jobs.in_worker()
            row.last_error = str(exc)[:500]
            logger.error(
                "Gửi cảnh báo %s qua %s tới người dùng %s lỗi (lần thử %d)%s: %s",
                alert.id,
                row.channel,
                row.user_id,
                attempt_no,
                " – hết lượt" if final else " – rq sẽ thử lại",
                exc,
            )
            if final:
                row.status = NotificationStatus.FAILED.value
                audit.record(
                    db,
                    "alert.notify_failed",
                    "alert",
                    alert.id,
                    None,
                    after={
                        "channel": row.channel,
                        "user_id": row.user_id,
                        "purpose": row.purpose,
                        "attempts": row.attempts,
                        "error": row.last_error,
                    },
                )
            db.commit()
            if jobs.in_worker():
                raise
            return
        row.status = NotificationStatus.SENT.value
        row.sent_at = now_utc()
        row.last_error = None
        db.commit()


def summary(db: Session, alert_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(CtlAlertNotification).where(CtlAlertNotification.alert_id == alert_id).order_by(CtlAlertNotification.id)
    ).all()
    return [
        {
            "channel": r.channel,
            "purpose": r.purpose,
            "user_id": r.user_id,
            "status": r.status,
            "attempts": r.attempts,
            "sent_at": r.sent_at,
            "last_error": r.last_error,
        }
        for r in rows
    ]
