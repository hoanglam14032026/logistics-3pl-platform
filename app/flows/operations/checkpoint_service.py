"""
Điểm kiểm soát của lô (Giai đoạn 16): nhận hàng → thông quan → xuất kho → giao hàng.

- Tạo (`ops_staff` phụ trách lô, lô chưa đóng/hủy): chọn loại (mặc định cả 4), thời điểm dự kiến, địa điểm
  (`md_locations` đang hoạt động) – mỗi loại một điểm / lô; tạo lại loại đã có → 409. Nhật ký `checkpoint.create`.
- Xác nhận (`ops_staff` phụ trách lô): `passed` (qua) / `failed` (trượt – bắt buộc ghi chú) + thời điểm thực tế
  (không ở tương lai). Điểm đã `passed` khóa; `failed` → xác nhận lại (`attempt_no` tăng). Thông quan `passed` cần số tờ
  khai, luồng (xanh / vàng / đỏ), thời điểm nộp (≤ thời điểm thông quan). Mỗi lần ghi `ops_checkpoint_evaluations`
  (chỉ thêm), nhật ký `checkpoint.evaluate`, sự kiện `ops.checkpoint.evaluated` (Luồng 4 – KPI-05); thông quan đạt →
  EV-141 (KPI-06). Không bắt buộc thứ tự giữa các điểm (CẦN XÁC NHẬN).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.timeutil import now_utc
from app.flows.operations.models import (
    CHECKPOINT_SEQ,
    TERMINAL_SHIPMENT_STATUSES,
    CheckpointType,
    OpsCheckpoint,
    OpsCheckpointEvaluation,
    OpsShipment,
)
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import CustomsCleared, OpsCheckpointEvaluated
from app.shared.identity.models import SysUser
from app.shared.masterdata.models import MdLocation, PartyStatus

ENTITY = "checkpoint"


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, message, [{"field": field, "issue": "invalid", "message": message}]
    )


def _writable_shipment(db: Session, shipment_id: int, actor: SysUser) -> OpsShipment:
    from app.flows.operations import shipment_service

    shipment = shipment_service.get_shipment(db, shipment_id, for_update=True)
    shipment_service._ensure_writer(shipment, actor)
    if shipment.status in TERMINAL_SHIPMENT_STATUSES:
        raise _conflict(f"Lô hàng đã {shipment.status}; không thay đổi điểm kiểm soát được")
    return shipment


def list_checkpoints(db: Session, shipment_id: int) -> list[OpsCheckpoint]:
    return list(
        db.scalars(select(OpsCheckpoint).where(OpsCheckpoint.shipment_id == shipment_id).order_by(OpsCheckpoint.seq))
    )


def evaluations_of(db: Session, checkpoint_id: int) -> list[OpsCheckpointEvaluation]:
    return list(
        db.scalars(
            select(OpsCheckpointEvaluation)
            .where(OpsCheckpointEvaluation.checkpoint_id == checkpoint_id)
            .order_by(OpsCheckpointEvaluation.attempt_no)
        )
    )


def create(db: Session, shipment_id: int, items: list[dict[str, Any]], actor: SysUser) -> list[OpsCheckpoint]:
    shipment = _writable_shipment(db, shipment_id, actor)
    items = items or [{"checkpoint_type": t.value} for t in CheckpointType]
    existing = {c.checkpoint_type for c in list_checkpoints(db, shipment.id)}
    dup = sorted({i["checkpoint_type"] for i in items} & existing)
    if dup:
        raise AppError(ErrorCode.DUPLICATE, 409, f"Lô đã có điểm kiểm soát: {', '.join(dup)}")
    created = []
    for item in items:
        location_id = item.get("location_id")
        if location_id is not None:
            loc = db.scalar(select(MdLocation).where(MdLocation.id == location_id).with_for_update(read=True))
            if loc is None or loc.status != PartyStatus.ACTIVE.value:
                raise _invalid("body.items.location_id", f"Địa điểm {location_id} không tồn tại hoặc đã lưu trữ")
        cp = OpsCheckpoint(
            shipment_id=shipment.id,
            checkpoint_type=item["checkpoint_type"],
            seq=CHECKPOINT_SEQ[CheckpointType(item["checkpoint_type"])],
            location_id=location_id,
            planned_at=item.get("planned_at"),
            created_by=actor.id,
        )
        db.add(cp)
        created.append(cp)
    try:
        db.flush()
    except IntegrityError as exc:  # tạo song song cùng loại
        db.rollback()
        raise AppError(ErrorCode.DUPLICATE, 409, "Lô đã có điểm kiểm soát loại này") from exc
    for cp in created:
        audit.record(
            db,
            "checkpoint.create",
            ENTITY,
            cp.id,
            actor.id,
            after={
                "shipment_id": shipment.id,
                "checkpoint_type": cp.checkpoint_type,
                "planned_at": cp.planned_at,
                "location_id": cp.location_id,
            },
        )
    db.commit()
    return sorted(created, key=lambda c: c.seq)


def get(db: Session, checkpoint_id: int) -> OpsCheckpoint:
    cp = db.get(OpsCheckpoint, checkpoint_id)
    if cp is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy điểm kiểm soát")
    from app.flows.operations import shipment_service

    shipment_service.get_shipment(db, cp.shipment_id)  # BR-01
    return cp


def evaluate(
    db: Session,
    checkpoint_id: int,
    result: str,
    actual_at: datetime,
    note: str | None,
    customs: dict[str, Any] | None,
    actor: SysUser,
) -> OpsCheckpoint:
    cp = get(db, checkpoint_id)
    shipment = _writable_shipment(db, cp.shipment_id, actor)
    db.refresh(cp, with_for_update=True)
    if cp.status == "passed":
        raise _conflict("Điểm kiểm soát đã đạt – không xác nhận lại được")
    if actual_at > now_utc():
        raise _invalid("body.actual_at", "Thời điểm thực tế không được ở tương lai")
    if result == "failed" and not note:
        raise _invalid("body.note", "Không đạt: bắt buộc ghi chú lý do")
    is_customs = cp.checkpoint_type == CheckpointType.CUSTOMS_CLEARANCE.value
    if customs and not is_customs:
        raise _invalid("body.customs", "Chỉ điểm thông quan có thông tin tờ khai")
    if is_customs and result == "passed":
        if not customs:
            raise _invalid("body.customs", "Thông quan đạt: cần số tờ khai, luồng, thời điểm nộp")
        if customs["submitted_at"] > actual_at:
            raise _invalid("body.customs.submitted_at", "Thời điểm nộp tờ khai phải trước thời điểm thông quan")
    attempt = cp.attempts + 1
    ev = OpsCheckpointEvaluation(
        checkpoint_id=cp.id,
        attempt_no=attempt,
        result=result,
        actual_at=actual_at,
        note=note,
        declaration_no=customs["declaration_no"] if customs else None,
        customs_channel=customs["channel"] if customs else None,
        customs_submitted_at=customs["submitted_at"] if customs else None,
        evaluated_by=actor.id,
    )
    db.add(ev)
    before = {"status": cp.status, "attempts": cp.attempts}
    cp.status, cp.attempts, cp.actual_at, cp.evaluated_by = result, attempt, actual_at, actor.id
    db.flush()
    audit.record(
        db,
        "checkpoint.evaluate",
        ENTITY,
        cp.id,
        actor.id,
        before,
        {
            "status": cp.status,
            "attempt_no": attempt,
            "actual_at": actual_at,
            "note": note,
            "customs": {k: str(v) for k, v in customs.items()} if customs else None,
        },
    )
    publisher.publish(
        db,
        OpsCheckpointEvaluated(
            checkpoint_id=cp.id,
            shipment_id=shipment.id,
            checkpoint_type=cp.checkpoint_type,
            attempt_no=attempt,
            result=result,
            actual_at=actual_at,
            planned_at=cp.planned_at,
            performer_id=actor.id,
        ),
        ENTITY,
        cp.id,
        actor.id,
        shipment.mode,
    )
    if is_customs and result == "passed":
        publisher.publish(
            db,
            CustomsCleared(
                shipment_id=shipment.id,
                declaration_no=customs["declaration_no"],
                channel=customs["channel"],
                submitted_at=customs["submitted_at"],
                cleared_at=actual_at,
            ),
            ENTITY,
            cp.id,
            actor.id,
            shipment.mode,
        )
    db.commit()
    db.refresh(cp)
    return cp
