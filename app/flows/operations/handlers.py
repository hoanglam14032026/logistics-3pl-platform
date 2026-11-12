"""
Luồng 1 nhận sự kiện (Giai đoạn 11): EV-202 `ctl.assignment.changed` từ Luồng 2 → đặt người phụ trách lô
(`ops_shipments.assignee_id`) – thay cho `manager` gán tay `:assign` (Giai đoạn 8, nay deprecated).

Bỏ qua (ghi kết quả, không lỗi): lô không có / đã đóng, hủy; người được giao không còn là `ops_staff` đang hoạt động; đã
đúng người. Nhật ký `shipment.assign` (`after.source = EV-202`, `event_id`).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import Role
from app.flows.operations.models import TERMINAL_SHIPMENT_STATUSES, OpsShipment
from app.shared.audit import service as audit
from app.shared.events import consumer
from app.shared.identity.models import SysUser

CONSUMER = "ops"


@consumer.handler(CONSUMER, "EV-202")
def on_assignment_changed(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    data = envelope["data"]
    if data.get("task_id") is not None:
        return {"applied": False, "reason": "task_level"}  # Luồng 1 chưa có bước (ops_tasks)
    shipment = db.scalar(select(OpsShipment).where(OpsShipment.id == data["shipment_id"]).with_for_update())
    if shipment is None:
        return {"applied": False, "reason": "shipment_not_found"}
    if shipment.status in TERMINAL_SHIPMENT_STATUSES:
        return {"applied": False, "reason": f"shipment_{shipment.status}"}
    assignee_id = data.get("assignee_id")
    user = db.get(SysUser, assignee_id) if assignee_id else None
    if user is None or not user.is_active or Role.OPS_STAFF.value not in user.role_codes:
        return {"applied": False, "reason": "assignee_not_active_ops_staff"}
    if shipment.assignee_id == assignee_id:
        return {"applied": False, "reason": "unchanged"}
    before = shipment.assignee_id
    shipment.assignee_id = assignee_id
    shipment.updated_by = envelope.get("actor_id")
    db.flush()
    audit.record(
        db,
        "shipment.assign",
        "shipment",
        shipment.id,
        envelope.get("actor_id"),
        before={"assignee_id": before},
        after={
            "assignee_id": assignee_id,
            "source": "EV-202",
            "event_id": envelope["event_id"],
            "method": data.get("method"),
        },
    )
    return {"applied": True, "previous_assignee_id": before, "assignee_id": assignee_id}


@consumer.handler(CONSUMER, "EV-215")
def on_task_synced(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    """Giai đoạn 13: trạng thái bước từ Luồng 2 (nguồn chuẩn) → `ops_tasks`."""
    from app.flows.operations import task_service

    return task_service.apply_synced(db, envelope)
