"""
Lô hàng / vận đơn (02 §3.2, §6.2; OPS-F02 – phần lõi) – Giai đoạn 8.

Quyền (06 §4 – L1 Lô hàng): ghi – `ops_staff` **được phân công** (người tạo lô được phân công mặc định);
đọc – `ops_staff`, `manager`, `business_manager`, `instructor`, `admin`; `customer` – mốc tiến độ lô của khách hàng
mình qua cổng (BR-111).
Phân công lại: tạm do `manager` (`:assign`) tới khi có EV-202 của Luồng 2 – **CẦN XÁC NHẬN**.

Máy trạng thái (02 §6.2):
- tạo → `draft` (hợp đồng `active`); PATCH chỉ khi `draft`.
- `:submit` draft → docs_collecting: hợp đồng vẫn `active`. Chưa sinh `ops_tasks` (chưa có quy trình mẫu).
- `:verify-documents` docs_collecting → docs_verified: tạm do nhân viên xác nhận – BR-103 (kiểm tra chứng từ) làm cùng
  chứng từ – **CẦN XÁC NHẬN**.
- `:start` docs_verified → in_execution.
- `:deliver` in_execution → delivered: kèm biên bản giao hàng (`ops_delivery_records`).
- `:close` delivered → closed. Chưa kiểm "mọi bước done/skipped" (chưa có bước).
- `:cancel` từ mọi trạng thái trước delivered, bắt buộc lý do.
Lô `closed`/`cancelled` chỉ đọc (service + trigger CSDL – migration 0009).

Sự kiện (01 §5, ghi vào `sys_audit_logs` cùng giao dịch – `app.shared.events`): tạo → EV-111; mỗi lần đổi trạng thái →
EV-112; giao hàng → thêm EV-142.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.numbering import next_number
from app.core.pagination import PageParams, like_pattern, order_by_clause, paginate
from app.core.rbac import Role, forbidden
from app.core.timeutil import VN_TZ, now_utc, today_vn
from app.flows.operations import service as contracts
from app.flows.operations import task_service
from app.flows.operations.models import (
    TERMINAL_SHIPMENT_STATUSES,
    ContractMode,
    ContractStatus,
    OpsContractScope,
    OpsDeliveryRecord,
    OpsServiceContract,
    OpsShipment,
    OpsShipmentStatusHistory,
    ShipmentStatus,
)
from app.flows.operations.shipment_schemas import DeliveryIn, ShipmentCreate, ShipmentUpdate, _check_rules
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import ShipmentCreated, ShipmentDelivered, ShipmentStatusChanged
from app.shared.identity.models import SysUser
from app.shared.masterdata.models import MdParty, PartyStatus

ENTITY = "shipment"
SHIPMENT_AUDIT_FIELDS = (
    "shipment_no",
    "contract_id",
    "contract_version",
    "direction",
    "transport_mode",
    "incoterm",
    "incoterm_place",
    "shipper_id",
    "consignee_id",
    "notify_party_id",
    "origin_location",
    "destination_location",
    "cargo_summary",
    "etd",
    "eta",
    "required_delivery_at",
    "claims_preferential_tariff",
    "priority",
    "assignee_id",
    "status",
    "status_reason",
)
_SORTABLE = {
    "created_at": OpsShipment.created_at,
    "shipment_no": OpsShipment.shipment_no,
    "required_delivery_at": OpsShipment.required_delivery_at,
    "eta": OpsShipment.eta,
    "status": OpsShipment.status,
}
# Chuyển trạng thái: hành động → (trạng thái nguồn được phép, trạng thái đích)
S = ShipmentStatus
TRANSITIONS: dict[str, tuple[tuple[ShipmentStatus, ...], ShipmentStatus]] = {
    "submit": ((S.DRAFT,), S.DOCS_COLLECTING),
    "verify_documents": ((S.DOCS_COLLECTING,), S.DOCS_VERIFIED),
    "start": ((S.DOCS_VERIFIED,), S.IN_EXECUTION),
    "deliver": ((S.IN_EXECUTION,), S.DELIVERED),
    "close": ((S.DELIVERED,), S.CLOSED),
    "cancel": ((S.DRAFT, S.DOCS_COLLECTING, S.DOCS_VERIFIED, S.IN_EXECUTION), S.CANCELLED),
}


def _not_found() -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy lô hàng")


def _transition_error(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _field_error(field: str, message: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, message, [{"field": f"body.{field}", "issue": "invalid", "message": message}]
    )


def _audit(db: Session, action: str, shipment: OpsShipment, actor_id: int | None, before=None, after=None) -> None:
    audit.record(db, action, ENTITY, shipment.id, actor_id, before, after)


def _check_version(shipment: OpsShipment, expected_version: int | None) -> None:
    if expected_version is not None and expected_version != shipment.row_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, 409, "Bản ghi đã bị người khác thay đổi; tải lại rồi thử lại")


def _ensure_writer(shipment: OpsShipment, actor: SysUser) -> None:
    """06 §4: `ops_staff` ghi lô **được phân công**."""
    if Role.OPS_STAFF.value not in actor.role_codes or shipment.assignee_id != actor.id:
        raise forbidden("Lô hàng không được phân công cho bạn")


def _ensure_open(shipment: OpsShipment) -> None:
    if shipment.status in TERMINAL_SHIPMENT_STATUSES:
        raise _transition_error(f"Lô hàng đã {shipment.status}; không thay đổi được")


# --- Đọc ------------------------------------------------------------------------------------------------------------


def get_shipment(db: Session, shipment_id: int, for_update: bool = False) -> OpsShipment:
    stmt = select(OpsShipment).where(
        OpsShipment.id == shipment_id, OpsShipment.mode == ContractMode.PRODUCTION.value
    )  # BR-01: nhân viên vận hành chỉ thấy production
    shipment = db.scalar(stmt.with_for_update() if for_update else stmt)
    if shipment is None:
        raise _not_found()
    return shipment


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, VN_TZ)


def list_shipments(
    db: Session,
    params: PageParams,
    q: str | None = None,
    contract_id: int | None = None,
    status: ShipmentStatus | None = None,
    assignee_id: int | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
) -> tuple[list[OpsShipment], int]:
    stmt = select(OpsShipment).where(OpsShipment.mode == ContractMode.PRODUCTION.value)
    if contract_id is not None:
        stmt = stmt.where(OpsShipment.contract_id == contract_id)
    if status is not None:
        stmt = stmt.where(OpsShipment.status == status.value)
    if assignee_id is not None:
        stmt = stmt.where(OpsShipment.assignee_id == assignee_id)
    if created_from is not None:  # ngày theo giờ Việt Nam
        stmt = stmt.where(OpsShipment.created_at >= _day_start(created_from))
    if created_to is not None:
        stmt = stmt.where(OpsShipment.created_at < _day_start(created_to) + timedelta(days=1))
    if q:
        pattern = like_pattern(q)
        stmt = stmt.where(
            or_(
                OpsShipment.shipment_no.ilike(pattern, escape="\\"),
                OpsShipment.shipper_name.ilike(pattern, escape="\\"),
                OpsShipment.consignee_name.ilike(pattern, escape="\\"),
            )
        )
    stmt = stmt.order_by(order_by_clause(params.sort, _SORTABLE), OpsShipment.id)
    return paginate(db, stmt, params)


def history(db: Session, shipment_id: int) -> list[OpsShipmentStatusHistory]:
    return list(
        db.scalars(
            select(OpsShipmentStatusHistory)
            .where(OpsShipmentStatusHistory.shipment_id == shipment_id)
            .order_by(OpsShipmentStatusHistory.id)
        )
    )


def delivery(db: Session, shipment_id: int) -> OpsDeliveryRecord | None:
    return db.scalar(select(OpsDeliveryRecord).where(OpsDeliveryRecord.shipment_id == shipment_id))


# --- Tạo, sửa nháp ----------------------------------------------------------------------------------------------------


def _active_contract(db: Session, contract_id: int, field: str = "contract_id") -> OpsServiceContract:
    """Hợp đồng `active` – khóa `FOR SHARE` tới hết giao dịch: không tạm dừng/thanh lý chen giữa được."""
    contracts.expire_due_contracts(db)
    contract = db.scalar(
        select(OpsServiceContract).where(OpsServiceContract.id == contract_id).with_for_update(read=True)
    )
    if contract is None or contract.mode != ContractMode.PRODUCTION.value:
        raise _field_error(field, "Không tìm thấy hợp đồng")
    if contract.status != ContractStatus.ACTIVE.value:
        raise _field_error(field, f"Hợp đồng không ở trạng thái active (đang {contract.status})")
    return contract


def _party_name(db: Session, party_id: int | None, field: str) -> str | None:
    if party_id is None:
        return None
    party = db.scalar(select(MdParty).where(MdParty.id == party_id).with_for_update(read=True))
    if party is None:
        raise _field_error(field, "Không tìm thấy khách hàng/đối tác")
    if party.status != PartyStatus.ACTIVE.value:
        raise _field_error(field, "Khách hàng/đối tác đã lưu trữ")
    return party.name


def _history(db: Session, shipment: OpsShipment, from_status: str | None, actor_id: int, note: str | None) -> None:
    db.add(
        OpsShipmentStatusHistory(
            shipment_id=shipment.id,
            from_status=from_status,
            to_status=shipment.status,
            changed_by=actor_id,
            note=note,
        )
    )


def _service_codes(db: Session, contract) -> list[str]:
    stmt = select(OpsContractScope.service_code).where(
        OpsContractScope.contract_id == contract.id, OpsContractScope.contract_version == contract.contract_version
    )
    return sorted(db.scalars(stmt))


def create_shipment(db: Session, data: ShipmentCreate, actor: SysUser) -> OpsShipment:
    if Role.OPS_STAFF.value not in actor.role_codes:
        raise forbidden()
    contract = _active_contract(db, data.contract_id)
    prefix = "LH" if contract.mode == ContractMode.PRODUCTION.value else "TR-LH"
    fields = data.model_dump(mode="python", exclude={"contract_id"})
    shipment = OpsShipment(
        **fields,
        shipper_name=_party_name(db, data.shipper_id, "shipper_id"),
        consignee_name=_party_name(db, data.consignee_id, "consignee_id"),
        notify_party_name=_party_name(db, data.notify_party_id, "notify_party_id"),
        shipment_no=next_number(db, prefix, today_vn().year, width=6),
        contract_id=contract.id,
        contract_version=contract.contract_version,
        mode=contract.mode,
        customer_id=contract.customer_id,
        status=ShipmentStatus.DRAFT.value,
        assignee_id=actor.id,
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(shipment)
    db.flush()
    _history(db, shipment, None, actor.id, None)
    _audit(db, "shipment.create", shipment, actor.id, after=audit.snapshot(shipment, SHIPMENT_AUDIT_FIELDS))
    publisher.publish(
        db,
        ShipmentCreated(
            shipment_id=shipment.id,
            shipment_no=shipment.shipment_no,
            contract_id=contract.id,
            customer_id=contract.customer_id,
            direction=shipment.direction,
            transport_mode=shipment.transport_mode,
            required_delivery_at=shipment.required_delivery_at,
            service_codes=_service_codes(db, contract),
        ),
        ENTITY,
        shipment.id,
        actor.id,
        shipment.mode,
    )
    db.commit()
    db.refresh(shipment)
    return shipment


def update_shipment(
    db: Session, shipment_id: int, data: ShipmentUpdate, actor: SysUser, expected_version: int | None
) -> OpsShipment:
    shipment = get_shipment(db, shipment_id, for_update=True)
    _ensure_writer(shipment, actor)
    _check_version(shipment, expected_version)
    if shipment.status != ShipmentStatus.DRAFT.value:
        raise _transition_error(f"Chỉ sửa được lô hàng nháp (đang {shipment.status})")
    changes = data.model_dump(mode="python", exclude_unset=True)
    merged = {f: changes.get(f, getattr(shipment, f)) for f in ("direction", "incoterm", "etd", "eta")}
    required = changes.get("required_delivery_at", shipment.required_delivery_at)
    try:
        _check_rules(merged["direction"], merged["incoterm"], merged["etd"], merged["eta"], required)
    except ValueError as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, 422, str(exc), [{"field": "body", "issue": "invalid", "message": str(exc)}]
        ) from exc
    before = audit.snapshot(shipment, SHIPMENT_AUDIT_FIELDS)
    for field, name_field in (
        ("shipper_id", "shipper_name"),
        ("consignee_id", "consignee_name"),
        ("notify_party_id", "notify_party_name"),
    ):
        if field in changes:
            changes[name_field] = _party_name(db, changes[field], field)
    for key, value in changes.items():
        setattr(shipment, key, value)
    shipment.updated_by = actor.id
    old, new = audit.changed(before, audit.snapshot(shipment, SHIPMENT_AUDIT_FIELDS))
    if new:
        _audit(db, "shipment.update", shipment, actor.id, old, new)
    db.commit()
    db.refresh(shipment)
    return shipment


# --- Chuyển trạng thái (02 §6.2) --------------------------------------------------------------------------------------


def _transition(
    db: Session,
    shipment_id: int,
    action: str,
    actor: SysUser,
    expected_version: int | None,
    note: str | None = None,
    reason: str | None = None,
    delivery_in: DeliveryIn | None = None,
) -> OpsShipment:
    shipment = get_shipment(db, shipment_id, for_update=True)
    _ensure_writer(shipment, actor)
    _check_version(shipment, expected_version)
    allowed, target = TRANSITIONS[action]
    if shipment.status not in {s.value for s in allowed}:
        raise _transition_error(
            f"Không chuyển được từ {shipment.status} sang {target.value} (cần {' / '.join(s.value for s in allowed)})"
        )
    if action == "submit":
        _active_contract(db, shipment.contract_id)  # 02 §6.2: hợp đồng active
    elif action == "verify_documents":
        from app.flows.operations import document_service

        document_service.ensure_documents_verified(db, shipment.id)  # BR-103 (Giai đoạn 16)
    now = now_utc()
    from_status = shipment.status
    shipment.status = target.value
    shipment.updated_by = actor.id
    if target is S.CANCELLED:
        shipment.status_reason = reason
        shipment.cancelled_at = now
        task_service.close_for_shipment(db, shipment.id)  # Giai đoạn 13: bước chưa xong → closed
    elif target is S.CLOSED:
        shipment.closed_at = now
        task_service.close_for_shipment(db, shipment.id)
    elif target is S.DELIVERED:
        if delivery_in.delivered_at > now:
            raise _field_error("delivered_at", "Thời điểm giao không được ở tương lai")
        db.add(OpsDeliveryRecord(shipment_id=shipment.id, created_by=actor.id, **delivery_in.model_dump()))
        shipment.delivered_at = delivery_in.delivered_at
    _history(db, shipment, from_status, actor.id, note or reason)
    after = {"status": target.value}
    if reason:
        after["status_reason"] = reason
    if delivery_in:
        after["delivery"] = delivery_in.model_dump(mode="json")
    _audit(db, f"shipment.{action}", shipment, actor.id, {"status": from_status}, after)
    publisher.publish(
        db,
        ShipmentStatusChanged(shipment_id=shipment.id, from_=from_status, to=target.value),
        ENTITY,
        shipment.id,
        actor.id,
        shipment.mode,
    )
    if target is S.DELIVERED:
        publisher.publish(
            db,
            ShipmentDelivered(
                shipment_id=shipment.id,
                delivered_at=delivery_in.delivered_at,
                sla_due_at=shipment.required_delivery_at,
                in_full=delivery_in.in_full,
                damage_free=delivery_in.damage_free,
            ),
            ENTITY,
            shipment.id,
            actor.id,
            shipment.mode,
        )
    db.commit()
    db.refresh(shipment)
    return shipment


def submit(db, shipment_id, actor, expected_version, note=None):
    return _transition(db, shipment_id, "submit", actor, expected_version, note=note)


def verify_documents(db, shipment_id, actor, expected_version, note=None):
    return _transition(db, shipment_id, "verify_documents", actor, expected_version, note=note)


def start(db, shipment_id, actor, expected_version, note=None):
    return _transition(db, shipment_id, "start", actor, expected_version, note=note)


def deliver(db, shipment_id, data: DeliveryIn, actor, expected_version):
    return _transition(db, shipment_id, "deliver", actor, expected_version, delivery_in=data)


def close(db, shipment_id, actor, expected_version, note=None):
    return _transition(db, shipment_id, "close", actor, expected_version, note=note)


def cancel(db, shipment_id, reason: str, actor, expected_version):
    return _transition(db, shipment_id, "cancel", actor, expected_version, reason=reason)


def assign(db: Session, shipment_id: int, assignee_id: int, actor: SysUser, expected_version: int | None):
    """Phân công lại (`manager`) – tạm thay EV-202 `ctl.assignment.changed` của Luồng 2."""
    shipment = get_shipment(db, shipment_id, for_update=True)
    _check_version(shipment, expected_version)
    _ensure_open(shipment)
    assignee = db.get(SysUser, assignee_id)
    if assignee is None or not assignee.is_active or Role.OPS_STAFF.value not in assignee.role_codes:
        raise _field_error("assignee_id", "Người được phân công phải là tài khoản ops_staff đang hoạt động")
    before = shipment.assignee_id
    shipment.assignee_id = assignee.id
    shipment.updated_by = actor.id
    if before != assignee.id:
        _audit(db, "shipment.assign", shipment, actor.id, {"assignee_id": before}, {"assignee_id": assignee.id})
    db.commit()
    db.refresh(shipment)
    return shipment


# --- Cổng khách hàng (PRT-06; 02 §9.4 `/api/v2/portal/shipments`) -----------------------------------------------------


def _portal_stmt(party_id: int):
    """BR-111: lô của hợp đồng `production`, `portal_enabled`, thuộc **chính khách hàng mình**; không có lô nháp."""
    return (
        select(OpsShipment, OpsServiceContract.contract_no)
        .join(OpsServiceContract, OpsServiceContract.id == OpsShipment.contract_id)
        .where(
            OpsShipment.customer_id == party_id,
            OpsServiceContract.customer_id == party_id,
            OpsShipment.mode == ContractMode.PRODUCTION.value,
            OpsServiceContract.portal_enabled.is_(True),
            OpsShipment.status != ShipmentStatus.DRAFT.value,
        )
    )


def _portal_row(shipment: OpsShipment, contract_no: str) -> dict:
    data = {c.key: getattr(shipment, c.key) for c in OpsShipment.__table__.columns}
    return {**data, "contract_no": contract_no}


def list_portal_shipments(
    db: Session, party_id: int, params: PageParams, status: ShipmentStatus | None = None, contract_id: int | None = None
) -> tuple[list[dict], int]:
    stmt = _portal_stmt(party_id)
    if status is not None:
        stmt = stmt.where(OpsShipment.status == status.value)
    if contract_id is not None:
        stmt = stmt.where(OpsShipment.contract_id == contract_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(order_by_clause(params.sort, _SORTABLE), OpsShipment.id)
    rows = db.execute(stmt.offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    return [_portal_row(s, no) for s, no in rows], total


def get_portal_shipment(db: Session, party_id: int, shipment_id: int) -> dict:
    """Lô của khách hàng khác, nháp, hợp đồng không bật cổng: 404 như không tồn tại (06 §2.1)."""
    row = db.execute(_portal_stmt(party_id).where(OpsShipment.id == shipment_id)).first()
    if row is None:
        raise _not_found()
    shipment, contract_no = row
    milestones = [
        {"status": h.to_status, "at": h.changed_at}
        for h in history(db, shipment.id)
        if h.to_status != ShipmentStatus.DRAFT.value
    ]
    return {**_portal_row(shipment, contract_no), "milestones": milestones}


# --- Điều kiện với hợp đồng -------------------------------------------------------------------------------------------


def has_open_shipments(db: Session, contract_id: int) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    OpsShipment.contract_id == contract_id, OpsShipment.status.not_in(TERMINAL_SHIPMENT_STATUSES)
                )
            )
        )
    )
