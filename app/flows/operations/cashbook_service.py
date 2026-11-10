"""
Sổ thu – chi theo hợp đồng (02 §3.10 – Giai đoạn 20): khoản mục, phiếu thu / phiếu chi, số dư quỹ, sổ chi tiết.

Phiếu (`ops_cash_vouchers`): `ops_staff` / `manager` lập nháp (bắt buộc hợp đồng, khoản mục; ngày không ở tương lai;
số tiền > 0) → tải chứng từ gốc (≥ 1 file) → người lập `:submit` → phiếu chi: `manager` / `business_manager` khác người
lập `:approve` (BR-114) → `:record` (ghi sổ); phiếu thu: `:record` thẳng từ `submitted` (người ghi khác người lập).
`:reject` trả về nháp (ghi chú). `:void` (lý do) – phiếu đã ghi sổ không sửa / xóa, chỉ hủy và lập phiếu thay thế
(`replaces_id`) – BR-112, trigger CSDL giữ. Hợp đồng `closed`: chỉ phiếu tất toán số S (`settlement_id`). Khoản mục C08,
T05 và phương thức `offset` chỉ do luồng tạm ứng sinh (advance_service).

Số dư quỹ = tổng thu − tổng chi của phiếu `recorded` (quy đổi VND – `amount_base`), không tính phiếu bù trừ `offset`
(không có dòng tiền). Mọi chuyển trạng thái ghi `sys_audit_logs` (`cash_voucher.<thao tác>`, trạng thái trước / sau).
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.numbering import next_number
from app.core.pagination import PageParams
from app.core.rbac import Role, forbidden
from app.core.timeutil import now_utc, today_vn
from app.flows.operations import service
from app.flows.operations.cashbook_schemas import CategoryCreate, VoucherCreate
from app.flows.operations.models import (
    CashDirection,
    ContractMode,
    ContractStatus,
    CounterpartyType,
    MdCashbookCategory,
    OpsCashVoucher,
    OpsContractSettlement,
    OpsServiceContract,
    OpsShipment,
    PaymentMethod,
    VoucherStatus,
)
from app.shared.audit import service as audit
from app.shared.files import service as files
from app.shared.files.models import SysFile
from app.shared.identity.models import SysUser

BASE_CURRENCY = "VND"
ENTITY = "cash_voucher"
FILE_OWNER = "cash_voucher"
APPROVERS = {Role.MANAGER.value, Role.BUSINESS_MANAGER.value}
READERS = {Role.OPS_STAFF.value, Role.MANAGER.value, Role.BUSINESS_MANAGER.value, Role.ADMIN.value}
SYSTEM_ONLY_ROOTS = ("C08", "T05")  # chỉ sinh từ tạm ứng – hoàn ứng
CASH_METHODS = (PaymentMethod.CASH.value, PaymentMethod.BANK_TRANSFER.value)
AUDIT_FIELDS = (
    "voucher_no",
    "voucher_type",
    "contract_id",
    "shipment_id",
    "category_code",
    "voucher_date",
    "amount",
    "currency",
    "amount_base",
    "payment_method",
    "advance_id",
    "settlement_id",
    "replaces_id",
    "status",
)


def conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def not_found(what: str) -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, f"Không tìm thấy {what}")


def invalid(field: str, message: str, issue: str = "invalid") -> AppError:
    return AppError(ErrorCode.VALIDATION_ERROR, 422, message, [{"field": field, "issue": issue, "message": message}])


def money(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def is_approver(actor: SysUser) -> bool:
    return bool(APPROVERS & set(actor.role_codes))


def voucher_audit(v: OpsCashVoucher) -> dict[str, Any]:
    snap = audit.snapshot(v, AUDIT_FIELDS)
    for k in ("amount", "amount_base"):
        snap[k] = str(snap[k])
    return snap


# --- Khoản mục ------------------------------------------------------------------------------------------------------


def list_categories(db: Session) -> list[MdCashbookCategory]:
    return list(db.scalars(select(MdCashbookCategory).order_by(MdCashbookCategory.code)))


def get_category(db: Session, code: str) -> MdCashbookCategory:
    cat = db.scalar(select(MdCashbookCategory).where(MdCashbookCategory.code == code))
    if cat is None:
        raise invalid("body.category_code", f"Không có khoản mục {code}", "not_found")
    return cat


def root_code(cat: MdCashbookCategory) -> str:
    return cat.parent_code or cat.code


def create_category(db: Session, data: CategoryCreate, actor: SysUser) -> MdCashbookCategory:
    parent_code = data.code.split(".", 1)[0]
    parent = db.scalar(select(MdCashbookCategory).where(MdCashbookCategory.code == parent_code))
    if parent is None or not parent.is_system:
        raise invalid("body.code", f"Không có khoản mục gốc {parent_code}", "not_found")
    if parent.code in SYSTEM_ONLY_ROOTS:
        raise invalid("body.code", f"Không thêm khoản mục con cho {parent.code} (chỉ dùng cho tạm ứng – hoàn ứng)")
    if db.scalar(select(MdCashbookCategory.id).where(MdCashbookCategory.code == data.code)):
        raise AppError(ErrorCode.DUPLICATE, 409, f"Đã có khoản mục {data.code}")
    cat = MdCashbookCategory(
        code=data.code,
        name=data.name,
        direction=parent.direction,
        result_class=parent.result_class,
        parent_code=parent.code,
        is_system=False,
        created_by=actor.id,
    )
    db.add(cat)
    db.flush()
    audit.record(
        db,
        "cashbook_category.create",
        "cashbook_category",
        cat.id,
        actor.id,
        after={"code": cat.code, "name": cat.name, "parent_code": cat.parent_code, "direction": cat.direction},
    )
    db.commit()
    db.refresh(cat)
    return cat


def delete_category(db: Session, code: str, actor: SysUser) -> None:
    cat = db.scalar(select(MdCashbookCategory).where(MdCashbookCategory.code == code).with_for_update())
    if cat is None:
        raise not_found("khoản mục")
    if cat.is_system:
        raise conflict("Không xóa khoản mục gốc")
    if db.scalar(select(func.count()).select_from(OpsCashVoucher).where(OpsCashVoucher.category_code == code)):
        raise conflict("Khoản mục đã có phiếu – không xóa được")
    audit.record(
        db, "cashbook_category.delete", "cashbook_category", cat.id, actor.id, before={"code": code, "name": cat.name}
    )
    db.delete(cat)
    db.commit()


# --- Phiếu ----------------------------------------------------------------------------------------------------------


def _shipment_of(db: Session, contract: OpsServiceContract, shipment_id: int | None, field: str) -> None:
    if shipment_id is None:
        return
    s = db.get(OpsShipment, shipment_id)
    if s is None or s.contract_id != contract.id:
        raise invalid(field, "Lô hàng không thuộc hợp đồng", "not_found")


def next_voucher_no(db: Session, contract: OpsServiceContract, voucher_type: str) -> str:
    prefix = "PT" if voucher_type == CashDirection.RECEIPT.value else "PC"
    if contract.mode == ContractMode.TRAINING.value:
        prefix = f"TR-{prefix}"
    return next_number(db, prefix, today_vn().year, width=5)


def new_voucher(db: Session, contract: OpsServiceContract, **fields: Any) -> OpsCashVoucher:
    """Tạo phiếu (đã kiểm tra nghiệp vụ ở nơi gọi) – dùng chung cho API và luồng tạm ứng."""
    rate = Decimal(fields.pop("exchange_rate", Decimal("1")))
    v = OpsCashVoucher(
        voucher_no=next_voucher_no(db, contract, fields["voucher_type"]),
        contract_id=contract.id,
        exchange_rate=rate,
        amount_base=money(Decimal(fields["amount"]) * rate),
        supporting_file_ids=list(fields.pop("supporting_file_ids", [])),
        **fields,
    )
    db.add(v)
    db.flush()
    return v


def check_contract_open(contract: OpsServiceContract, settlement_id: int | None = None) -> None:
    if contract.status == ContractStatus.DRAFT.value:
        raise conflict("Hợp đồng còn nháp – chưa ghi thu – chi được")
    if contract.status == ContractStatus.CLOSED.value and settlement_id is None:
        raise conflict("BR-112: hợp đồng đã đóng – chỉ lập phiếu tất toán số S của bảng quyết toán")


def create_voucher(db: Session, data: VoucherCreate, actor: SysUser) -> OpsCashVoucher:
    contract = service.get_contract(db, data.contract_id)
    db.refresh(contract, with_for_update={"read": True})
    check_contract_open(contract, data.settlement_id)
    if data.voucher_date > today_vn():
        raise invalid("body.voucher_date", "Ngày ghi nhận không được ở tương lai")
    cat = get_category(db, data.category_code)
    if cat.direction != data.voucher_type:
        raise invalid("body.category_code", f"Khoản mục {cat.code} là khoản {cat.direction}, không dùng cho phiếu này")
    if root_code(cat) in SYSTEM_ONLY_ROOTS:
        raise invalid("body.category_code", f"Khoản mục {cat.code} chỉ sinh từ tạm ứng – hoàn ứng nhân viên")
    _shipment_of(db, contract, data.shipment_id, "body.shipment_id")
    if data.currency == BASE_CURRENCY and data.exchange_rate != 1:
        raise invalid("body.exchange_rate", "Phiếu VND có tỷ giá 1")
    if data.counterparty_type == CounterpartyType.STAFF and (
        data.counterparty_id is None or db.get(SysUser, data.counterparty_id) is None
    ):
        raise invalid("body.counterparty_id", "Nhân viên không tồn tại", "not_found")
    if data.settlement_id is not None:
        s = db.get(OpsContractSettlement, data.settlement_id)
        if s is None or s.contract_id != contract.id:
            raise invalid("body.settlement_id", "Bảng quyết toán không thuộc hợp đồng", "not_found")
    if data.replaces_id is not None:
        old = db.scalar(select(OpsCashVoucher).where(OpsCashVoucher.id == data.replaces_id).with_for_update())
        if old is None or old.contract_id != contract.id:
            raise invalid("body.replaces_id", "Phiếu được thay thế không thuộc hợp đồng", "not_found")
        if old.status != VoucherStatus.VOID.value:
            raise conflict("Chỉ lập phiếu thay thế cho phiếu đã hủy (void)")
        if db.scalar(select(OpsCashVoucher.id).where(OpsCashVoucher.replaces_id == old.id)):
            raise AppError(ErrorCode.DUPLICATE, 409, f"Phiếu {old.voucher_no} đã có phiếu thay thế")
    v = new_voucher(
        db,
        contract,
        status=VoucherStatus.DRAFT.value,
        prepared_by=actor.id,
        created_by=actor.id,
        updated_by=actor.id,
        **data.model_dump(exclude={"contract_id"}),
    )
    audit.record(db, "cash_voucher.create", ENTITY, v.id, actor.id, after=voucher_audit(v))
    db.commit()
    db.refresh(v)
    return v


def get_voucher(db: Session, voucher_id: int, for_update: bool = False) -> OpsCashVoucher:
    stmt = select(OpsCashVoucher).where(OpsCashVoucher.id == voucher_id)
    v = db.scalar(stmt.with_for_update() if for_update else stmt)
    if v is None:
        raise not_found("phiếu thu / chi")
    return v


def list_vouchers(
    db: Session,
    params: PageParams,
    contract_id: int | None = None,
    status: str | None = None,
    voucher_type: str | None = None,
    category_code: str | None = None,
    advance_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[list[OpsCashVoucher], int]:
    stmt = select(OpsCashVoucher)
    for col, val in (
        (OpsCashVoucher.contract_id, contract_id),
        (OpsCashVoucher.status, status),
        (OpsCashVoucher.voucher_type, voucher_type),
        (OpsCashVoucher.category_code, category_code),
        (OpsCashVoucher.advance_id, advance_id),
    ):
        if val is not None:
            stmt = stmt.where(col == val)
    if date_from:
        stmt = stmt.where(OpsCashVoucher.voucher_date >= date_from)
    if date_to:
        stmt = stmt.where(OpsCashVoucher.voucher_date <= date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(
        stmt.order_by(OpsCashVoucher.voucher_date.desc(), OpsCashVoucher.id.desc())
        .offset((params.page - 1) * params.page_size)
        .limit(params.page_size)
    )
    return list(items), total


def _transition(
    db: Session, v: OpsCashVoucher, action: str, actor: SysUser, before: str, extra: dict | None = None
) -> OpsCashVoucher:
    v.updated_by, v.updated_at = actor.id, now_utc()
    db.flush()
    audit.record(
        db, f"cash_voucher.{action}", ENTITY, v.id, actor.id, {"status": before}, {"status": v.status, **(extra or {})}
    )
    db.commit()
    db.refresh(v)
    return v


def _manual(v: OpsCashVoucher) -> None:
    if v.advance_id is not None:
        raise conflict("Phiếu sinh từ tạm ứng – xử lý qua tạm ứng (/staff-advances)")


def attach_file(db: Session, voucher_id: int, upload: UploadFile, actor: SysUser) -> SysFile:
    v = get_voucher(db, voucher_id, for_update=True)
    _manual(v)
    if v.status != VoucherStatus.DRAFT.value:
        raise conflict(f"Phiếu đang {v.status} – chỉ thêm chứng từ khi nháp")
    if v.prepared_by != actor.id and not is_approver(actor):
        raise forbidden("Chỉ người lập phiếu thêm chứng từ")
    stored = files.store(db, upload, "cash_document", "ops", FILE_OWNER, v.id, actor.id)
    v.supporting_file_ids = [*v.supporting_file_ids, stored.id]
    v.updated_by = actor.id
    db.flush()
    audit.record(
        db,
        "cash_voucher.attach_file",
        ENTITY,
        v.id,
        actor.id,
        after={"file_id": stored.id, "filename": stored.original_filename, "sha256": stored.sha256},
    )
    files.commit_or_discard(db, [stored])
    return stored


def submit(db: Session, voucher_id: int, actor: SysUser) -> OpsCashVoucher:
    v = get_voucher(db, voucher_id, for_update=True)
    _manual(v)
    if v.prepared_by != actor.id:
        raise forbidden("Chỉ người lập gửi phiếu")
    if v.status != VoucherStatus.DRAFT.value:
        raise conflict(f"Phiếu đang {v.status} – chỉ gửi phiếu nháp")
    if not v.supporting_file_ids:
        raise invalid("supporting_file_ids", "Phiếu phải có ≥ 1 chứng từ gốc (bản scan) trước khi gửi", "required")
    check_contract_open(db.get(OpsServiceContract, v.contract_id), v.settlement_id)
    v.status, v.submitted_at = VoucherStatus.SUBMITTED.value, now_utc()
    return _transition(db, v, "submit", actor, "draft")


def approve(db: Session, voucher_id: int, note: str | None, actor: SysUser) -> OpsCashVoucher:
    v = get_voucher(db, voucher_id, for_update=True)
    _manual(v)
    if v.voucher_type != CashDirection.PAYMENT.value:
        raise conflict("Phiếu thu không cần duyệt – ghi sổ trực tiếp (:record)")
    if v.status != VoucherStatus.SUBMITTED.value:
        raise conflict(f"Phiếu đang {v.status} – chỉ duyệt phiếu đã gửi")
    if actor.id == v.prepared_by:
        raise forbidden("Người duyệt phải khác người lập phiếu (BR-114)")
    v.status, v.approved_by, v.approved_at, v.decision_note = VoucherStatus.APPROVED.value, actor.id, now_utc(), note
    return _transition(db, v, "approve", actor, "submitted", {"note": note})


def record(db: Session, voucher_id: int, actor: SysUser) -> OpsCashVoucher:
    v = get_voucher(db, voucher_id, for_update=True)
    _manual(v)
    needed = (
        VoucherStatus.SUBMITTED.value if v.voucher_type == CashDirection.RECEIPT.value else VoucherStatus.APPROVED.value
    )
    if v.status != needed:
        hint = "" if v.voucher_type == CashDirection.RECEIPT.value else " (phiếu chi phải được duyệt trước – BR-114)"
        raise conflict(f"Phiếu đang {v.status} – chưa ghi sổ được{hint}")
    if actor.id == v.prepared_by:
        raise forbidden("Người ghi sổ phải khác người lập phiếu")
    check_contract_open(db.get(OpsServiceContract, v.contract_id), v.settlement_id)
    before = v.status
    v.status, v.recorded_by, v.recorded_at = VoucherStatus.RECORDED.value, actor.id, now_utc()
    return _transition(db, v, "record", actor, before, {"amount_base": str(v.amount_base)})


def reject(db: Session, voucher_id: int, note: str, actor: SysUser) -> OpsCashVoucher:
    v = get_voucher(db, voucher_id, for_update=True)
    _manual(v)
    if v.status not in (VoucherStatus.SUBMITTED.value, VoucherStatus.APPROVED.value):
        raise conflict(f"Phiếu đang {v.status} – không trả lại được")
    before = v.status
    v.status, v.decision_note = VoucherStatus.DRAFT.value, note
    v.submitted_at = v.approved_by = v.approved_at = None
    return _transition(db, v, "reject", actor, before, {"note": note})


def void(db: Session, voucher_id: int, reason: str, actor: SysUser) -> OpsCashVoucher:
    """BR-112: hủy phiếu (kể cả đã ghi sổ) kèm lý do; sửa sai = lập phiếu thay thế `replaces_id`."""
    v = get_voucher(db, voucher_id, for_update=True)
    _manual(v)
    if v.status == VoucherStatus.VOID.value:
        raise conflict("Phiếu đã hủy")
    before = v.status
    v.status, v.void_reason, v.voided_by, v.voided_at = VoucherStatus.VOID.value, reason, actor.id, now_utc()
    return _transition(db, v, "void", actor, before, {"reason": reason})


# --- Số dư, sổ chi tiết ---------------------------------------------------------------------------------------------


def balance(db: Session, contract_id: int | None = None) -> dict[str, Any]:
    if contract_id is not None:
        service.get_contract(db, contract_id)
    stmt = select(
        OpsCashVoucher.payment_method, OpsCashVoucher.voucher_type, func.count(), func.sum(OpsCashVoucher.amount_base)
    ).where(OpsCashVoucher.status == VoucherStatus.RECORDED.value)
    if contract_id is not None:
        stmt = stmt.where(OpsCashVoucher.contract_id == contract_id)
    zero = Decimal("0.00")
    by_method = {m: {"receipts": zero, "payments": zero, "balance": zero} for m in CASH_METHODS}
    offset_total, count = zero, 0
    for method, vtype, n, total in db.execute(
        stmt.group_by(OpsCashVoucher.payment_method, OpsCashVoucher.voucher_type)
    ):
        count += n
        if method == PaymentMethod.OFFSET.value:
            offset_total += total
            continue
        key = "receipts" if vtype == CashDirection.RECEIPT.value else "payments"
        by_method[method][key] += total
    for m in by_method.values():
        m["balance"] = m["receipts"] - m["payments"]
    receipts = sum((m["receipts"] for m in by_method.values()), zero)
    payments = sum((m["payments"] for m in by_method.values()), zero)
    return {
        "base_currency": BASE_CURRENCY,
        "contract_id": contract_id,
        "receipts": receipts,
        "payments": payments,
        "balance": receipts - payments,
        "by_method": by_method,
        "offset_total": offset_total,
        "voucher_count": count,
    }


def ledger(
    db: Session,
    contract_id: int,
    shipment_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """BC-TC-01: phiếu `recorded` của hợp đồng theo ngày; số dư lũy kế theo dòng tiền (`offset` không đổi số dư)."""
    service.get_contract(db, contract_id)
    stmt = select(OpsCashVoucher).where(
        OpsCashVoucher.contract_id == contract_id, OpsCashVoucher.status == VoucherStatus.RECORDED.value
    )
    if shipment_id is not None:
        stmt = stmt.where(OpsCashVoucher.shipment_id == shipment_id)
    if date_from:
        stmt = stmt.where(OpsCashVoucher.voucher_date >= date_from)
    if date_to:
        stmt = stmt.where(OpsCashVoucher.voucher_date <= date_to)
    running = receipts = payments = Decimal("0.00")
    rows = []
    for v in db.scalars(stmt.order_by(OpsCashVoucher.voucher_date, OpsCashVoucher.id)):
        cash = v.payment_method != PaymentMethod.OFFSET.value
        if cash:
            if v.voucher_type == CashDirection.RECEIPT.value:
                receipts += v.amount_base
                running += v.amount_base
            else:
                payments += v.amount_base
                running -= v.amount_base
        rows.append(
            {
                "voucher_id": v.id,
                "voucher_no": v.voucher_no,
                "voucher_date": v.voucher_date,
                "voucher_type": v.voucher_type,
                "category_code": v.category_code,
                "shipment_id": v.shipment_id,
                "counterparty_name": v.counterparty_name,
                "description": v.description,
                "payment_method": v.payment_method,
                "amount_base": v.amount_base,
                "affects_cash": cash,
                "running_balance": running,
            }
        )
    return {
        "contract_id": contract_id,
        "base_currency": BASE_CURRENCY,
        "rows": rows,
        "receipts": receipts,
        "payments": payments,
        "balance": receipts - payments,
    }


# --- Quyền tải file -------------------------------------------------------------------------------------------------


def _can_read_file(db: Session, user, file: SysFile) -> bool:
    return bool(READERS & set(user.role_codes)) and db.get(OpsCashVoucher, file.owner_id) is not None


def register_file_access() -> None:
    files.ACCESS_CHECKERS[FILE_OWNER] = _can_read_file
