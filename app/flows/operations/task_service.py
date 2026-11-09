"""
Bước thực hiện ở Luồng 1 `ops_tasks` (Giai đoạn 13) – đồng bộ hai chiều với Luồng 2 (`ctl_shipment_tasks`).

Luồng 2 là **nguồn chuẩn** (mỗi bước có `revision` tăng mỗi lần đổi):
- Luồng 2 đổi → `ctl.task.synced` (trạng thái đầy đủ) → `apply_synced`: ghi đè khi `revision` ≥ bản đang có (sự kiện
  cũ hơn bị bỏ qua); thay đổi khởi phát ở Luồng 2 (`origin = control`) → Luồng 1 phát EV-131 / EV-132 / EV-133 /
  `ops.task.unblocked` (01 §5 – Luồng 1 là nơi phát).
- Người phụ trách lô đổi ở Luồng 1 (`:start`, `:complete`, `:block`, `:unblock`) → cập nhật tạm (`pending_local`) + phát
  EV-131/132/133/`ops.task.unblocked` (`origin = ops`, `base_revision`) → Luồng 2 kiểm tra rồi xác nhận bằng
  `ctl.task.synced` (ghi đè, bỏ `pending_local`); xung đột (Luồng 2 đã đổi khác) → Luồng 2 gửi trạng thái chuẩn
  (`conflict = true`) – Luồng 1 quay về trạng thái đó (nhật ký `ops_task.sync_conflict`). Hai bên luôn hội tụ.
- Lô hủy/đóng ở Luồng 1 → bước chưa xong `closed` (Luồng 2 đóng tương ứng khi nhận EV-112).
"""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.rbac import Role, forbidden
from app.core.timeutil import now_utc
from app.flows.operations.models import TERMINAL_SHIPMENT_STATUSES, OpsShipment, OpsTask, OpsTaskStatus
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import OpsTaskBlocked, OpsTaskCompleted, OpsTaskStarted, OpsTaskUnblocked
from app.shared.identity.models import SysUser

ACTIVE = (OpsTaskStatus.OPEN.value, OpsTaskStatus.IN_PROGRESS.value, OpsTaskStatus.BLOCKED.value)
FINISHED = (OpsTaskStatus.DONE.value, OpsTaskStatus.CLOSED.value)
_FIELDS = (
    "step_code",
    "seq",
    "name",
    "responsible_role",
    "depends_on",
    "is_checkpoint",
    "planned_start",
    "planned_end",
    "assignee_id",
    "status",
    "progress_pct",
    "actual_start",
    "actual_end",
    "blocked_reason",
)


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _parse(value):
    from datetime import datetime

    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def tasks_of(db: Session, shipment_id: int) -> list[OpsTask]:
    return list(db.scalars(select(OpsTask).where(OpsTask.shipment_id == shipment_id).order_by(OpsTask.seq)))


def _sequence_ok(db: Session, task: OpsTask) -> bool:
    earlier = db.scalars(select(OpsTask.status).where(OpsTask.shipment_id == task.shipment_id, OpsTask.seq < task.seq))
    return all(s in FINISHED for s in earlier)


def _publish_transition(
    db: Session, task: OpsTask, before: str, origin: str, actor_id: int | None, base: int, note: str | None
) -> str | None:
    """Phát EV-131/132/133/ops.task.unblocked theo chuyển trạng thái `before` → `task.status`."""
    common = {
        "task_id": task.id,
        "ctl_task_id": task.ctl_task_id,
        "shipment_id": task.shipment_id,
        "step_code": task.step_code,
        "base_revision": base,
        "origin": origin,
    }
    after = task.status
    if after == before:
        return None
    if after == OpsTaskStatus.IN_PROGRESS.value and before == OpsTaskStatus.OPEN.value:
        event = OpsTaskStarted(**common, performer_id=task.assignee_id, actual_start=task.actual_start or now_utc())
    elif after == OpsTaskStatus.DONE.value:
        event = OpsTaskCompleted(
            **common,
            performer_id=task.assignee_id,
            planned_end=task.planned_end,
            actual_end=task.actual_end or now_utc(),
            sequence_ok=_sequence_ok(db, task),
        )
    elif after == OpsTaskStatus.BLOCKED.value:
        event = OpsTaskBlocked(**common, reason_code="other", note=task.blocked_reason)
    elif before == OpsTaskStatus.BLOCKED.value and after in (OpsTaskStatus.OPEN.value, OpsTaskStatus.IN_PROGRESS.value):
        event = OpsTaskUnblocked(**common, note=note)
    else:
        return None
    shipment = db.get(OpsShipment, task.shipment_id)
    publisher.publish(db, event, "ops_task", task.id, actor_id, shipment.mode if shipment else "production")
    return event.code


# --- Luồng 2 → Luồng 1 ------------------------------------------------------------------------------------------


def apply_synced(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    data = envelope["data"]
    shipment = db.get(OpsShipment, data["shipment_id"])
    if shipment is None:
        return {"applied": False, "reason": "shipment_not_found"}
    task = db.scalar(select(OpsTask).where(OpsTask.ctl_task_id == data["task_id"]).with_for_update())
    values = {
        k: _parse(data[k]) if k in ("planned_start", "planned_end", "actual_start", "actual_end") else data[k]
        for k in _FIELDS
    }
    if task is None:
        task = OpsTask(
            shipment_id=shipment.id,
            ctl_task_id=data["task_id"],
            revision=data["revision"],
            **values,
            last_synced_at=now_utc(),
        )
        db.add(task)
        db.flush()
        before = None
    else:
        if data["revision"] < task.revision:
            return {"applied": False, "reason": "stale", "local_revision": task.revision}
        before = task.status
        was_pending = task.pending_local
        for k, v in values.items():
            setattr(task, k, v)
        task.revision = data["revision"]
        task.pending_local = False
        task.last_synced_at = now_utc()
        db.flush()
        if data.get("conflict") and was_pending:
            audit.record(
                db,
                "ops_task.sync_conflict",
                "ops_task",
                task.id,
                None,
                {"status": before},
                {"status": task.status, "revision": task.revision, "source_event_id": envelope["event_id"]},
            )
    published = None
    if before is not None and data.get("origin") == "control":
        published = _publish_transition(db, task, before, "control", envelope.get("actor_id"), task.revision, None)
    return {
        "applied": True,
        "ops_task_id": task.id,
        "status": task.status,
        "revision": task.revision,
        "published": published,
    }


# --- Thao tác ở Luồng 1 ----------------------------------------------------------------------------------------


def _lock(db: Session, task_id: int, actor: SysUser) -> OpsTask:
    task = db.get(OpsTask, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy bước")
    shipment = db.scalar(select(OpsShipment).where(OpsShipment.id == task.shipment_id).with_for_update())
    db.refresh(task, with_for_update=True)
    if Role.OPS_STAFF.value not in actor.role_codes or actor.id not in (shipment.assignee_id, task.assignee_id):
        raise forbidden("Lô / bước không được phân công cho bạn")
    if shipment.status in TERMINAL_SHIPMENT_STATUSES:
        raise _conflict(f"Lô hàng đã {shipment.status}; không cập nhật bước được")
    return task


def act(
    db: Session, task_id: int, action: str, actor: SysUser, note: str | None = None, reason: str | None = None
) -> OpsTask:
    task = _lock(db, task_id, actor)
    before = task.status
    now = now_utc()
    if action == "start":
        if before != OpsTaskStatus.OPEN.value:
            raise _conflict(f"Bước đang '{before}' – chỉ bắt đầu bước chưa làm")
        deps = db.scalars(
            select(OpsTask).where(OpsTask.shipment_id == task.shipment_id, OpsTask.step_code.in_(task.depends_on or []))
        ).all()
        pending = sorted(d.step_code for d in deps if d.status not in FINISHED)
        if pending:
            raise _conflict(f"Bước phụ thuộc chưa xong: {', '.join(pending)}")
        task.status, task.actual_start = OpsTaskStatus.IN_PROGRESS.value, now
    elif action == "complete":
        if before != OpsTaskStatus.IN_PROGRESS.value:
            raise _conflict(f"Bước đang '{before}' – phải bắt đầu trước khi hoàn thành")
        task.status, task.actual_end, task.progress_pct = OpsTaskStatus.DONE.value, now, 100
    elif action == "block":
        if before not in (OpsTaskStatus.OPEN.value, OpsTaskStatus.IN_PROGRESS.value):
            raise _conflict(f"Bước đang '{before}' – không chuyển sang bị chặn được")
        task.status, task.blocked_reason = OpsTaskStatus.BLOCKED.value, reason
    elif action == "unblock":
        if before != OpsTaskStatus.BLOCKED.value:
            raise _conflict("Bước không bị chặn")
        task.status = OpsTaskStatus.IN_PROGRESS.value if task.actual_start else OpsTaskStatus.OPEN.value
        task.blocked_reason = None
    else:
        raise ValueError(action)
    task.pending_local = True
    task.updated_at, task.updated_by = now, actor.id
    db.flush()
    audit.record(
        db,
        f"ops_task.{action}",
        "ops_task",
        task.id,
        actor.id,
        {"status": before},
        {"status": task.status, "step_code": task.step_code, "note": note or reason, "base_revision": task.revision},
    )
    _publish_transition(db, task, before, "ops", actor.id, task.revision, note)
    db.commit()
    return task


def close_for_shipment(db: Session, shipment_id: int) -> int:
    """Lô hủy/đóng ở Luồng 1: bước chưa xong → closed (Luồng 2 tự đóng khi nhận EV-112)."""
    return db.execute(
        update(OpsTask)
        .where(OpsTask.shipment_id == shipment_id, OpsTask.status.in_(ACTIVE))
        .values(status=OpsTaskStatus.CLOSED.value, blocked_reason=None, updated_at=now_utc())
    ).rowcount
