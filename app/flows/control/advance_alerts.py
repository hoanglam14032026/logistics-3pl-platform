"""
Luồng 2 – cảnh báo M-10 "tạm ứng nhân viên quá hạn hoàn ứng" (03 §3.5; Giai đoạn 20).

- Quét định kỳ `scan(db, today)`: đọc tạm ứng quá hạn qua giao diện công bố của Luồng 1
  (`operations.public.overdue_staff_advances` – N4) → cảnh báo `staff_advance_overdue` (warning, không gắn lô) cho mọi
  `manager`; một cảnh báo chưa xử lý cho mỗi khoản tạm ứng (không lặp); nhật ký `alert.raise`, email / Zalo như Giai
  đoạn 13; leo thang BR-205 như cảnh báo khác. Khoản không còn quá hạn → tự đóng.
- EV-116 (`ops.staff_advance.settled`) → cảnh báo của khoản đó tự đóng.
- Tác vụ rq tự xếp lại mỗi `CONTROL_SCAN_INTERVAL_SEC` (worker xếp lần đầu khi khởi động) như `tracking`.
"""

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutil import now_utc, today_vn
from app.flows.control import notify, tracking
from app.flows.control.models import AlertSeverity, AlertStatus, AlertType, CtlAlert
from app.shared.audit import service as audit

logger = logging.getLogger("app.control.advance_alerts")
TYPE = AlertType.STAFF_ADVANCE_OVERDUE


def _key(advance_id: int) -> str:
    return f"{TYPE.value}:{advance_id}"


def scan(db: Session, today: date | None = None) -> dict[str, int]:
    from app.flows.operations import public as ops_public

    today = today or today_vn()
    stats = {"raised": 0, "resolved": 0}
    overdue = ops_public.overdue_staff_advances(db, today)
    for d in overdue:
        if tracking._open_alert(db, _key(d["advance_id"])) is not None:
            continue
        alert = CtlAlert(
            alert_type=TYPE.value,
            severity=AlertSeverity.WARNING.value,
            dedup_key=_key(d["advance_id"]),
            shipment_id=d["shipment_id"],
            contract_id=d["contract_id"],
            recipient_id=None,  # mọi manager
            message=(
                f"Tạm ứng {d['advance_no']} ({d['amount']} {d['currency']}, hợp đồng {d['contract_no']}) quá hạn hoàn "
                f"ứng {d['overdue_days']} ngày (hạn {d['due_date']})"
            )[:500],
            details=d,
            status=AlertStatus.OPEN.value,
            raised_at=now_utc(),
        )
        db.add(alert)
        db.flush()
        audit.record(
            db,
            "alert.raise",
            "alert",
            alert.id,
            None,
            after={"alert_type": alert.alert_type, "severity": alert.severity, "rule": "M-10", **d},
        )
        notify.queue(db, alert, "raised")
        stats["raised"] += 1
    still = {_key(d["advance_id"]) for d in overdue}
    for alert in db.scalars(
        select(CtlAlert)
        .where(CtlAlert.alert_type == TYPE.value, CtlAlert.status != AlertStatus.RESOLVED.value)
        .with_for_update()
    ):
        if alert.dedup_key not in still:
            advance_id = int(alert.dedup_key.split(":", 1)[1])
            stats["resolved"] += tracking.auto_resolve(db, TYPE, advance_id, "Tạm ứng không còn quá hạn")
    db.commit()
    return stats


def on_advance_settled(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    advance_id = envelope["data"]["advance_id"]
    return {"resolved": tracking.auto_resolve(db, TYPE, advance_id, "Tạm ứng đã hoàn ứng (EV-116)")}


SCAN_JOB_ID = "control-advance-scan"


def scheduled_scan() -> dict[str, int]:
    """Tác vụ rq: quét rồi tự xếp lần sau sau `CONTROL_SCAN_INTERVAL_SEC` giây (0 = tắt)."""
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        stats = scan(db)
    schedule_scan()
    return stats


def schedule_scan() -> bool:
    from redis.exceptions import RedisError
    from rq import Queue

    from app.core import jobs

    settings = get_settings()
    if not settings.job_queue_url or settings.control_scan_interval_sec <= 0:
        return False
    if "ops" not in settings.enabled_flows:
        return False
    queue = Queue(jobs.QUEUE_NAME, connection=jobs.redis_connection(settings.job_queue_url))
    try:
        queue.enqueue_in(
            timedelta(seconds=settings.control_scan_interval_sec), scheduled_scan, job_id=SCAN_JOB_ID, result_ttl=600
        )
    except RedisError as exc:
        logger.error("Không xếp được tác vụ quét tạm ứng quá hạn (%s)", type(exc).__name__)
        return False
    return True
