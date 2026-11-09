"""
Hợp đồng dịch vụ (02 §3.1, §6.1; OPS-F01 – phần lõi) và điều kiện cổng khách hàng (02 §9.2; 06 §4).

Máy trạng thái (02 §6.1) – Giai đoạn 3:
- tạo → `draft` (`ops_staff`, `manager`); PATCH chỉ khi `draft`.
- `draft` → `:activate` → `active` (`manager`): có `signed_date`, `valid_to ≥ hôm nay`, khách hàng đang hoạt động.
  Chưa kiểm biểu giá, file scan đã ký (02 §3.8, §3.11 – làm khi có) – **CẦN XÁC NHẬN**.
- `active` ⇄ `suspended` (`:suspend` có lý do / `:resume`) – `manager`.
- `active` → `expired`: hệ thống, khi qua `valid_to` (`expire_due_contracts`; chạy khi khởi động ứng dụng và trước mỗi
  thao tác đọc/ghi hợp đồng – tới khi có hàng đợi tác vụ nền).
- Thanh lý (`liquidating` → `closed`) làm cùng quyết toán (02 §3.9).

"Hợp đồng còn hiệu lực cho cổng": `status = active`, `valid_from ≤ hôm nay ≤ valid_to` (giờ Việt Nam), `portal_enabled`.
"""

from datetime import date

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.numbering import next_number
from app.core.pagination import PageParams, like_pattern, order_by_clause, paginate
from app.core.rbac import Role
from app.core.timeutil import now_utc, today_vn
from app.flows.operations.models import (
    TERMINAL_SHIPMENT_STATUSES,
    AttachmentKind,
    ContractMode,
    ContractStatus,
    OpsContractAttachment,
    OpsContractRate,
    OpsContractScope,
    OpsContractSla,
    OpsContractVersion,
    OpsServiceContract,
    OpsShipment,
)
from app.flows.operations.schemas import (
    ContractCreate,
    ContractUpdate,
    RateOut,
    ScopeOut,
    SlaOut,
    VersionCreate,
)
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import ContractActivated
from app.shared.files import service as files
from app.shared.files.models import SysFile
from app.shared.masterdata import service as md_service
from app.shared.masterdata.models import MdParty, PartyStatus, PartyType

CONTRACT_AUDIT_FIELDS = (
    "contract_no",
    "customer_id",
    "customer_name",
    "customer_tax_code",
    "customer_contract_ref",
    "signed_date",
    "valid_from",
    "valid_to",
    "liability_terms",
    "payment_terms",
    "advance_required",
    "payment_due_days",
    "portal_enabled",
    "status",
    "status_reason",
)
_SORTABLE = {
    "created_at": OpsServiceContract.created_at,
    "contract_no": OpsServiceContract.contract_no,
    "valid_from": OpsServiceContract.valid_from,
    "valid_to": OpsServiceContract.valid_to,
    "customer_name": OpsServiceContract.customer_name,
}
# Trạng thái còn ràng buộc với khách hàng (chưa đóng)
_OPEN_STATUSES = tuple(s.value for s in ContractStatus if s is not ContractStatus.CLOSED)


# Bảng con theo phiên bản: tên trường API → (model ORM, schema đầu ra, khóa sắp xếp)
CHILDREN = {
    "service_scopes": (OpsContractScope, ScopeOut, OpsContractScope.service_code),
    "sla_terms": (OpsContractSla, SlaOut, OpsContractSla.sla_code),
    "rates": (OpsContractRate, RateOut, OpsContractRate.rate_code),
}
# Điều khoản đưa vào bản chụp phiên bản (không gồm trạng thái, lý do nội bộ)
SNAPSHOT_FIELDS = (
    "contract_no",
    "customer_contract_ref",
    "customer_id",
    "customer_name",
    "customer_tax_code",
    "signed_date",
    "valid_from",
    "valid_to",
    "liability_terms",
    "payment_terms",
    "advance_required",
    "payment_due_days",
)
READER_ROLES = (Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.INSTRUCTOR, Role.ADMIN)
FILE_OWNER = "service_contract"


def _audit(db: Session, action: str, contract: OpsServiceContract, actor_id: int | None, before=None, after=None):
    audit.record(db, action, "service_contract", contract.id, actor_id, before, after)


def _not_found() -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy hợp đồng")


def _transition_error(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _field_error(field: str, message: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, message, [{"field": f"body.{field}", "issue": "invalid", "message": message}]
    )


def _check_version(contract: OpsServiceContract, expected_version: int | None) -> None:
    if expected_version is not None and expected_version != contract.row_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, 409, "Bản ghi đã bị người khác thay đổi; tải lại rồi thử lại")


def _ensure_not_closed(contract: OpsServiceContract) -> None:
    if contract.status == ContractStatus.CLOSED.value:
        raise _transition_error("Hợp đồng đã đóng; không thay đổi được")


# --- Bảng con theo phiên bản ------------------------------------------------------------------------------------


def children(db: Session, contract: OpsServiceContract, version: int | None = None) -> dict[str, list]:
    """Phạm vi, SLA, biểu giá của một phiên bản (mặc định: phiên bản hiện tại) – dạng schema đầu ra."""
    version = version or contract.contract_version
    result = {}
    for key, (model, out, order) in CHILDREN.items():
        rows = db.scalars(
            select(model).where(model.contract_id == contract.id, model.contract_version == version).order_by(order)
        )
        result[key] = [out.model_validate(r) for r in rows]
    return result


def _children_json(db: Session, contract: OpsServiceContract, version: int | None = None) -> dict[str, list]:
    return {k: [i.model_dump(mode="json") for i in v] for k, v in children(db, contract, version).items()}


def _write_children(db: Session, contract: OpsServiceContract, key: str, items: list, version: int) -> None:
    model = CHILDREN[key][0]
    db.execute(delete(model).where(model.contract_id == contract.id, model.contract_version == version))
    for item in items:
        # Kiểu python (Decimal, date) cho cột NUMERIC/DATE; StrEnum ghi như chuỗi
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        db.add(model(contract_id=contract.id, contract_version=version, **data))
    db.flush()


def _terms_json(db: Session, contract: OpsServiceContract) -> dict:
    """Điều khoản + bảng con của phiên bản hiện tại (dùng cho nhật ký và bản chụp phiên bản)."""
    return {**audit.snapshot(contract, SNAPSHOT_FIELDS), **_children_json(db, contract)}


def _check_terms(db: Session, contract: OpsServiceContract) -> list[tuple[str, str]]:
    """Điều kiện điều khoản khi kích hoạt / tạo phiên bản: ≥ 1 phạm vi (02 §3.1), có biểu giá (B khi kích hoạt),
    biểu giá chỉ cho dịch vụ thuộc phạm vi."""
    current = children(db, contract)
    scopes = {s.service_code for s in current["service_scopes"]}
    problems = []
    if not scopes:
        problems.append(("service_scopes", "Cần ít nhất một phạm vi dịch vụ"))
    if not current["rates"]:
        problems.append(("rates", "Cần biểu giá"))
    outside = sorted({r.service_code for r in current["rates"]} - scopes)
    if outside:
        problems.append(("rates", f"Biểu giá cho dịch vụ ngoài phạm vi: {', '.join(outside)}"))
    return problems


def _requirements_error(message: str, problems: list[tuple[str, str]]) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR,
        422,
        message,
        [{"field": f"body.{f}", "issue": "requirement", "message": m} for f, m in problems],
    )


def _has_attachment(db: Session, contract_id: int, kind: AttachmentKind) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    OpsContractAttachment.contract_id == contract_id, OpsContractAttachment.kind == kind.value
                )
            )
        )
    )


# --- Hết hạn tự động ---------------------------------------------------------------------------------------------


def expire_due_contracts(db: Session, today: date | None = None) -> list[int]:
    """`active` quá `valid_to` → `expired` (02 §6.1 – hệ thống). Trả id các hợp đồng vừa chuyển; ghi nhật ký."""
    today = today or today_vn()
    ids = db.scalars(
        update(OpsServiceContract)
        .where(OpsServiceContract.status == ContractStatus.ACTIVE.value, OpsServiceContract.valid_to < today)
        .values(
            status=ContractStatus.EXPIRED.value,
            updated_at=now_utc(),
            row_version=OpsServiceContract.row_version + 1,
        )
        .returning(OpsServiceContract.id)
        .execution_options(synchronize_session=False)
    ).all()
    for contract_id in ids:
        audit.record(
            db, "contract.expire", "service_contract", contract_id, None, {"status": "active"}, {"status": "expired"}
        )
    if ids:
        db.commit()
    return list(ids)


# --- Đọc ---------------------------------------------------------------------------------------------------------


def get_contract(db: Session, contract_id: int, for_update: bool = False) -> OpsServiceContract:
    expire_due_contracts(db)
    stmt = select(OpsServiceContract).where(OpsServiceContract.id == contract_id)
    contract = db.scalar(stmt.with_for_update() if for_update else stmt)
    if contract is None:
        raise _not_found()
    return contract


def list_contracts(
    db: Session,
    params: PageParams,
    q: str | None = None,
    customer_id: int | None = None,
    status: ContractStatus | None = None,
    portal_enabled: bool | None = None,
) -> tuple[list[OpsServiceContract], int]:
    expire_due_contracts(db)
    # BR-01: truy vấn của nhân viên vận hành chỉ thấy `production` (Giai đoạn 3 chỉ có production).
    stmt = select(OpsServiceContract).where(OpsServiceContract.mode == ContractMode.PRODUCTION.value)
    if customer_id is not None:
        stmt = stmt.where(OpsServiceContract.customer_id == customer_id)
    if status is not None:
        stmt = stmt.where(OpsServiceContract.status == status.value)
    if portal_enabled is not None:
        stmt = stmt.where(OpsServiceContract.portal_enabled.is_(portal_enabled))
    if q:
        pattern = like_pattern(q)
        stmt = stmt.where(
            or_(
                OpsServiceContract.contract_no.ilike(pattern, escape="\\"),
                OpsServiceContract.customer_contract_ref.ilike(pattern, escape="\\"),
                OpsServiceContract.customer_name.ilike(pattern, escape="\\"),
            )
        )
    stmt = stmt.order_by(order_by_clause(params.sort, _SORTABLE), OpsServiceContract.id)
    return paginate(db, stmt, params)


# --- Tạo, sửa nháp ----------------------------------------------------------------------------------------------


def _customer_snapshot(db: Session, customer_id: int) -> dict:
    party = db.scalar(select(MdParty).where(MdParty.id == customer_id).with_for_update(read=True))
    if party is None:
        raise _field_error("customer_id", "Không tìm thấy khách hàng")
    if party.party_type != PartyType.CUSTOMER.value:
        raise _field_error("customer_id", "Đối tác không phải khách hàng (party_type ≠ customer)")
    if party.status != PartyStatus.ACTIVE.value:
        raise _field_error("customer_id", "Khách hàng đã lưu trữ")
    return {"customer_id": party.id, "customer_name": party.name, "customer_tax_code": party.tax_code}


def create_contract(db: Session, data: ContractCreate, actor_id: int) -> OpsServiceContract:
    year = today_vn().year
    contract = OpsServiceContract(
        **data.model_dump(exclude={"customer_id", *CHILDREN}),
        **_customer_snapshot(db, data.customer_id),
        contract_no=next_number(db, "HD", year),
        status=ContractStatus.DRAFT.value,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(contract)
    db.flush()
    for key in CHILDREN:
        _write_children(db, contract, key, getattr(data, key), contract.contract_version)
    after = {**audit.snapshot(contract, CONTRACT_AUDIT_FIELDS), **_children_json(db, contract)}
    _audit(db, "contract.create", contract, actor_id, after=after)
    db.commit()
    return contract


def update_contract(
    db: Session, contract_id: int, data: ContractUpdate, actor_id: int, expected_version: int | None
) -> OpsServiceContract:
    contract = get_contract(db, contract_id, for_update=True)
    _check_version(contract, expected_version)
    if contract.status != ContractStatus.DRAFT.value:
        raise _transition_error("Chỉ sửa được hợp đồng nháp; hợp đồng đã kích hoạt thay đổi qua phụ lục (02 §3.7)")
    new_children = {key: getattr(data, key) for key in CHILDREN if key in data.model_fields_set}
    changes = data.model_dump(exclude_unset=True, exclude=set(CHILDREN))
    valid_from = changes.get("valid_from", contract.valid_from)
    valid_to = changes.get("valid_to", contract.valid_to)
    if valid_from > valid_to:
        raise _field_error("valid_to", "valid_from phải ≤ valid_to")
    before = {**audit.snapshot(contract, CONTRACT_AUDIT_FIELDS), **_children_json(db, contract)}
    for key, items in new_children.items():
        _write_children(db, contract, key, items, contract.contract_version)
    if "customer_id" in changes:
        changes.update(_customer_snapshot(db, changes.pop("customer_id")))
    for key, value in changes.items():
        setattr(contract, key, value)
    contract.updated_by = actor_id
    contract.updated_at = now_utc()
    old, new = audit.changed(
        before, {**audit.snapshot(contract, CONTRACT_AUDIT_FIELDS), **_children_json(db, contract)}
    )
    if new:
        _audit(db, "contract.update", contract, actor_id, old, new)
    db.commit()
    db.refresh(contract)
    return contract


# --- Chuyển trạng thái (02 §6.1) ---------------------------------------------------------------------------------


def _transition(
    db: Session,
    contract: OpsServiceContract,
    action: str,
    to_status: ContractStatus,
    actor_id: int,
    reason: str | None = None,
) -> OpsServiceContract:
    before = {"status": contract.status, "status_reason": contract.status_reason}
    contract.status = to_status.value
    contract.status_reason = reason
    contract.updated_by = actor_id
    contract.updated_at = now_utc()
    _audit(db, action, contract, actor_id, before, {"status": to_status.value, "status_reason": reason})
    db.commit()
    db.refresh(contract)
    return contract


def activate_contract(db: Session, contract_id: int, actor_id: int, expected_version: int | None):
    contract = get_contract(db, contract_id, for_update=True)
    _check_version(contract, expected_version)
    if contract.status != ContractStatus.DRAFT.value:
        raise _transition_error(f"Chỉ kích hoạt được hợp đồng nháp (đang {contract.status})")
    missing = []
    if contract.signed_date is None:
        missing.append(("signed_date", "Thiếu ngày ký"))
    if contract.valid_to < today_vn():
        missing.append(("valid_to", "Hợp đồng đã quá ngày hết hiệu lực"))
    missing += _check_terms(db, contract)
    if not _has_attachment(db, contract.id, AttachmentKind.SIGNED_CONTRACT):
        missing.append(("attachments", "Cần bản scan hợp đồng đã ký (file đính kèm loại signed_contract)"))
    if missing:
        raise _requirements_error("Chưa đủ điều kiện kích hoạt", missing)
    _customer_snapshot(db, contract.customer_id)  # khách hàng vẫn đang hoạt động
    contract.activated_at = now_utc()
    contract.activated_by = actor_id
    publisher.publish(db, _activated_event(db, contract), "service_contract", contract.id, actor_id, contract.mode)
    # Bản chụp phiên bản 1 (02 §3.1 ops_contract_versions)
    db.add(
        OpsContractVersion(
            contract_id=contract.id,
            contract_version=contract.contract_version,
            snapshot=_terms_json(db, contract),
            effective_from=contract.valid_from,
            created_by=actor_id,
        )
    )
    return _transition(db, contract, "contract.activate", ContractStatus.ACTIVE, actor_id)


def _activated_event(db: Session, contract: OpsServiceContract) -> ContractActivated:
    """EV-101 `ops.contract.activated` (01 §5) – phạm vi, SLA của phiên bản đang kích hoạt (Luồng 2 điền sẵn mục tiêu
    KPI kế hoạch từ SLA – 03 CTL-F01)."""
    current = children(db, contract)
    return ContractActivated(
        contract_id=contract.id,
        contract_no=contract.contract_no,
        customer_id=contract.customer_id,
        service_scope=[s.model_dump(mode="json") for s in current["service_scopes"]],
        sla={s.sla_code: s.model_dump(mode="json", exclude={"sla_code", "penalty_rule"}) for s in current["sla_terms"]},
        valid_from=contract.valid_from,
        valid_to=contract.valid_to,
    )


def suspend_contract(db: Session, contract_id: int, reason: str, actor_id: int, expected_version: int | None):
    contract = get_contract(db, contract_id, for_update=True)
    _check_version(contract, expected_version)
    if contract.status != ContractStatus.ACTIVE.value:
        raise _transition_error(f"Chỉ tạm dừng được hợp đồng đang hiệu lực (đang {contract.status})")
    return _transition(db, contract, "contract.suspend", ContractStatus.SUSPENDED, actor_id, reason)


def resume_contract(db: Session, contract_id: int, actor_id: int, expected_version: int | None):
    contract = get_contract(db, contract_id, for_update=True)
    _check_version(contract, expected_version)
    if contract.status != ContractStatus.SUSPENDED.value:
        raise _transition_error(f"Chỉ tiếp tục được hợp đồng đang tạm dừng (đang {contract.status})")
    if contract.valid_to < today_vn():
        # Tạm dừng qua ngày hết hạn: tiếp tục = hết hiệu lực luôn
        return _transition(db, contract, "contract.expire", ContractStatus.EXPIRED, actor_id)
    return _transition(db, contract, "contract.resume", ContractStatus.ACTIVE, actor_id)


# --- Phiên bản mới (02 §3.1, §3.7) -------------------------------------------------------------------------------


def create_version(
    db: Session, contract_id: int, data: VersionCreate, actor_id: int, expected_version: int | None
) -> OpsContractVersion:
    """Áp phụ lục đã ký: `contract_version + 1`, lưu bản chụp. Tạm thời do `manager` nhập trực tiếp kèm bản scan
    phụ lục (`attachment_id` loại `amendment`) – quy trình phụ lục đầy đủ (đề nghị, đàm phán, phê duyệt
    `CONTRACT_AMENDMENT` qua Luồng 2, xác nhận bản scan – 02 §3.7, §3.11) thay thế khi làm."""
    contract = get_contract(db, contract_id, for_update=True)
    _check_version(contract, expected_version)
    _ensure_not_closed(contract)
    if contract.status not in (ContractStatus.ACTIVE.value, ContractStatus.SUSPENDED.value):
        raise _transition_error(f"Chỉ tạo phiên bản mới cho hợp đồng đang hiệu lực/tạm dừng (đang {contract.status})")

    attachment = db.get(OpsContractAttachment, data.attachment_id)
    if attachment is None or attachment.contract_id != contract.id or attachment.kind != AttachmentKind.AMENDMENT:
        raise _field_error("attachment_id", "Cần file đính kèm loại amendment của chính hợp đồng này")
    used = db.scalar(select(exists().where(OpsContractVersion.attachment_id == attachment.id)))
    if used:
        raise _field_error("attachment_id", "Bản scan phụ lục này đã dùng cho một phiên bản khác")

    previous = db.scalar(
        select(OpsContractVersion.effective_from).where(
            OpsContractVersion.contract_id == contract.id,
            OpsContractVersion.contract_version == contract.contract_version,
        )
    )
    valid_to = data.valid_to or contract.valid_to
    problems = []
    if previous and data.effective_from < previous:
        problems.append(("effective_from", "Ngày hiệu lực phải sau ngày hiệu lực của phiên bản hiện tại"))
    if not (contract.valid_from <= data.effective_from <= valid_to):
        problems.append(("effective_from", "Ngày hiệu lực phải nằm trong thời hạn hợp đồng"))
    if valid_to < contract.valid_from:
        problems.append(("valid_to", "valid_to phải ≥ valid_from"))
    if problems:
        raise _requirements_error("Phiên bản mới không hợp lệ", problems)

    before = _terms_json(db, contract)
    old_version, new_version = contract.contract_version, contract.contract_version + 1
    current = children(db, contract)
    for key in CHILDREN:
        items = getattr(data, key) if key in data.model_fields_set else current[key]
        _write_children(db, contract, key, items, new_version)
    contract.contract_version = new_version
    for field in ("valid_to", "liability_terms", "payment_terms", "advance_required", "payment_due_days"):
        if field in data.model_fields_set:
            setattr(contract, field, getattr(data, field))
    problems = _check_terms(db, contract)
    if problems:
        raise _requirements_error("Phiên bản mới không hợp lệ", problems)
    contract.updated_by = actor_id
    contract.updated_at = now_utc()
    after = _terms_json(db, contract)
    version = OpsContractVersion(
        contract_id=contract.id,
        contract_version=new_version,
        snapshot=after,
        effective_from=data.effective_from,
        amendment_ref=data.amendment_ref,
        reason=data.reason,
        attachment_id=attachment.id,
        created_by=actor_id,
    )
    db.add(version)
    old, new = audit.changed(before, after)
    _audit(
        db,
        "contract.new_version",
        contract,
        actor_id,
        {"contract_version": old_version, **old},
        {"contract_version": new_version, "amendment_ref": data.amendment_ref, **new},
    )
    db.commit()
    db.refresh(version)
    return version


def list_versions(db: Session, contract: OpsServiceContract) -> list[OpsContractVersion]:
    return list(
        db.scalars(
            select(OpsContractVersion)
            .where(OpsContractVersion.contract_id == contract.id)
            .order_by(OpsContractVersion.contract_version)
        )
    )


# --- Thanh lý, đóng (02 §3.9.2, §6.1) --------------------------------------------------------------------------


def start_liquidation(db: Session, contract_id: int, reason: str | None, actor_id: int, expected_version: int | None):
    """`active`/`suspended`/`expired` → `liquidating` (`manager`). Còn hạn = chấm dứt trước hạn: bắt buộc lý do.
    Chưa có phê duyệt `CONTRACT_TERMINATION` (Luồng 2) – **CẦN XÁC NHẬN** tạm cho phép với lý do."""
    contract = get_contract(db, contract_id, for_update=True)
    _check_version(contract, expected_version)
    allowed = (ContractStatus.ACTIVE.value, ContractStatus.SUSPENDED.value, ContractStatus.EXPIRED.value)
    if contract.status not in allowed:
        raise _transition_error(f"Chỉ thanh lý được hợp đồng đang hiệu lực/tạm dừng/hết hạn (đang {contract.status})")
    early = contract.status != ContractStatus.EXPIRED.value and contract.valid_to >= today_vn()
    if early and not reason:
        raise _field_error("reason", "Thanh lý trước hạn (hợp đồng còn hiệu lực) phải có lý do")
    contract.liquidation_started_at = now_utc()
    return _transition(db, contract, "contract.start_liquidation", ContractStatus.LIQUIDATING, actor_id, reason)


def close_contract(db: Session, contract_id: int, actor_id: int, expected_version: int | None):
    """`liquidating` → `closed` (`manager`): cần bản scan biên bản thanh lý đã ký. Đã đóng: không sửa, không tải thêm
    file, không đổi trạng thái (kiểm ở service và trigger CSDL – migration 0006). Chưa kiểm quyết toán
    (BR-110 – 02 §3.9) và xác nhận bản scan hai bên (BR-116 – 02 §3.11) – **CẦN XÁC NHẬN**."""
    contract = get_contract(db, contract_id, for_update=True)
    _check_version(contract, expected_version)
    if contract.status != ContractStatus.LIQUIDATING.value:
        raise _transition_error(f"Chỉ đóng được hợp đồng đang thanh lý (đang {contract.status})")
    problems = []
    if not _has_attachment(db, contract.id, AttachmentKind.LIQUIDATION_MINUTES):
        problems.append(
            ("attachments", "Cần bản scan biên bản thanh lý đã ký (file đính kèm loại liquidation_minutes)")
        )
    # BR-110 (phần lô hàng – Giai đoạn 8): mọi lô closed/cancelled
    open_shipment = db.scalar(
        select(
            exists().where(
                OpsShipment.contract_id == contract.id, OpsShipment.status.not_in(TERMINAL_SHIPMENT_STATUSES)
            )
        )
    )
    if open_shipment:
        problems.append(("shipments", "Còn lô hàng chưa đóng/hủy (BR-110)"))
    if problems:
        raise _requirements_error("Chưa đủ điều kiện đóng hợp đồng", problems)
    contract.closed_at = now_utc()
    contract.closed_by = actor_id
    return _transition(db, contract, "contract.close", ContractStatus.CLOSED, actor_id, contract.status_reason)


# --- File đính kèm -------------------------------------------------------------------------------------------------

# Mặc định khách hàng tải được bản đã ký (PRT-01, PRT-08); file khác nội bộ trừ khi chọn
_CUSTOMER_VISIBLE_DEFAULT = {
    AttachmentKind.SIGNED_CONTRACT,
    AttachmentKind.AMENDMENT,
    AttachmentKind.LIQUIDATION_MINUTES,
}


def add_attachment(
    db: Session,
    contract_id: int,
    upload,
    kind: AttachmentKind,
    description: str | None,
    customer_visible: bool | None,
    actor_id: int,
) -> OpsContractAttachment:
    contract = get_contract(db, contract_id, for_update=True)
    _ensure_not_closed(contract)
    record = files.store(db, upload, "contract_document", "ops", FILE_OWNER, contract.id, actor_id)
    # Mọi lỗi sau khi file đã ghi xuống kho (tới hết commit) → rollback + xóa file: không có file mồ côi (06 §2.6)
    try:
        attachment = OpsContractAttachment(
            contract_id=contract.id,
            file_id=record.id,
            kind=kind.value,
            description=description,
            customer_visible=kind in _CUSTOMER_VISIBLE_DEFAULT if customer_visible is None else customer_visible,
            created_by=actor_id,
        )
        db.add(attachment)
        db.flush()
        _audit(
            db,
            "contract.attachment_add",
            contract,
            actor_id,
            after={
                "attachment_id": attachment.id,
                "kind": kind.value,
                "file_id": record.id,
                "original_filename": record.original_filename,
                "sha256": record.sha256,
                "customer_visible": attachment.customer_visible,
            },
        )
    except BaseException:
        db.rollback()
        files.get_storage().delete(record.storage_key)
        raise
    files.commit_or_discard(db, [record])
    db.refresh(attachment)
    return attachment


def list_attachments(db: Session, contract: OpsServiceContract, customer_only: bool = False) -> list[dict]:
    stmt = (
        select(OpsContractAttachment, SysFile)
        .join(SysFile, SysFile.id == OpsContractAttachment.file_id)
        .where(OpsContractAttachment.contract_id == contract.id)
        .order_by(OpsContractAttachment.id)
    )
    if customer_only:
        stmt = stmt.where(OpsContractAttachment.customer_visible.is_(True))
    return [
        {
            "id": a.id,
            "kind": a.kind,
            "description": a.description,
            "customer_visible": a.customer_visible,
            "file": f,
            "created_at": a.created_at,
        }
        for a, f in db.execute(stmt).all()
    ]


def _portal_visible(contract: OpsServiceContract | None, party_id: int | None) -> bool:
    """BR-111: khách hàng chỉ thấy hợp đồng `production` của chính khách hàng mình, có `portal_enabled`."""
    return (
        contract is not None
        and party_id is not None
        and contract.customer_id == party_id
        and contract.mode == ContractMode.PRODUCTION.value
        and contract.portal_enabled
    )


def _can_read_file(db: Session, user, file: SysFile) -> bool:
    """Quyền tải file của hợp đồng: vai trò được đọc hợp đồng (06 §4), hoặc khách hàng của hợp đồng (file được đánh
    dấu cho khách hàng xem)."""
    codes = set(user.role_codes)
    if codes & {r.value for r in READER_ROLES}:
        return True
    if Role.CUSTOMER.value in codes:
        contract = db.get(OpsServiceContract, file.owner_id)
        if not _portal_visible(contract, user.party_id):
            return False
        return bool(
            db.scalar(
                select(
                    exists().where(
                        OpsContractAttachment.file_id == file.id, OpsContractAttachment.customer_visible.is_(True)
                    )
                )
            )
        )
    return False


# --- Cổng khách hàng (PRT-01) --------------------------------------------------------------------------------------


def _portal_stmt(party_id: int):
    return select(OpsServiceContract).where(
        OpsServiceContract.customer_id == party_id,
        OpsServiceContract.mode == ContractMode.PRODUCTION.value,
        OpsServiceContract.portal_enabled.is_(True),
    )


def list_portal_contracts(
    db: Session, party_id: int, params: PageParams, status: ContractStatus | None = None
) -> tuple[list[OpsServiceContract], int]:
    expire_due_contracts(db)
    stmt = _portal_stmt(party_id)
    if status is not None:
        stmt = stmt.where(OpsServiceContract.status == status.value)
    stmt = stmt.order_by(order_by_clause(params.sort, _SORTABLE), OpsServiceContract.id)
    return paginate(db, stmt, params)


def get_portal_contract(db: Session, party_id: int, contract_id: int) -> OpsServiceContract:
    """Hợp đồng của khách hàng khác, không bật cổng, `training`: 404 như không tồn tại (06 §2.1)."""
    expire_due_contracts(db)
    contract = db.get(OpsServiceContract, contract_id)
    if not _portal_visible(contract, party_id):
        raise _not_found()
    return contract


# --- Điều kiện cho danh mục khách hàng, tài khoản cổng (đăng ký vào app.shared.masterdata) ------------------------


def has_portal_contract(db: Session, party_id: int, today: date | None = None) -> bool:
    """Khách hàng có hợp đồng `active`, đang trong thời hạn, `portal_enabled` (02 §9.2; 06 §4).

    Khóa `FOR SHARE` các hợp đồng thỏa điều kiện tới hết giao dịch: không tạm dừng được hợp đồng đó trong lúc đang tạo
    tài khoản."""
    today = today or today_vn()
    row = db.scalar(
        select(OpsServiceContract.id)
        .where(
            OpsServiceContract.customer_id == party_id,
            OpsServiceContract.status == ContractStatus.ACTIVE.value,
            OpsServiceContract.portal_enabled.is_(True),
            OpsServiceContract.valid_from <= today,
            OpsServiceContract.valid_to >= today,
        )
        .limit(1)
        .with_for_update(read=True)
    )
    return row is not None


def _guard_customer_account(db: Session, party: MdParty) -> str | None:
    if not has_portal_contract(db, party.id):
        return "Khách hàng chưa có hợp đồng đang hiệu lực cho phép dùng cổng (portal_enabled)"
    return None


def _guard_archive(db: Session, party: MdParty) -> str | None:
    open_contract = db.scalar(
        select(
            exists().where(OpsServiceContract.customer_id == party.id, OpsServiceContract.status.in_(_OPEN_STATUSES))
        )
    )
    return "Khách hàng còn hợp đồng chưa đóng; không lưu trữ được" if open_contract else None


def _guard_type_change(db: Session, party: MdParty) -> str | None:
    has_any = db.scalar(select(exists().where(OpsServiceContract.customer_id == party.id)))
    return "Khách hàng đã có hợp đồng; không đổi được loại đối tác" if has_any else None


def register_guards() -> None:
    """Gọi một lần khi tạo ứng dụng (create_app) – idempotent."""
    for guards, fn in (
        (md_service.CUSTOMER_ACCOUNT_GUARDS, _guard_customer_account),
        (md_service.ARCHIVE_GUARDS, _guard_archive),
        (md_service.TYPE_CHANGE_GUARDS, _guard_type_change),
    ):
        if fn not in guards:
            guards.append(fn)
    files.ACCESS_CHECKERS[FILE_OWNER] = _can_read_file
