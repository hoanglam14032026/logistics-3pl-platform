"""
Theo dõi thực thi (Giai đoạn 12) – cập nhật thực tế các bước, tiến độ, tình trạng lô, cảnh báo chậm / lỗi.

Cập nhật bước (người được giao bước hoặc `manager`), mỗi thao tác một giao dịch: `ctl_task_updates` (chỉ ghi thêm) +
nhật ký `task.*` + sự kiện vào outbox (luồng nhận: Luồng 4):
- `:start` (open → in_progress; bước phụ thuộc phải xong) → `ctl.task.started`
- `PATCH` % tiến độ (in_progress)
- `:block` (open/in_progress → blocked, có lý do – "lỗi") → `ctl.task.blocked` + cảnh báo `task_blocked`
- `:unblock` → in_progress (đã bắt đầu) / open; cảnh báo `task_blocked` của bước tự đóng
- `:complete` (in_progress → done) → `ctl.task.completed` (kèm số giờ trễ); cảnh báo `task_overdue` của bước tự đóng

Quét định kỳ `scan(now)` (tác vụ rq tự xếp lại mỗi `CONTROL_SCAN_INTERVAL_SEC`; manager gọi `POST …/alerts:scan`):
- bước chưa xong quá `planned_end` → cảnh báo `task_overdue` (+ `ctl.task.delayed`); trễ từ
  `CONTROL_OVERDUE_CRITICAL_HOURS` giờ → mức `critical` (cảnh báo đang mở được nâng mức, nhật ký `alert.escalate`)
- lô dự kiến xong (giờ chuẩn còn lại từ bây giờ) sau hạn giao `required_delivery_at` → `shipment_late_risk` (critical);
  hết nguy cơ → tự đóng.
Cảnh báo gửi `account_manager` của kế hoạch (không có → mọi `manager`); một cảnh báo chưa xử lý cho mỗi bước/lô và loại
(không lặp); nhật ký `alert.raise` / `alert.acknowledge` / `alert.resolve`; sự kiện `ctl.alert.raised`.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.rbac import Role, forbidden
from app.core.timeutil import now_utc
from app.flows.control.models import (
    ACTIVE_TASK_STATUSES,
    AlertSeverity,
    AlertStatus,
    AlertType,
    CtlAlert,
    CtlContractPlan,
    CtlShipmentPlan,
    CtlShipmentTask,
    CtlTaskUpdate,
    TaskStatus,
    WorkStatus,
)
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import (
    CtlAlertRaised,
    CtlTaskBlocked,
    CtlTaskCompleted,
    CtlTaskDelayed,
    CtlTaskStarted,
)
from app.shared.identity.models import SysUser

logger = logging.getLogger("app.control.tracking")

FINISHED = (TaskStatus.DONE.value, TaskStatus.CLOSED.value)


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _hours(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() / 3600, 2)


# --- Tình trạng, tiến độ ----------------------------------------------------------------------------------------


def health(sp: CtlShipmentPlan, tasks: list[CtlShipmentTask], now: datetime | None = None) -> dict[str, Any]:
    now = now or now_utc()
    counts = {s.value: 0 for s in TaskStatus}
    for t in tasks:
        counts[t.status] += 1
    total = sum((Decimal(t.standard_duration_hours) for t in tasks), Decimal(0))
    done = sum((Decimal(t.standard_duration_hours) for t in tasks if t.status == TaskStatus.DONE.value), Decimal(0))
    done += sum(
        (Decimal(t.standard_duration_hours) * t.progress_pct / 100 for t in tasks if t.status == "in_progress"),
        Decimal(0),
    )
    active = [t for t in tasks if t.status in ACTIVE_TASK_STATUSES]
    overdue = [t for t in active if t.planned_end < now]
    remaining = sum((Decimal(t.standard_duration_hours) * (100 - t.progress_pct) / 100 for t in active), Decimal(0))
    finished_ends = [t.actual_end for t in tasks if t.actual_end]
    if active:
        forecast = now + timedelta(hours=float(remaining))
    else:
        forecast = max(finished_ends) if finished_ends else None
    late = bool(sp.required_delivery_at and forecast and forecast > sp.required_delivery_at)
    if sp.status == WorkStatus.CLOSED.value:
        state = "closed"
    elif tasks and not active:
        state = "completed"
    elif counts[TaskStatus.BLOCKED.value]:
        state = "blocked"
    elif late:
        state = "late"
    elif overdue:
        state = "at_risk"
    else:
        state = "on_track"
    current = next((t for t in sorted(active, key=lambda t: t.seq)), None)
    return {
        "health": state,
        "progress_pct": round(float(done / total * 100), 1) if total else (100.0 if tasks else 0.0),
        "task_counts": counts,
        "overdue_tasks": [t.step_code for t in overdue],
        "current_step": current.step_code if current else None,
        "planned_end": max((t.planned_end for t in tasks), default=None),
        "forecast_end": forecast,
        "required_delivery_at": sp.required_delivery_at,
        "late_vs_required_hours": _hours(sp.required_delivery_at, forecast) if late else 0.0,
    }


# --- Cảnh báo ---------------------------------------------------------------------------------------------------


def _recipient(db: Session, sp: CtlShipmentPlan) -> int | None:
    plan = db.get(CtlContractPlan, sp.plan_id) if sp.plan_id else None
    return plan.account_manager_id if plan else None


def _open_alert(db: Session, key: str) -> CtlAlert | None:
    return db.scalar(
        select(CtlAlert)
        .where(CtlAlert.dedup_key == key, CtlAlert.status != AlertStatus.RESOLVED.value)
        .with_for_update()
    )


def raise_alert(
    db: Session,
    alert_type: AlertType,
    severity: AlertSeverity,
    sp: CtlShipmentPlan,
    task: CtlShipmentTask | None,
    message: str,
    details: dict[str, Any],
) -> CtlAlert | None:
    """Tạo cảnh báo (không lặp); đã có cảnh báo chưa xử lý cùng loại → nâng mức nếu cần, trả None."""
    key = f"{alert_type.value}:{task.id if task else sp.shipment_id}"
    existing = _open_alert(db, key)
    if existing is not None:
        if severity == AlertSeverity.CRITICAL and existing.severity != AlertSeverity.CRITICAL.value:
            existing.severity = AlertSeverity.CRITICAL.value
            existing.details = details
            audit.record(
                db,
                "alert.escalate",
                "alert",
                existing.id,
                None,
                {"severity": "warning"},
                {"severity": "critical", **details},
            )
        return None
    alert = CtlAlert(
        alert_type=alert_type.value,
        severity=severity.value,
        dedup_key=key,
        shipment_id=sp.shipment_id,
        task_id=task.id if task else None,
        plan_id=sp.plan_id,
        recipient_id=_recipient(db, sp),
        message=message[:500],
        details=details,
        status=AlertStatus.OPEN.value,
    )
    db.add(alert)
    db.flush()
    audit.record(
        db,
        "alert.raise",
        "alert",
        alert.id,
        None,
        after={
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "shipment_id": alert.shipment_id,
            "task_id": alert.task_id,
            "recipient_id": alert.recipient_id,
            "message": alert.message,
        },
    )
    publisher.publish(
        db,
        CtlAlertRaised(
            alert_id=alert.id,
            alert_type=alert.alert_type,
            severity=alert.severity,
            shipment_id=alert.shipment_id,
            task_id=alert.task_id,
            plan_id=alert.plan_id,
            recipient_id=alert.recipient_id,
        ),
        "alert",
        alert.id,
        None,
        sp.mode,
    )
    logger.warning("Cảnh báo %s (%s) lô %s: %s", alert.alert_type, alert.severity, sp.shipment_no, alert.message)
    return alert


def auto_resolve(db: Session, alert_type: AlertType, ref_id: int, note: str) -> int:
    alert = _open_alert(db, f"{alert_type.value}:{ref_id}")
    if alert is None:
        return 0
    alert.status = AlertStatus.RESOLVED.value
    alert.resolved_at = now_utc()
    alert.resolution_note = note
    audit.record(db, "alert.resolve", "alert", alert.id, None, {"status": "open"}, {"status": "resolved", "note": note})
    return 1


def _alert_for_manager(db: Session, alert_id: int, actor: SysUser) -> CtlAlert:
    alert = db.scalar(select(CtlAlert).where(CtlAlert.id == alert_id).with_for_update())
    if alert is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy cảnh báo")
    return alert


def acknowledge(db: Session, alert_id: int, actor: SysUser) -> CtlAlert:
    alert = _alert_for_manager(db, alert_id, actor)
    if alert.status != AlertStatus.OPEN.value:
        raise _conflict(f"Cảnh báo đang '{alert.status}' – chỉ xác nhận cảnh báo mới")
    alert.status = AlertStatus.ACKNOWLEDGED.value
    alert.acknowledged_at = now_utc()
    alert.acknowledged_by = actor.id
    audit.record(db, "alert.acknowledge", "alert", alert.id, actor.id, {"status": "open"}, {"status": "acknowledged"})
    db.commit()
    return alert


def resolve(db: Session, alert_id: int, note: str, actor: SysUser) -> CtlAlert:
    alert = _alert_for_manager(db, alert_id, actor)
    if alert.status == AlertStatus.RESOLVED.value:
        raise _conflict("Cảnh báo đã xử lý")
    before = alert.status
    alert.status = AlertStatus.RESOLVED.value
    alert.resolved_at = now_utc()
    alert.resolved_by = actor.id
    alert.resolution_note = note
    audit.record(
        db, "alert.resolve", "alert", alert.id, actor.id, {"status": before}, {"status": "resolved", "note": note}
    )
    db.commit()
    return alert


# --- Cập nhật thực tế của bước ----------------------------------------------------------------------------------


def _lock(db: Session, task_id: int, actor: SysUser) -> tuple[CtlShipmentTask, CtlShipmentPlan]:
    task = db.get(CtlShipmentTask, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy bước")
    sp = db.scalar(select(CtlShipmentPlan).where(CtlShipmentPlan.id == task.shipment_plan_id).with_for_update())
    db.refresh(task, with_for_update=True)
    is_manager = Role.MANAGER.value in actor.role_codes
    if not is_manager and task.assignee_id != actor.id:
        if sp.assignee_id != actor.id:
            raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy bước")
        raise forbidden("Bước không được giao cho bạn")
    if sp.status != WorkStatus.OPEN.value:
        raise _conflict("Lô đã đóng/hủy – không cập nhật bước được")
    return task, sp


def _base(task: CtlShipmentTask, sp: CtlShipmentPlan) -> dict[str, Any]:
    return {
        "shipment_id": sp.shipment_id,
        "task_id": task.id,
        "step_code": task.step_code,
        "plan_id": sp.plan_id,
        "assignee_id": task.assignee_id,
        "planned_start": task.planned_start,
        "planned_end": task.planned_end,
    }


def _log(db: Session, task: CtlShipmentTask, action: str, before: str, actor: SysUser, note: str | None) -> None:
    task.updated_at = now_utc()
    task.updated_by = actor.id
    if note:
        task.note = note
    db.add(
        CtlTaskUpdate(
            task_id=task.id,
            action=action,
            from_status=before,
            to_status=task.status,
            progress_pct=task.progress_pct,
            note=note,
            actor_id=actor.id,
        )
    )
    audit.record(
        db,
        f"task.{action}",
        "task",
        task.id,
        actor.id,
        {"status": before},
        {
            "status": task.status,
            "progress_pct": task.progress_pct,
            "step_code": task.step_code,
            "note": note,
            "actual_start": task.actual_start,
            "actual_end": task.actual_end,
            "blocked_reason": task.blocked_reason,
        },
    )


def start(db: Session, task_id: int, note: str | None, actor: SysUser) -> CtlShipmentTask:
    task, sp = _lock(db, task_id, actor)
    if task.status != TaskStatus.OPEN.value:
        raise _conflict(f"Bước đang '{task.status}' – chỉ bắt đầu bước chưa làm")
    if task.depends_on:
        deps = db.scalars(
            select(CtlShipmentTask).where(
                CtlShipmentTask.shipment_plan_id == sp.id, CtlShipmentTask.step_code.in_(task.depends_on)
            )
        ).all()
        pending = sorted(d.step_code for d in deps if d.status not in FINISHED)
        if pending:
            raise _conflict(f"Bước phụ thuộc chưa xong: {', '.join(pending)}")
    now = now_utc()
    task.status = TaskStatus.IN_PROGRESS.value
    task.actual_start = now
    _log(db, task, "start", TaskStatus.OPEN.value, actor, note)
    publisher.publish(
        db,
        CtlTaskStarted(
            **_base(task, sp), actual_start=now, start_delay_hours=max(_hours(task.planned_start, now), 0.0)
        ),
        "task",
        task.id,
        actor.id,
        sp.mode,
    )
    db.commit()
    return task


def progress(db: Session, task_id: int, pct: int, note: str | None, actor: SysUser) -> CtlShipmentTask:
    task, _ = _lock(db, task_id, actor)
    if task.status != TaskStatus.IN_PROGRESS.value:
        raise _conflict("Chỉ cập nhật % tiến độ khi bước đang làm (in_progress)")
    task.progress_pct = pct
    _log(db, task, "progress", task.status, actor, note)
    db.commit()
    return task


def block(db: Session, task_id: int, reason: str, actor: SysUser) -> CtlShipmentTask:
    task, sp = _lock(db, task_id, actor)
    if task.status not in (TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value):
        raise _conflict(f"Bước đang '{task.status}' – không chuyển sang bị chặn được")
    before = task.status
    task.status = TaskStatus.BLOCKED.value
    task.blocked_reason = reason
    task.blocked_at = now_utc()
    _log(db, task, "block", before, actor, reason)
    publisher.publish(db, CtlTaskBlocked(**_base(task, sp), reason=reason), "task", task.id, actor.id, sp.mode)
    raise_alert(
        db,
        AlertType.TASK_BLOCKED,
        AlertSeverity.CRITICAL,
        sp,
        task,
        f"Lô {sp.shipment_no} – bước {task.step_code} bị chặn: {reason}",
        {"step_code": task.step_code, "reason": reason, "reported_by": actor.id},
    )
    db.commit()
    return task


def unblock(db: Session, task_id: int, note: str | None, actor: SysUser) -> CtlShipmentTask:
    task, _ = _lock(db, task_id, actor)
    if task.status != TaskStatus.BLOCKED.value:
        raise _conflict("Bước không bị chặn")
    task.status = TaskStatus.IN_PROGRESS.value if task.actual_start else TaskStatus.OPEN.value
    task.blocked_reason = None
    task.blocked_at = None
    _log(db, task, "unblock", TaskStatus.BLOCKED.value, actor, note)
    auto_resolve(db, AlertType.TASK_BLOCKED, task.id, "Bước đã gỡ chặn")
    db.commit()
    return task


def complete(db: Session, task_id: int, note: str | None, actor: SysUser) -> CtlShipmentTask:
    task, sp = _lock(db, task_id, actor)
    if task.status != TaskStatus.IN_PROGRESS.value:
        raise _conflict(f"Bước đang '{task.status}' – phải bắt đầu (in_progress) trước khi hoàn thành")
    now = now_utc()
    task.status = TaskStatus.DONE.value
    task.actual_end = now
    task.progress_pct = 100
    _log(db, task, "complete", TaskStatus.IN_PROGRESS.value, actor, note)
    publisher.publish(
        db,
        CtlTaskCompleted(
            **_base(task, sp),
            actual_start=task.actual_start,
            actual_end=now,
            delay_hours=max(_hours(task.planned_end, now), 0.0),
            is_checkpoint=task.is_checkpoint,
        ),
        "task",
        task.id,
        actor.id,
        sp.mode,
    )
    auto_resolve(db, AlertType.TASK_OVERDUE, task.id, "Bước đã hoàn thành")
    db.commit()
    return task


def updates_of(db: Session, task_id: int) -> list[CtlTaskUpdate]:
    return list(db.scalars(select(CtlTaskUpdate).where(CtlTaskUpdate.task_id == task_id).order_by(CtlTaskUpdate.id)))


# --- Quét chậm trễ ----------------------------------------------------------------------------------------------


def scan(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or now_utc()
    critical_after = get_settings().control_overdue_critical_hours
    stats = {"task_overdue": 0, "shipment_late_risk": 0, "resolved": 0}
    plans = db.scalars(
        select(CtlShipmentPlan).where(CtlShipmentPlan.status == WorkStatus.OPEN.value).with_for_update()
    ).all()
    for sp in plans:
        tasks = list(
            db.scalars(
                select(CtlShipmentTask).where(CtlShipmentTask.shipment_plan_id == sp.id).order_by(CtlShipmentTask.seq)
            )
        )
        for task in tasks:
            if task.status not in ACTIVE_TASK_STATUSES or task.planned_end >= now:
                continue
            overdue = _hours(task.planned_end, now)
            severity = AlertSeverity.CRITICAL if overdue >= critical_after else AlertSeverity.WARNING
            alert = raise_alert(
                db,
                AlertType.TASK_OVERDUE,
                severity,
                sp,
                task,
                f"Lô {sp.shipment_no} – bước {task.step_code} trễ {overdue} giờ so với kế hoạch",
                {
                    "step_code": task.step_code,
                    "overdue_hours": overdue,
                    "status": task.status,
                    "assignee_id": task.assignee_id,
                },
            )
            if alert is not None:
                stats["task_overdue"] += 1
                publisher.publish(
                    db,
                    CtlTaskDelayed(**_base(task, sp), overdue_hours=overdue, status=task.status),
                    "task",
                    task.id,
                    None,
                    sp.mode,
                )
        info = health(sp, tasks, now)
        if info["late_vs_required_hours"] > 0:
            alert = raise_alert(
                db,
                AlertType.SHIPMENT_LATE_RISK,
                AlertSeverity.CRITICAL,
                sp,
                None,
                f"Lô {sp.shipment_no} dự kiến xong trễ {info['late_vs_required_hours']} giờ so với hạn giao",
                {
                    "forecast_end": info["forecast_end"].isoformat(),
                    "required_delivery_at": sp.required_delivery_at.isoformat(),
                    "late_hours": info["late_vs_required_hours"],
                },
            )
            stats["shipment_late_risk"] += alert is not None
        else:
            stats["resolved"] += auto_resolve(
                db, AlertType.SHIPMENT_LATE_RISK, sp.shipment_id, "Hết nguy cơ trễ hạn giao"
            )
    db.commit()
    return stats


SCAN_JOB_ID = "control-scan"


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
    queue = Queue(jobs.QUEUE_NAME, connection=jobs.redis_connection(settings.job_queue_url))
    try:
        queue.enqueue_in(
            timedelta(seconds=settings.control_scan_interval_sec), scheduled_scan, job_id=SCAN_JOB_ID, result_ttl=600
        )
    except RedisError as exc:
        logger.error("Không xếp được tác vụ quét chậm trễ (%s)", type(exc).__name__)
        return False
    return True
