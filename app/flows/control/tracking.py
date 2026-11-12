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
from app.flows.control import notify
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
    CtlAlertEscalated,
    CtlAlertRaised,
    CtlTaskBlocked,
    CtlTaskCompleted,
    CtlTaskDelayed,
    CtlTaskStarted,
    CtlTaskSynced,
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
    at: datetime | None = None,
) -> CtlAlert | None:
    """Tạo cảnh báo (không lặp); đã có cảnh báo chưa xử lý cùng loại → nâng mức nếu cần, trả None. `at`: thời điểm
    phát hiện (lần quét) – mặc định bây giờ."""
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
                {"kind": "severity", "severity": "critical", **details},
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
        raised_at=at or now_utc(),
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
    notify.queue(db, alert, "raised")  # Giai đoạn 13: email / Zalo qua hàng đợi
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
    bm_allowed = alert.escalated_at is not None or alert.alert_type == AlertType.KPI_BELOW_TARGET.value
    if Role.MANAGER.value not in actor.role_codes and not bm_allowed:
        raise forbidden("business_manager chỉ xử lý cảnh báo đã leo thang (BR-205)")
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


def _log(
    db: Session, task: CtlShipmentTask, action: str, before: str, actor_id: int | None, note: str | None, source: str
) -> None:
    task.updated_at = now_utc()
    task.updated_by = actor_id
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
            actor_id=actor_id,
        )
    )
    audit.record(
        db,
        f"task.{action}",
        "task",
        task.id,
        actor_id,
        {"status": before},
        {
            "status": task.status,
            "progress_pct": task.progress_pct,
            "step_code": task.step_code,
            "note": note,
            "actual_start": task.actual_start,
            "actual_end": task.actual_end,
            "blocked_reason": task.blocked_reason,
            "revision": task.revision,
            "source": source,
        },
    )


def publish_synced(
    db: Session, task: CtlShipmentTask, sp: CtlShipmentPlan, origin: str, actor_id: int | None, conflict: bool = False
) -> None:
    """Trạng thái đầy đủ của bước → Luồng 1 (`ops_tasks`) – Giai đoạn 13."""
    publisher.publish(
        db,
        CtlTaskSynced(
            task_id=task.id,
            shipment_id=sp.shipment_id,
            step_code=task.step_code,
            seq=task.seq,
            name=task.name,
            responsible_role=task.responsible_role,
            depends_on=list(task.depends_on or []),
            is_checkpoint=task.is_checkpoint,
            planned_start=task.planned_start,
            planned_end=task.planned_end,
            assignee_id=task.assignee_id,
            status=task.status,
            progress_pct=task.progress_pct,
            actual_start=task.actual_start,
            actual_end=task.actual_end,
            blocked_reason=task.blocked_reason,
            revision=task.revision,
            origin=origin,
            conflict=conflict,
        ),
        "task",
        task.id,
        actor_id,
        sp.mode,
    )


def apply_action(
    db: Session,
    task: CtlShipmentTask,
    sp: CtlShipmentPlan,
    action: str,
    actor_id: int | None,
    note: str | None = None,
    reason: str | None = None,
    pct: int | None = None,
    source: str = "control",
) -> None:
    """Chuyển trạng thái bước (đã khóa). `source`: `control` – thao tác ở Luồng 2 (phát ctl.task.* cho Luồng 4);
    `ops` – thay đổi Luồng 1 gửi lên (Luồng 1 đã phát EV-131/132/133). Luôn tăng `revision` + `ctl.task.synced`.
    Chuyển không hợp lệ → `AppError` 409, chưa đổi gì."""
    before = task.status
    now = now_utc()
    events: list = []
    if action == "start":
        if before != TaskStatus.OPEN.value:
            raise _conflict(f"Bước đang '{before}' – chỉ bắt đầu bước chưa làm")
        if task.depends_on:
            deps = db.scalars(
                select(CtlShipmentTask).where(
                    CtlShipmentTask.shipment_plan_id == sp.id, CtlShipmentTask.step_code.in_(task.depends_on)
                )
            ).all()
            pending = sorted(d.step_code for d in deps if d.status not in FINISHED)
            if pending:
                raise _conflict(f"Bước phụ thuộc chưa xong: {', '.join(pending)}")
        task.status, task.actual_start = TaskStatus.IN_PROGRESS.value, now
        events.append(
            CtlTaskStarted(
                **_base(task, sp), actual_start=now, start_delay_hours=max(_hours(task.planned_start, now), 0.0)
            )
        )
    elif action == "progress":
        if before != TaskStatus.IN_PROGRESS.value:
            raise _conflict("Chỉ cập nhật % tiến độ khi bước đang làm (in_progress)")
        task.progress_pct = pct
    elif action == "block":
        if before not in (TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value):
            raise _conflict(f"Bước đang '{before}' – không chuyển sang bị chặn được")
        task.status, task.blocked_reason, task.blocked_at = TaskStatus.BLOCKED.value, reason, now
        events.append(CtlTaskBlocked(**_base(task, sp), reason=reason))
        note = note or reason
    elif action == "unblock":
        if before != TaskStatus.BLOCKED.value:
            raise _conflict("Bước không bị chặn")
        task.status = TaskStatus.IN_PROGRESS.value if task.actual_start else TaskStatus.OPEN.value
        task.blocked_reason = task.blocked_at = None
    elif action == "complete":
        if before != TaskStatus.IN_PROGRESS.value:
            raise _conflict(f"Bước đang '{before}' – phải bắt đầu (in_progress) trước khi hoàn thành")
        task.status, task.actual_end, task.progress_pct = TaskStatus.DONE.value, now, 100
        events.append(
            CtlTaskCompleted(
                **_base(task, sp),
                actual_start=task.actual_start,
                actual_end=now,
                delay_hours=max(_hours(task.planned_end, now), 0.0),
                is_checkpoint=task.is_checkpoint,
            )
        )
    else:
        raise ValueError(action)
    task.revision += 1
    _log(db, task, action, before, actor_id, note, source)
    if source == "control":  # thay đổi từ Luồng 1: Luồng 4 đã nhận EV-131/132/133
        for ev in events:
            ev = ev.model_copy(update={"revision": task.revision})  # Giai đoạn 14: Luồng 4 khử trùng theo revision
            publisher.publish(db, ev, "task", task.id, actor_id, sp.mode)
    publish_synced(db, task, sp, source, actor_id)
    if action == "block":
        raise_alert(
            db,
            AlertType.TASK_BLOCKED,
            AlertSeverity.CRITICAL,
            sp,
            task,
            f"Lô {sp.shipment_no} – bước {task.step_code} bị chặn: {reason}",
            {"step_code": task.step_code, "reason": reason, "reported_by": actor_id, "source": source},
        )
    elif action == "unblock":
        auto_resolve(db, AlertType.TASK_BLOCKED, task.id, "Bước đã gỡ chặn")
    elif action == "complete":
        auto_resolve(db, AlertType.TASK_OVERDUE, task.id, "Bước đã hoàn thành")


def _api(db: Session, task_id: int, action: str, actor: SysUser, **kw) -> CtlShipmentTask:
    task, sp = _lock(db, task_id, actor)
    apply_action(db, task, sp, action, actor.id, **kw)
    db.commit()
    return task


def start(db: Session, task_id: int, note: str | None, actor: SysUser) -> CtlShipmentTask:
    return _api(db, task_id, "start", actor, note=note)


def progress(db: Session, task_id: int, pct: int, note: str | None, actor: SysUser) -> CtlShipmentTask:
    return _api(db, task_id, "progress", actor, note=note, pct=pct)


def block(db: Session, task_id: int, reason: str, actor: SysUser) -> CtlShipmentTask:
    return _api(db, task_id, "block", actor, reason=reason)


def unblock(db: Session, task_id: int, note: str | None, actor: SysUser) -> CtlShipmentTask:
    return _api(db, task_id, "unblock", actor, note=note)


def complete(db: Session, task_id: int, note: str | None, actor: SysUser) -> CtlShipmentTask:
    return _api(db, task_id, "complete", actor, note=note)


# --- Đồng bộ ngược: Luồng 1 → Luồng 2 (Giai đoạn 13) -------------------------------------------------------------

_OPS_ACTIONS = {"EV-131": "start", "EV-132": "complete", "EV-133": "block", "EV-135": "unblock"}
_TARGET = {"start": "in_progress", "complete": "done", "block": "blocked"}


def apply_from_ops(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    """EV-131/132/133/ops.task.unblocked từ Luồng 1. Chuyển tiếp thay đổi của chính Luồng 2 (`origin = control`) → bỏ
    qua. Thay đổi ở Luồng 1 hợp lệ → áp dụng, xác nhận (`ctl.task.synced`); đã đúng trạng thái → xác nhận lại; không
    hợp lệ (xung đột) → gửi trạng thái chuẩn (`conflict`), cảnh báo `task_sync_conflict`, nhật ký task.sync_conflict."""
    data = envelope["data"]
    if data.get("origin") == "control":
        return {"applied": False, "reason": "relay_of_control_change"}
    task = db.get(CtlShipmentTask, data["ctl_task_id"])
    if task is None:
        return {"applied": False, "reason": "task_not_found"}
    sp = db.scalar(select(CtlShipmentPlan).where(CtlShipmentPlan.id == task.shipment_plan_id).with_for_update())
    db.refresh(task, with_for_update=True)
    action = _OPS_ACTIONS[envelope["event_code"]]
    actor_id = envelope.get("actor_id")
    target = _TARGET.get(action)
    if (target and task.status == target) or (action == "unblock" and task.status in ("open", "in_progress")):
        publish_synced(db, task, sp, "ops", actor_id)  # đã đúng – xác nhận để Luồng 1 bỏ pending_local
        return {"applied": False, "reason": "already_in_state", "revision": task.revision}
    if sp.status != WorkStatus.OPEN.value:
        error = "Lô đã đóng/hủy ở Luồng 2"
    else:
        error = None
        try:
            apply_action(
                db,
                task,
                sp,
                action,
                actor_id,
                note=data.get("note"),
                reason=data.get("note") or "Báo từ Luồng 1",
                source="ops",
            )
            return {"applied": True, "action": action, "revision": task.revision}
        except AppError as exc:
            error = exc.message
    audit.record(
        db,
        "task.sync_conflict",
        "task",
        task.id,
        actor_id,
        {"status": task.status, "revision": task.revision},
        {
            "ops_action": action,
            "ops_base_revision": data.get("base_revision"),
            "error": error,
            "source_event_id": envelope["event_id"],
        },
    )
    publish_synced(db, task, sp, "control", actor_id, conflict=True)
    raise_alert(
        db,
        AlertType.TASK_SYNC_CONFLICT,
        AlertSeverity.WARNING,
        sp,
        task,
        f"Lô {sp.shipment_no} – bước {task.step_code}: thao tác '{action}' ở Luồng 1 không áp dụng được ({error}); "
        "Luồng 1 đã được đưa về trạng thái của Luồng 2",
        {"ops_action": action, "error": error, "status": task.status},
    )
    return {"applied": False, "reason": "conflict", "error": error, "revision": task.revision}


def resync(db: Session, sp: CtlShipmentPlan, actor_id: int | None) -> int:
    """Gửi lại trạng thái chuẩn mọi bước của lô cho Luồng 1 (revision hiện tại – Luồng 1 ghi đè) – dùng khi một bên lỡ
    sự kiện (vd. sự kiện `dead`). Nhật ký `task.resync`."""
    tasks = list(db.scalars(select(CtlShipmentTask).where(CtlShipmentTask.shipment_plan_id == sp.id)))
    for task in tasks:
        publish_synced(db, task, sp, "control", actor_id)
    audit.record(
        db, "task.resync", "shipment_plan", sp.id, actor_id, after={"shipment_id": sp.shipment_id, "tasks": len(tasks)}
    )
    db.commit()
    return len(tasks)


def updates_of(db: Session, task_id: int) -> list[CtlTaskUpdate]:
    return list(db.scalars(select(CtlTaskUpdate).where(CtlTaskUpdate.task_id == task_id).order_by(CtlTaskUpdate.id)))


# --- Quét chậm trễ ----------------------------------------------------------------------------------------------


def scan(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or now_utc()
    critical_after = get_settings().control_overdue_critical_hours
    stats = {"task_overdue": 0, "shipment_late_risk": 0, "resolved": 0, "escalated": 0}
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
                at=now,
            )
            if alert is not None:
                stats["task_overdue"] += 1
                publisher.publish(
                    db,
                    CtlTaskDelayed(
                        **_base(task, sp), overdue_hours=overdue, status=task.status, revision=task.revision
                    ),
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
                at=now,
            )
            stats["shipment_late_risk"] += alert is not None
        else:
            stats["resolved"] += auto_resolve(
                db, AlertType.SHIPMENT_LATE_RISK, sp.shipment_id, "Hết nguy cơ trễ hạn giao"
            )
    stats["escalated"] = escalate(db, now)
    db.commit()
    return stats


def escalate(db: Session, now: datetime) -> int:
    """BR-205: cảnh báo chưa xử lý (open / acknowledged) quá `CONTROL_ESCALATION_HOURS` → leo thang `business_manager`
    (một lần): nhật ký `alert.escalate`, sự kiện `ctl.alert.escalated`, gửi email / Zalo cho mọi business_manager."""
    cutoff = now - timedelta(hours=get_settings().control_escalation_hours)
    alerts = db.scalars(
        select(CtlAlert)
        .where(
            CtlAlert.status != AlertStatus.RESOLVED.value,
            CtlAlert.escalated_at.is_(None),
            CtlAlert.raised_at <= cutoff,
            CtlAlert.alert_type != AlertType.KPI_BELOW_TARGET.value,  # đã gửi business_manager ngay (Giai đoạn 15)
        )
        .order_by(CtlAlert.id)
        .with_for_update()
    ).all()
    for alert in alerts:
        hours = _hours(alert.raised_at, now)
        alert.escalated_at = now
        alert.escalated_to_role = Role.BUSINESS_MANAGER.value
        audit.record(
            db,
            "alert.escalate",
            "alert",
            alert.id,
            None,
            {"escalated_to_role": None, "status": alert.status},
            {
                "kind": "business_manager",
                "escalated_to_role": Role.BUSINESS_MANAGER.value,
                "unresolved_hours": hours,
                "rule": "BR-205",
            },
        )
        sp = db.scalar(select(CtlShipmentPlan).where(CtlShipmentPlan.shipment_id == alert.shipment_id))
        publisher.publish(
            db,
            CtlAlertEscalated(
                alert_id=alert.id,
                alert_type=alert.alert_type,
                severity=alert.severity,
                shipment_id=alert.shipment_id,
                escalated_to_role=Role.BUSINESS_MANAGER.value,
                unresolved_hours=hours,
            ),
            "alert",
            alert.id,
            None,
            sp.mode if sp else "production",
        )
        notify.queue(db, alert, "escalated")
        logger.warning("Leo thang cảnh báo %s lên business_manager sau %.1f giờ chưa xử lý", alert.id, hours)
    return len(alerts)


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
