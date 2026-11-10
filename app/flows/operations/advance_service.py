"""
Tạm ứng – hoàn ứng của nhân viên (02 §3.10.3 – Giai đoạn 20).

`requested` → `approved` (`manager` / `business_manager` khác nhân viên – thay phê duyệt `STAFF_ADVANCE` tới khi có
chính sách phê duyệt Luồng 2) → `disbursed` (sinh phiếu chi C08 đã ghi sổ; EV-115) → `settling` (nhân viên khai các
khoản đã chi kèm chứng từ → phiếu chi C01–C05 `offset` chờ duyệt) → `settled` (duyệt hoàn ứng: ghi sổ các phiếu
`offset`; thừa → phiếu thu T05 nhân viên nộp lại, thiếu → phiếu chi C08 bổ sung; công nợ tạm ứng về 0; EV-116).
`rejected` (lý do). Trả lại hoàn ứng → các phiếu `offset` bị hủy, về `disbursed`.

BR-115: mỗi nhân viên tối đa `OPS_ADVANCE_MAX_OPEN` khoản chưa hoàn ứng (mặc định 2) – vượt → 409. Hạn hoàn ứng mặc
định hôm nay + `OPS_ADVANCE_DUE_DAYS` (7); quá hạn chưa `settled` → Luồng 2 cảnh báo M-10 (không chặn – 02 §3.10.3).
Tạm ứng bằng VND. Mọi thao tác ghi `sys_audit_logs` (`staff_advance.<thao tác>`, trạng thái trước / sau).
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.numbering import next_number
from app.core.pagination import PageParams
from app.core.rbac import Role, forbidden
from app.core.timeutil import now_utc, today_vn
from app.flows.operations import cashbook_service as cb
from app.flows.operations import service
from app.flows.operations.cashbook_schemas import AdvanceCreate, DisburseIn, SettlementApproveIn, SettlementSubmitIn
from app.flows.operations.models import (
    OPEN_ADVANCE_STATUSES,
    AdvanceStatus,
    CashDirection,
    ContractStatus,
    CounterpartyType,
    OpsCashVoucher,
    OpsServiceContract,
    OpsStaffAdvance,
    PaymentMethod,
    VoucherStatus,
)
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import StaffAdvanceDisbursed, StaffAdvanceSettled
from app.shared.files import service as files
from app.shared.files.models import SysFile
from app.shared.identity.models import SysUser

ENTITY = "staff_advance"
FILE_OWNER = "staff_advance"
EXPENSE_ROOTS = ("C01", "C02", "C03", "C04", "C05")  # khoản hoàn ứng được khai (02 §3.10.3)
OPEN_ACCOUNTING_CONTRACT = (
    ContractStatus.ACTIVE.value,
    ContractStatus.SUSPENDED.value,
    ContractStatus.LIQUIDATING.value,
)


def _audit(a: OpsStaffAdvance) -> dict[str, Any]:
    return {
        "advance_no": a.advance_no,
        "staff_id": a.staff_id,
        "contract_id": a.contract_id,
        "shipment_id": a.shipment_id,
        "amount": str(a.amount),
        "due_date": a.due_date.isoformat(),
        "status": a.status,
    }


def _log(db: Session, action: str, a: OpsStaffAdvance, actor: SysUser, before: str | None, **extra: Any) -> None:
    audit.record(
        db,
        f"staff_advance.{action}",
        ENTITY,
        a.id,
        actor.id,
        {"status": before} if before else None,
        {"status": a.status, **extra},
    )


def _approver(a: OpsStaffAdvance, actor: SysUser, what: str) -> None:
    if not cb.is_approver(actor):
        raise forbidden(f"Chỉ manager / business_manager {what}")
    if actor.id == a.staff_id:
        raise forbidden(f"Người {what} phải khác nhân viên tạm ứng")


def _open_contract(db: Session, contract_id: int) -> OpsServiceContract:
    contract = service.get_contract(db, contract_id)
    if contract.status not in OPEN_ACCOUNTING_CONTRACT:
        raise cb.conflict(f"Hợp đồng đang {contract.status} – không tạm ứng / hoàn ứng được")
    return contract


def get_advance(
    db: Session, advance_id: int, actor: SysUser | None = None, for_update: bool = False
) -> OpsStaffAdvance:
    stmt = select(OpsStaffAdvance).where(OpsStaffAdvance.id == advance_id)
    a = db.scalar(stmt.with_for_update() if for_update else stmt)
    if a is None or (actor is not None and not _can_see(actor, a)):
        raise cb.not_found("tạm ứng")
    return a


def _can_see(actor: SysUser, a: OpsStaffAdvance) -> bool:
    roles = set(actor.role_codes)
    return a.staff_id == actor.id or bool(roles & {Role.MANAGER.value, Role.BUSINESS_MANAGER.value, Role.ADMIN.value})


def list_advances(
    db: Session,
    actor: SysUser,
    params: PageParams,
    staff_id: int | None = None,
    status: str | None = None,
    contract_id: int | None = None,
    overdue: bool | None = None,
) -> tuple[list[OpsStaffAdvance], int]:
    """BC-TC-05: `ops_staff` chỉ thấy tạm ứng của mình."""
    stmt = select(OpsStaffAdvance)
    if not set(actor.role_codes) & {Role.MANAGER.value, Role.BUSINESS_MANAGER.value, Role.ADMIN.value}:
        staff_id = actor.id
    for col, val in (
        (OpsStaffAdvance.staff_id, staff_id),
        (OpsStaffAdvance.status, status),
        (OpsStaffAdvance.contract_id, contract_id),
    ):
        if val is not None:
            stmt = stmt.where(col == val)
    if overdue is not None:
        cond = OpsStaffAdvance.status.in_(OPEN_ADVANCE_STATUSES[2:]) & (OpsStaffAdvance.due_date < today_vn())
        stmt = stmt.where(cond if overdue else ~cond)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(
        stmt.order_by(OpsStaffAdvance.id.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)
    )
    return list(items), total


def vouchers_of(db: Session, advance_id: int) -> list[OpsCashVoucher]:
    return list(
        db.scalars(select(OpsCashVoucher).where(OpsCashVoucher.advance_id == advance_id).order_by(OpsCashVoucher.id))
    )


def outstanding(db: Session, advance_id: int) -> Decimal:
    """Công nợ tạm ứng = chi C08 − hoàn ứng `offset` − thu T05 (phiếu `recorded`)."""
    total = Decimal("0.00")
    for v in vouchers_of(db, advance_id):
        if v.status != VoucherStatus.RECORDED.value:
            continue
        if v.voucher_type == CashDirection.RECEIPT.value or v.payment_method == PaymentMethod.OFFSET.value:
            total -= v.amount_base
        else:
            total += v.amount_base
    return total


def file_ids(db: Session, advance_id: int) -> list[int]:
    return list(
        db.scalars(
            select(SysFile.id)
            .where(SysFile.owner_type == FILE_OWNER, SysFile.owner_id == advance_id)
            .order_by(SysFile.id)
        )
    )


def detail(db: Session, a: OpsStaffAdvance) -> dict[str, Any]:
    return {
        "outstanding": outstanding(db, a.id),
        "overdue": a.status in OPEN_ADVANCE_STATUSES[2:] and a.due_date < today_vn(),
        "file_ids": file_ids(db, a.id),
        "vouchers": vouchers_of(db, a.id),
    }


def _check_files(db: Session, a: OpsStaffAdvance, ids: list[int], field: str) -> list[int]:
    owned = set(file_ids(db, a.id))
    missing = [i for i in ids if i not in owned]
    if missing:
        raise cb.invalid(field, f"File {missing} không thuộc tạm ứng {a.advance_no} (tải lên qua …/files)", "not_found")
    return list(dict.fromkeys(ids))


# --- Đề nghị, duyệt -------------------------------------------------------------------------------------------------


def request(db: Session, data: AdvanceCreate, actor: SysUser) -> OpsStaffAdvance:
    settings = get_settings()
    contract = _open_contract(db, data.contract_id)
    if data.currency != cb.BASE_CURRENCY:
        raise cb.invalid("body.currency", f"Tạm ứng bằng {cb.BASE_CURRENCY}")
    due = data.due_date or today_vn() + timedelta(days=settings.ops_advance_due_days)
    if due < today_vn():
        raise cb.invalid("body.due_date", "Hạn hoàn ứng không được ở quá khứ")
    if data.shipment_id is not None:
        cb._shipment_of(db, contract, data.shipment_id, "body.shipment_id")
    db.scalar(select(SysUser.id).where(SysUser.id == actor.id).with_for_update())  # tuần tự hóa BR-115 theo nhân viên
    n_open = db.scalar(
        select(func.count())
        .select_from(OpsStaffAdvance)
        .where(OpsStaffAdvance.staff_id == actor.id, OpsStaffAdvance.status.in_(OPEN_ADVANCE_STATUSES))
    )
    if n_open >= settings.ops_advance_max_open:
        raise AppError(
            ErrorCode.INVALID_STATE_TRANSITION,
            409,
            f"BR-115: đã có {n_open} khoản tạm ứng chưa hoàn ứng (tối đa {settings.ops_advance_max_open})",
        )
    a = OpsStaffAdvance(
        advance_no=next_number(db, "TU", today_vn().year),
        staff_id=actor.id,
        contract_id=contract.id,
        shipment_id=data.shipment_id,
        purpose=data.purpose,
        amount=data.amount,
        currency=data.currency,
        due_date=due,
        status=AdvanceStatus.REQUESTED.value,
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(a)
    db.flush()
    audit.record(db, "staff_advance.request", ENTITY, a.id, actor.id, after=_audit(a))
    db.commit()
    db.refresh(a)
    return a


def decide(db: Session, advance_id: int, approve: bool, note: str | None, actor: SysUser) -> OpsStaffAdvance:
    a = get_advance(db, advance_id, for_update=True)
    _approver(a, actor, "duyệt tạm ứng")
    if a.status != AdvanceStatus.REQUESTED.value:
        raise cb.conflict(f"Tạm ứng đang {a.status} – không duyệt / từ chối được")
    if not approve and not note:
        raise cb.invalid("body.note", "Từ chối phải có lý do", "required")
    a.status = AdvanceStatus.APPROVED.value if approve else AdvanceStatus.REJECTED.value
    a.approved_by, a.approved_at = (actor.id, now_utc()) if approve else (None, None)
    a.decision_note, a.updated_by = note, actor.id
    db.flush()
    _log(db, "approve" if approve else "reject", a, actor, "requested", note=note)
    db.commit()
    db.refresh(a)
    return a


def attach_file(db: Session, advance_id: int, upload: UploadFile, actor: SysUser) -> SysFile:
    a = get_advance(db, advance_id, actor, for_update=True)
    if a.staff_id != actor.id and not cb.is_approver(actor):
        raise forbidden("Chỉ nhân viên tạm ứng hoặc người duyệt tải chứng từ")
    if a.status in (AdvanceStatus.SETTLED.value, AdvanceStatus.REJECTED.value):
        raise cb.conflict(f"Tạm ứng đã {a.status}")
    stored = files.store(db, upload, "cash_document", "ops", FILE_OWNER, a.id, actor.id)
    audit.record(
        db,
        "staff_advance.attach_file",
        ENTITY,
        a.id,
        actor.id,
        after={"file_id": stored.id, "filename": stored.original_filename, "sha256": stored.sha256},
    )
    files.commit_or_discard(db, [stored])
    return stored


# --- Chi tạm ứng ----------------------------------------------------------------------------------------------------


def _staff_voucher(db: Session, a: OpsStaffAdvance, contract: OpsServiceContract, **fields: Any) -> OpsCashVoucher:
    staff = db.get(SysUser, a.staff_id)
    return cb.new_voucher(
        db,
        contract,
        advance_id=a.id,
        currency=a.currency,
        counterparty_type=fields.pop("counterparty_type", CounterpartyType.STAFF.value),
        counterparty_id=fields.pop("counterparty_id", a.staff_id),
        counterparty_name=fields.pop("counterparty_name", staff.full_name if staff else f"Nhân viên {a.staff_id}"),
        prepared_by=a.staff_id,
        **fields,
    )


def disburse(db: Session, advance_id: int, data: DisburseIn, actor: SysUser) -> OpsStaffAdvance:
    a = get_advance(db, advance_id, for_update=True)
    _approver(a, actor, "chi tạm ứng")
    if a.status != AdvanceStatus.APPROVED.value:
        raise cb.conflict(f"Tạm ứng đang {a.status} – chỉ chi khoản đã duyệt")
    contract = _open_contract(db, a.contract_id)
    vdate = data.voucher_date or today_vn()
    if vdate > today_vn():
        raise cb.invalid("body.voucher_date", "Ngày ghi nhận không được ở tương lai")
    now = now_utc()
    v = _staff_voucher(
        db,
        a,
        contract,
        voucher_type=CashDirection.PAYMENT.value,
        shipment_id=a.shipment_id,
        category_code="C08",
        description=f"Chi tạm ứng {a.advance_no}: {a.purpose}"[:1000],
        voucher_date=vdate,
        amount=a.amount,
        payment_method=data.payment_method,
        supporting_file_ids=_check_files(db, a, data.file_ids, "body.file_ids"),
        status=VoucherStatus.RECORDED.value,
        submitted_at=now,
        approved_by=a.approved_by,
        approved_at=a.approved_at,
        recorded_by=actor.id,
        recorded_at=now,
        created_by=actor.id,
        updated_by=actor.id,
    )
    audit.record(db, "cash_voucher.record", cb.ENTITY, v.id, actor.id, after=cb.voucher_audit(v))
    a.status, a.disbursed_by, a.disbursed_at, a.updated_by = AdvanceStatus.DISBURSED.value, actor.id, now, actor.id
    db.flush()
    _log(db, "disburse", a, actor, "approved", voucher_id=v.id, voucher_no=v.voucher_no)
    publisher.publish(
        db,
        StaffAdvanceDisbursed(
            advance_id=a.id,
            contract_id=a.contract_id,
            shipment_id=a.shipment_id,
            staff_id=a.staff_id,
            amount=a.amount,
            currency=a.currency,
            due_date=a.due_date,
        ),
        ENTITY,
        a.id,
        actor.id,
        contract.mode,
    )
    db.commit()
    db.refresh(a)
    return a


# --- Hoàn ứng -------------------------------------------------------------------------------------------------------


def submit_settlement(db: Session, advance_id: int, data: SettlementSubmitIn, actor: SysUser) -> OpsStaffAdvance:
    a = get_advance(db, advance_id, actor, for_update=True)
    if a.staff_id != actor.id:
        raise forbidden("Chỉ nhân viên tạm ứng khai hoàn ứng")
    if a.status != AdvanceStatus.DISBURSED.value:
        raise cb.conflict(f"Tạm ứng đang {a.status} – chỉ hoàn ứng khoản đã chi")
    contract = _open_contract(db, a.contract_id)
    today = today_vn()
    spent = Decimal("0.00")
    created = []
    for i, e in enumerate(data.expenses):
        field = f"body.expenses[{i}]"
        cat = cb.get_category(db, e.category_code)
        if cb.root_code(cat) not in EXPENSE_ROOTS:
            raise cb.invalid(f"{field}.category_code", f"Hoàn ứng chỉ khai khoản mục {', '.join(EXPENSE_ROOTS)}")
        cb._shipment_of(db, contract, e.shipment_id, f"{field}.shipment_id")
        edate = e.expense_date or today
        if edate > today:
            raise cb.invalid(f"{field}.expense_date", "Ngày chi không được ở tương lai")
        extra = (
            {
                "counterparty_type": CounterpartyType.SUPPLIER.value,
                "counterparty_id": None,
                "counterparty_name": e.counterparty_name,
            }
            if e.counterparty_name
            else {}
        )
        v = _staff_voucher(
            db,
            a,
            contract,
            voucher_type=CashDirection.PAYMENT.value,
            shipment_id=e.shipment_id or a.shipment_id,
            category_code=cat.code,
            description=e.description,
            voucher_date=edate,
            amount=e.amount,
            payment_method=PaymentMethod.OFFSET.value,
            supporting_file_ids=_check_files(db, a, e.file_ids, f"{field}.file_ids"),
            external_invoice_no=e.external_invoice_no,
            status=VoucherStatus.SUBMITTED.value,
            submitted_at=now_utc(),
            created_by=actor.id,
            updated_by=actor.id,
            **extra,
        )
        audit.record(db, "cash_voucher.create", cb.ENTITY, v.id, actor.id, after=cb.voucher_audit(v))
        spent += v.amount_base
        created.append(v.voucher_no)
    a.status, a.spent_total = AdvanceStatus.SETTLING.value, spent
    a.settlement_submitted_at, a.updated_by = now_utc(), actor.id
    db.flush()
    _log(db, "submit_settlement", a, actor, "disbursed", spent_total=str(spent), vouchers=created)
    db.commit()
    db.refresh(a)
    return a


def return_settlement(db: Session, advance_id: int, note: str, actor: SysUser) -> OpsStaffAdvance:
    a = get_advance(db, advance_id, for_update=True)
    _approver(a, actor, "trả lại hoàn ứng")
    if a.status != AdvanceStatus.SETTLING.value:
        raise cb.conflict(f"Tạm ứng đang {a.status} – không có hoàn ứng chờ duyệt")
    now = now_utc()
    for v in vouchers_of(db, a.id):
        if v.payment_method == PaymentMethod.OFFSET.value and v.status == VoucherStatus.SUBMITTED.value:
            v.status, v.void_reason, v.voided_by, v.voided_at = (
                VoucherStatus.VOID.value,
                f"Trả lại hoàn ứng: {note}"[:500],
                actor.id,
                now,
            )
            v.updated_by = actor.id
            audit.record(
                db,
                "cash_voucher.void",
                cb.ENTITY,
                v.id,
                actor.id,
                {"status": "submitted"},
                {"status": "void", "reason": v.void_reason},
            )
    a.status, a.spent_total, a.settlement_submitted_at = AdvanceStatus.DISBURSED.value, None, None
    a.decision_note, a.updated_by = note, actor.id
    db.flush()
    _log(db, "return_settlement", a, actor, "settling", note=note)
    db.commit()
    db.refresh(a)
    return a


def approve_settlement(db: Session, advance_id: int, data: SettlementApproveIn, actor: SysUser) -> OpsStaffAdvance:
    a = get_advance(db, advance_id, for_update=True)
    _approver(a, actor, "duyệt hoàn ứng")
    if a.status != AdvanceStatus.SETTLING.value:
        raise cb.conflict(f"Tạm ứng đang {a.status} – không có hoàn ứng chờ duyệt")
    contract = _open_contract(db, a.contract_id)
    now = now_utc()
    for v in vouchers_of(db, a.id):
        if v.payment_method == PaymentMethod.OFFSET.value and v.status == VoucherStatus.SUBMITTED.value:
            v.status, v.approved_by, v.approved_at = VoucherStatus.RECORDED.value, actor.id, now
            v.recorded_by, v.recorded_at, v.updated_by = actor.id, now, actor.id
            audit.record(
                db, "cash_voucher.record", cb.ENTITY, v.id, actor.id, {"status": "submitted"}, {"status": "recorded"}
            )
    db.flush()
    diff = outstanding(db, a.id)  # > 0: nhân viên còn giữ tiền (thừa); < 0: công ty còn nợ nhân viên (thiếu)
    adjustment = None
    if diff != 0:
        if data.payment_method is None or not data.file_ids:
            raise cb.invalid(
                "body.file_ids",
                f"Chênh lệch {diff} – cần phương thức và chứng từ "
                f"({'phiếu thu nhân viên nộp lại' if diff > 0 else 'phiếu chi bổ sung'})",
                "required",
            )
        receipt = diff > 0
        v = _staff_voucher(
            db,
            a,
            contract,
            voucher_type=CashDirection.RECEIPT.value if receipt else CashDirection.PAYMENT.value,
            shipment_id=a.shipment_id,
            category_code="T05" if receipt else "C08",
            description=(
                f"Nhân viên nộp lại tạm ứng thừa {a.advance_no}"
                if receipt
                else f"Chi bổ sung tạm ứng thiếu {a.advance_no}"
            ),
            voucher_date=today_vn(),
            amount=abs(diff),
            payment_method=data.payment_method,
            supporting_file_ids=_check_files(db, a, data.file_ids, "body.file_ids"),
            status=VoucherStatus.RECORDED.value,
            submitted_at=now,
            approved_by=actor.id,
            approved_at=now,
            recorded_by=actor.id,
            recorded_at=now,
            created_by=actor.id,
            updated_by=actor.id,
        )
        audit.record(db, "cash_voucher.record", cb.ENTITY, v.id, actor.id, after=cb.voucher_audit(v))
        adjustment = {"voucher_id": v.id, "voucher_no": v.voucher_no, "kind": "refund" if receipt else "top_up"}
        db.flush()
    left = outstanding(db, a.id)
    if left != 0:  # bất biến: hoàn ứng xong công nợ phải về 0
        raise AppError(ErrorCode.INTERNAL_ERROR, 500, f"Công nợ tạm ứng sau hoàn ứng còn {left}")
    a.status, a.settled_by, a.settled_at = AdvanceStatus.SETTLED.value, actor.id, now
    a.decision_note, a.updated_by = data.note or a.decision_note, actor.id
    db.flush()
    _log(
        db, "settle", a, actor, "settling", spent_total=str(a.spent_total), difference=str(diff), adjustment=adjustment
    )
    publisher.publish(db, StaffAdvanceSettled(advance_id=a.id, settled_at=now), ENTITY, a.id, actor.id, contract.mode)
    db.commit()
    db.refresh(a)
    return a


# --- Quyền tải file, giao diện đọc cho Luồng 2 ----------------------------------------------------------------------


def _can_read_file(db: Session, user, file: SysFile) -> bool:
    a = db.get(OpsStaffAdvance, file.owner_id)
    return a is not None and _can_see(user, a)


def register_file_access() -> None:
    files.ACCESS_CHECKERS[FILE_OWNER] = _can_read_file


def overdue(db: Session, today: date) -> list[OpsStaffAdvance]:
    return list(
        db.scalars(
            select(OpsStaffAdvance)
            .where(OpsStaffAdvance.status.in_(OPEN_ADVANCE_STATUSES[2:]), OpsStaffAdvance.due_date < today)
            .order_by(OpsStaffAdvance.id)
        )
    )
