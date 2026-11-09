"""
Luồng 4 – ghi dữ kiện từ sự kiện (Giai đoạn 14). Mỗi hàm `(db, envelope) -> dict` chạy trong giao dịch của bộ nhận
dùng chung (`app/shared/events/consumer.py`, consumer `assessment`) cùng dòng `sys_event_consumptions` → không xử lý
trùng một sự kiện (N5).

Một thay đổi của bước có thể đến Luồng 4 qua nhiều sự kiện (`ctl.task.blocked` + `ctl.task.synced` của Luồng 2, EV-133
Luồng 1 chuyển tiếp) – khử trùng theo `revision` của bước ở Luồng 2:
- trạng thái chỉ ghi đè khi `revision` ≥ bản đang có (sự kiện cũ hơn bỏ qua);
- lần bị chặn khóa (bước, revision) – mỗi lần chặn tính một lần;
- EV-13x `origin = ops` (Luồng 1 tự đổi, Luồng 2 chưa xác nhận, có thể bị từ chối do xung đột) → chỉ lưu
  `performer_id`, `sequence_ok`, `evidence_ok`; trạng thái đợi `ctl.task.synced` của Luồng 2.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.timeutil import now_utc
from app.flows.assessment.models import (
    AsmFactAlert,
    AsmFactContract,
    AsmFactShipment,
    AsmFactTask,
    AsmFactTaskBlock,
)

BLOCKED = "blocked"


def _dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _meta(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": envelope.get("mode") or "production",
        "last_event_id": envelope["event_id"],
        "updated_at": now_utc(),
    }


def _locked(db: Session, model, key: str, value: int, defaults: dict[str, Any]):
    """Dòng dữ kiện theo khóa – tạo nếu chưa có (sự kiện đến trước sự kiện "tạo"), khóa tới hết giao dịch."""
    db.execute(insert(model).values({key: value, **defaults}).on_conflict_do_nothing(index_elements=[key]))
    return db.scalar(
        select(model).where(getattr(model, key) == value).with_for_update().execution_options(populate_existing=True)
    )


# --- Hợp đồng, lô -------------------------------------------------------------------------------------------------


def on_contract_activated(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    """EV-101: hợp đồng + SLA (đổi SLA khi phụ lục – EV-103 – làm sau)."""
    d = envelope["data"]
    values = {
        "contract_no": d["contract_no"],
        "customer_id": d["customer_id"],
        "sla": d.get("sla") or {},
        "valid_from": d["valid_from"],
        "valid_to": d["valid_to"],
        "activated_at": _dt(envelope["occurred_at"]),
        **_meta(envelope),
    }
    db.execute(
        insert(AsmFactContract)
        .values(contract_id=d["contract_id"], **values)
        .on_conflict_do_update(index_elements=["contract_id"], set_=values)
    )
    return {"contract_id": d["contract_id"], "sla_codes": sorted(values["sla"])}


def _shipment(db: Session, envelope: dict[str, Any]) -> AsmFactShipment:
    return _locked(db, AsmFactShipment, "shipment_id", envelope["data"]["shipment_id"], _meta(envelope))


def on_shipment_created(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    d = envelope["data"]
    row = _shipment(db, envelope)
    row.shipment_no, row.contract_id, row.customer_id = d["shipment_no"], d["contract_id"], d["customer_id"]
    row.required_delivery_at = _dt(d.get("required_delivery_at"))
    row.created_at = _dt(envelope["occurred_at"])
    row.status = row.status or "draft"
    _touch(row, envelope)
    return {"shipment_id": row.shipment_id, "contract_id": row.contract_id}


def on_shipment_status_changed(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    d = envelope["data"]
    row = _shipment(db, envelope)
    at = _dt(envelope["occurred_at"])
    if row.status_changed_at is not None and at < row.status_changed_at:
        return {"applied": False, "reason": "stale"}
    row.status, row.status_changed_at = d["to"], at
    _touch(row, envelope)
    return {"shipment_id": row.shipment_id, "status": row.status}


def on_shipment_delivered(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    d = envelope["data"]
    row = _shipment(db, envelope)
    row.delivered_at, row.sla_due_at = _dt(d["delivered_at"]), _dt(d["sla_due_at"])
    row.in_full, row.damage_free = d["in_full"], d["damage_free"]
    _touch(row, envelope)
    return {"shipment_id": row.shipment_id, "on_time": row.delivered_at <= row.sla_due_at}


def _touch(row, envelope: dict[str, Any]) -> None:
    for k, v in _meta(envelope).items():
        setattr(row, k, v)


# --- Bước -------------------------------------------------------------------------------------------------------


def _task(db: Session, envelope: dict[str, Any], task_id: int) -> AsmFactTask:
    d = envelope["data"]
    return _locked(
        db,
        AsmFactTask,
        "task_id",
        task_id,
        {"shipment_id": d["shipment_id"], "step_code": d["step_code"], **_meta(envelope)},
    )


def _record_block(db: Session, task: AsmFactTask, revision: int, envelope: dict[str, Any], reason: str | None) -> bool:
    inserted = db.execute(
        insert(AsmFactTaskBlock)
        .values(
            task_id=task.task_id,
            revision=revision,
            shipment_id=task.shipment_id,
            assignee_id=task.assignee_id,
            mode=envelope.get("mode") or "production",
            reason=(reason or "")[:500] or None,
            blocked_at=_dt(envelope["occurred_at"]),
            source_event_id=envelope["event_id"],
        )
        .on_conflict_do_nothing(constraint="uq_asm_fact_task_blocks_task_revision")
        .returning(AsmFactTaskBlock.id)
    ).first()
    if inserted:
        task.block_count += 1
    return bool(inserted)


def _apply(
    db: Session,
    task: AsmFactTask,
    envelope: dict[str, Any],
    revision: int | None,
    values: dict[str, Any],
    reason: str | None = None,
) -> dict[str, Any]:
    """Ghi trạng thái nếu không cũ hơn; chuyển sang `blocked` → ghi một lần bị chặn (khóa bước + revision)."""
    if revision is not None and revision < task.revision:
        return {"task_id": task.task_id, "applied": False, "reason": "stale", "fact_revision": task.revision}
    before = task.status
    for k, v in values.items():
        setattr(task, k, v)
    if revision is not None:
        task.revision = revision
    _touch(task, envelope)
    blocked = False
    if task.status == BLOCKED and before != BLOCKED and revision is not None:
        blocked = _record_block(db, task, revision, envelope, reason)
    return {
        "task_id": task.task_id,
        "applied": True,
        "status": task.status,
        "revision": task.revision,
        "block_recorded": blocked,
    }


def on_task_synced(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    """`ctl.task.synced` – trạng thái đầy đủ của bước (nguồn chuẩn)."""
    d = envelope["data"]
    task = _task(db, envelope, d["task_id"])
    values = {
        "step_code": d["step_code"],
        "assignee_id": d["assignee_id"],
        "status": d["status"],
        "planned_start": _dt(d["planned_start"]),
        "planned_end": _dt(d["planned_end"]),
        "actual_start": _dt(d["actual_start"]),
        "actual_end": _dt(d["actual_end"]),
    }
    return _apply(db, task, envelope, d["revision"], values, d.get("blocked_reason"))


_CTL_STATUS = {"ctl.task.started": "in_progress", "ctl.task.completed": "done", "ctl.task.blocked": BLOCKED}


def on_ctl_task_event(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    """`ctl.task.started|completed|blocked|delayed` – thay đổi ở Luồng 2 (kèm `revision` từ Giai đoạn 14)."""
    d = envelope["data"]
    code = envelope["event_code"]
    task = _task(db, envelope, d["task_id"])
    if code == "ctl.task.delayed":
        overdue = Decimal(str(d["overdue_hours"]))
        if task.max_overdue_hours is None or overdue > task.max_overdue_hours:
            task.max_overdue_hours = overdue
        _touch(task, envelope)
        return {"task_id": task.task_id, "overdue_hours": float(overdue)}
    values: dict[str, Any] = {
        "assignee_id": d["assignee_id"],
        "planned_start": _dt(d["planned_start"]),
        "planned_end": _dt(d["planned_end"]),
        "status": _CTL_STATUS[code],
    }
    if code == "ctl.task.started":
        values["actual_start"] = _dt(d["actual_start"])
    elif code == "ctl.task.completed":
        values["actual_end"] = _dt(d["actual_end"])
        if d.get("actual_start"):
            values["actual_start"] = _dt(d["actual_start"])
    revision = d.get("revision")
    if revision is None:  # sự kiện trước Giai đoạn 14: không khử trùng được lần chặn – chỉ lấy trạng thái
        result = _apply(db, task, envelope, None, {k: v for k, v in values.items() if k != "status"})
        return {**result, "reason": "no_revision"}
    return _apply(db, task, envelope, revision, values, d.get("reason"))


_OPS_STATUS = {"EV-131": "in_progress", "EV-132": "done", "EV-133": BLOCKED}


def on_ops_task_event(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    """EV-131/132/133/`ops.task.unblocked` của Luồng 1."""
    d = envelope["data"]
    code = envelope["event_code"]
    task = _task(db, envelope, d["ctl_task_id"])
    task.ops_task_id = d["task_id"]
    if d.get("performer_id") is not None:
        task.performer_id = d["performer_id"]
    if code == "EV-132":
        task.sequence_ok, task.evidence_ok = d.get("sequence_ok"), d.get("evidence_ok")
    if d.get("origin") != "control":  # chưa được Luồng 2 xác nhận – đợi ctl.task.synced
        _touch(task, envelope)
        return {"task_id": task.task_id, "applied": False, "reason": "awaiting_control_confirmation"}
    values: dict[str, Any] = {}
    if code in _OPS_STATUS:
        values["status"] = _OPS_STATUS[code]
    if code == "EV-131":
        values["actual_start"] = _dt(d["actual_start"])
    elif code == "EV-132":
        values["actual_end"], values["planned_end"] = _dt(d["actual_end"]), _dt(d["planned_end"])
    if not values:  # gỡ chặn: trạng thái sau (open / in_progress) lấy từ ctl.task.synced
        _touch(task, envelope)
        return {"task_id": task.task_id, "applied": False, "reason": "status_from_synced"}
    return _apply(db, task, envelope, d["base_revision"], values, d.get("note"))


# --- Cảnh báo ---------------------------------------------------------------------------------------------------


def on_alert_raised(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    d = envelope["data"]
    row = _locked(
        db,
        AsmFactAlert,
        "alert_id",
        d["alert_id"],
        {
            "alert_type": d["alert_type"],
            "severity": d["severity"],
            "shipment_id": d["shipment_id"],
            "raised_at": _dt(envelope["occurred_at"]),
            **_meta(envelope),
        },
    )
    row.alert_type, row.severity, row.shipment_id = d["alert_type"], d["severity"], d["shipment_id"]
    row.task_id, row.recipient_id = d.get("task_id"), d.get("recipient_id")
    row.raised_at = _dt(envelope["occurred_at"])
    _touch(row, envelope)
    return {"alert_id": row.alert_id}


def on_alert_escalated(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    d = envelope["data"]
    at = _dt(envelope["occurred_at"])
    row = _locked(
        db,
        AsmFactAlert,
        "alert_id",
        d["alert_id"],
        {  # chưa nhận ctl.alert.raised (vd. phát trước khi bật Luồng 4) – suy ra thời điểm phát
            "alert_type": d["alert_type"],
            "severity": d["severity"],
            "shipment_id": d["shipment_id"],
            "raised_at": at - timedelta(hours=float(d["unresolved_hours"])),
            **_meta(envelope),
        },
    )
    row.severity = d["severity"]
    row.escalated_at, row.escalated_to_role = at, d["escalated_to_role"]
    _touch(row, envelope)
    return {"alert_id": row.alert_id, "escalated_to_role": row.escalated_to_role}
