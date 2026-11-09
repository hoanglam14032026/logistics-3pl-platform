"""
Vòng đời hợp đồng dịch vụ – Giai đoạn 19: phụ lục (duyệt 2 người), chấm dứt trước hạn, quyết toán BR-110.

Phụ lục (02 §3.7, rút gọn): `ops_staff` / `manager` lập (hợp đồng `active` / `suspended`) → `pending_approval` →
người duyệt 1 (`manager` / `business_manager`) → `pending_second_approval` → người duyệt 2 khác người lập và người duyệt
1 → áp vào hợp đồng (`service.create_version` – `contract_version + 1`, bản chụp mới, bản cũ giữ nguyên), `effective`,
EV-103. Từ chối (lý do) / người lập rút. Hợp đồng đã đổi phiên bản kể từ khi lập phụ lục → 409 (lập phụ lục mới).

Chấm dứt trước hạn (02 §3.9.2): `manager` đề xuất (lý do, ngày chấm dứt) cho hợp đồng `active` / `suspended` còn hạn;
`business_manager` duyệt / từ chối. Duyệt → lô chưa bắt đầu thực hiện (`draft`, `docs_collecting`, `docs_verified`)
tự hủy (lý do chấm dứt hợp đồng); lô `in_execution` / `delivered` ghi lại để hoàn tất trước khi đóng; hợp đồng
`liquidating`, EV-104. `:start-liquidation` không còn cho chấm dứt trước hạn không qua duyệt.

Quyết toán `final` (02 §3.9, BR-110): `manager` lập khi hợp đồng `expired` / `liquidating`, mọi lô đóng / hủy, không còn
phụ lục / đề xuất chấm dứt đang xử lý. Tổng hợp hóa đơn thương mại của hợp đồng + KPI (Luồng 4 –
`assessment.public.get_kpi_values`, BR-05; Luồng 4 tắt → `kpi_snapshot` null). `:submit` → `business_manager` duyệt
(khác người lập) → `approved`, sự kiện `ops.settlement.approved` (Luồng 4) / trả lại (lý do). `:close` cần quyết
toán đã duyệt. Mọi thao tác ghi nhật ký.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.numbering import next_number
from app.core.rbac import Role, forbidden
from app.core.timeutil import now_utc, today_vn
from app.flows.operations import service
from app.flows.operations.models import (
    OPEN_AMENDMENT_STATUSES,
    TERMINAL_SHIPMENT_STATUSES,
    AmendmentStatus,
    ContractStatus,
    OpsCommercialInvoice,
    OpsContractAmendment,
    OpsContractSettlement,
    OpsContractTermination,
    OpsServiceContract,
    OpsShipment,
    SettlementStatus,
    ShipmentStatus,
    TerminationStatus,
)
from app.flows.operations.schemas import AmendmentCreate, VersionCreate
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import ContractAmended, ContractLiquidationStarted, SettlementApproved
from app.shared.identity.models import SysUser

APPROVERS = {Role.MANAGER.value, Role.BUSINESS_MANAGER.value}
NOT_STARTED = (ShipmentStatus.DRAFT.value, ShipmentStatus.DOCS_COLLECTING.value, ShipmentStatus.DOCS_VERIFIED.value)
ENTITY = "service_contract"


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _not_found(what: str) -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, f"Không tìm thấy {what}")


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, message, [{"field": field, "issue": "invalid", "message": message}]
    )


def _money(v) -> str:
    return str(Decimal(v).quantize(Decimal("0.01")))


# --- Phụ lục ----------------------------------------------------------------------------------------------------


def amendment_audit(a: OpsContractAmendment) -> dict[str, Any]:
    return {
        "amendment_no": a.amendment_no,
        "status": a.status,
        "base_contract_version": a.base_contract_version,
        "effective_from": a.effective_from.isoformat(),
        "changes": a.changes,
    }


def list_amendments(db: Session, contract_id: int) -> list[OpsContractAmendment]:
    service.get_contract(db, contract_id)
    return list(
        db.scalars(
            select(OpsContractAmendment)
            .where(OpsContractAmendment.contract_id == contract_id)
            .order_by(OpsContractAmendment.id)
        )
    )


def create_amendment(db: Session, contract_id: int, data: AmendmentCreate, actor: SysUser) -> OpsContractAmendment:
    contract = service.get_contract(db, contract_id, for_update=True)
    if contract.status not in (ContractStatus.ACTIVE.value, ContractStatus.SUSPENDED.value):
        raise _conflict(f"Chỉ lập phụ lục cho hợp đồng đang hiệu lực / tạm dừng (đang {contract.status})")
    if contract.mode != "production":
        raise _conflict("Phụ lục chỉ cho hợp đồng production")
    _dry_run(db, contract, data, actor)  # kiểm tra quy tắc như khi áp (bản scan, ngày hiệu lực, biểu giá ⊂ phạm vi…)
    count = db.scalar(
        select(func.count()).select_from(OpsContractAmendment).where(OpsContractAmendment.contract_id == contract.id)
    )
    amendment = OpsContractAmendment(
        contract_id=contract.id,
        amendment_no=f"PL-{count + 1:02d}",
        base_contract_version=contract.contract_version,
        changes=data.changes(),
        effective_from=data.effective_from,
        reason=data.reason,
        attachment_id=data.attachment_id,
        status=AmendmentStatus.PENDING_APPROVAL.value,
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(amendment)
    db.flush()
    audit.record(
        db,
        "amendment.create",
        "contract_amendment",
        amendment.id,
        actor.id,
        after={"contract_id": contract.id, **amendment_audit(amendment)},
    )
    db.commit()
    db.refresh(amendment)
    return amendment


def _version_body(a_changes: dict, no: str, reason: str, effective_from, attachment_id) -> VersionCreate:
    return VersionCreate.model_validate(
        {
            **a_changes,
            "amendment_ref": no,
            "reason": reason,
            "effective_from": effective_from,
            "attachment_id": attachment_id,
        }
    )


def _dry_run(db: Session, contract: OpsServiceContract, data: AmendmentCreate, actor: SysUser) -> None:
    """Áp thử trong SAVEPOINT rồi hoàn tác – lỗi quy tắc báo ngay khi lập phụ lục."""
    sp = db.begin_nested()
    try:
        body = _version_body(data.changes(), "PL-TEST", data.reason, data.effective_from, data.attachment_id)
        service.create_version(db, contract.id, body, actor.id, None, commit=False)
    finally:
        sp.rollback()


def _amendment(db: Session, amendment_id: int) -> tuple[OpsContractAmendment, OpsServiceContract]:
    a = db.scalar(select(OpsContractAmendment).where(OpsContractAmendment.id == amendment_id).with_for_update())
    if a is None:
        raise _not_found("phụ lục")
    contract = service.get_contract(db, a.contract_id, for_update=True)
    return a, contract


def approve_amendment(db: Session, amendment_id: int, note: str | None, actor: SysUser) -> OpsContractAmendment:
    """Duyệt 2 người: lần 1 → `pending_second_approval`; lần 2 (khác người lập, khác người duyệt 1) → áp vào hợp
    đồng."""
    if not APPROVERS & set(actor.role_codes):
        raise forbidden("Chỉ manager / business_manager duyệt phụ lục")
    a, contract = _amendment(db, amendment_id)
    before = a.status
    now = now_utc()
    if a.status == AmendmentStatus.PENDING_APPROVAL.value:
        a.status, a.first_approved_by, a.first_approved_at = (
            AmendmentStatus.PENDING_SECOND_APPROVAL.value,
            actor.id,
            now,
        )
        action = "amendment.approve_first"
    elif a.status == AmendmentStatus.PENDING_SECOND_APPROVAL.value:
        if actor.id in (a.created_by, a.first_approved_by):
            raise forbidden("Người duyệt thứ 2 phải khác người lập và người duyệt thứ nhất")
        if contract.contract_version != a.base_contract_version:
            raise _conflict(
                f"Hợp đồng đã sang phiên bản {contract.contract_version} (phụ lục lập trên phiên bản "
                f"{a.base_contract_version}) – lập phụ lục mới"
            )
        body = _version_body(a.changes, a.amendment_no, a.reason, a.effective_from, a.attachment_id)
        version = service.create_version(db, contract.id, body, actor.id, None, commit=False)
        a.status, a.second_approved_by, a.second_approved_at = AmendmentStatus.EFFECTIVE.value, actor.id, now
        a.applied_version = version.contract_version
        action = "amendment.approve_second"
        current = service.children(db, contract)
        publisher.publish(
            db,
            ContractAmended(
                contract_id=contract.id,
                amendment_id=a.id,
                amendment_no=a.amendment_no,
                contract_version=version.contract_version,
                changed_groups=sorted(a.changes),
                service_scope=[s.model_dump(mode="json") for s in current["service_scopes"]],
                sla={
                    s.sla_code: s.model_dump(mode="json", exclude={"sla_code", "penalty_rule"})
                    for s in current["sla_terms"]
                },
                valid_to=contract.valid_to,
                effective_from=a.effective_from,
            ),
            ENTITY,
            contract.id,
            actor.id,
            contract.mode,
        )
    else:
        raise _conflict(f"Phụ lục đang {a.status} – không duyệt được")
    a.decision_note, a.updated_by = note, actor.id
    db.flush()
    audit.record(
        db,
        action,
        "contract_amendment",
        a.id,
        actor.id,
        {"status": before},
        {"status": a.status, "note": note, "applied_version": a.applied_version},
    )
    db.commit()
    db.refresh(a)
    return a


def reject_amendment(db: Session, amendment_id: int, note: str, actor: SysUser) -> OpsContractAmendment:
    if not APPROVERS & set(actor.role_codes):
        raise forbidden("Chỉ manager / business_manager từ chối phụ lục")
    a, _ = _amendment(db, amendment_id)
    if a.status not in OPEN_AMENDMENT_STATUSES:
        raise _conflict(f"Phụ lục đang {a.status} – không từ chối được")
    before = a.status
    a.status, a.decision_note, a.updated_by = AmendmentStatus.REJECTED.value, note, actor.id
    db.flush()
    audit.record(
        db,
        "amendment.reject",
        "contract_amendment",
        a.id,
        actor.id,
        {"status": before},
        {"status": a.status, "note": note},
    )
    db.commit()
    db.refresh(a)
    return a


def withdraw_amendment(db: Session, amendment_id: int, actor: SysUser) -> OpsContractAmendment:
    a, _ = _amendment(db, amendment_id)
    if a.created_by != actor.id:
        raise forbidden("Chỉ người lập rút phụ lục")
    if a.status not in OPEN_AMENDMENT_STATUSES:
        raise _conflict(f"Phụ lục đang {a.status} – không rút được")
    before = a.status
    a.status, a.updated_by = AmendmentStatus.WITHDRAWN.value, actor.id
    db.flush()
    audit.record(
        db, "amendment.withdraw", "contract_amendment", a.id, actor.id, {"status": before}, {"status": a.status}
    )
    db.commit()
    db.refresh(a)
    return a


# --- Chấm dứt trước hạn -----------------------------------------------------------------------------------------


def list_terminations(db: Session, contract_id: int) -> list[OpsContractTermination]:
    service.get_contract(db, contract_id)
    return list(
        db.scalars(
            select(OpsContractTermination)
            .where(OpsContractTermination.contract_id == contract_id)
            .order_by(OpsContractTermination.id)
        )
    )


def request_termination(
    db: Session, contract_id: int, reason: str, effective_date: date, actor: SysUser
) -> OpsContractTermination:
    contract = service.get_contract(db, contract_id, for_update=True)
    if contract.status not in (ContractStatus.ACTIVE.value, ContractStatus.SUSPENDED.value):
        raise _conflict(f"Chỉ đề xuất chấm dứt hợp đồng đang hiệu lực / tạm dừng (đang {contract.status})")
    if not (today_vn() <= effective_date < contract.valid_to):
        raise _invalid("body.effective_date", "Ngày chấm dứt phải từ hôm nay tới trước ngày hết hạn hợp đồng")
    pending = db.scalar(
        select(OpsContractTermination.id).where(
            OpsContractTermination.contract_id == contract.id, OpsContractTermination.status == "pending"
        )
    )
    if pending:
        raise AppError(ErrorCode.DUPLICATE, 409, "Đã có đề xuất chấm dứt đang chờ duyệt")
    t = OpsContractTermination(
        contract_id=contract.id, reason=reason, effective_date=effective_date, requested_by=actor.id
    )
    db.add(t)
    db.flush()
    audit.record(
        db,
        "termination.request",
        "contract_termination",
        t.id,
        actor.id,
        after={"contract_id": contract.id, "reason": reason, "effective_date": effective_date.isoformat()},
    )
    db.commit()
    db.refresh(t)
    return t


def _termination(db: Session, termination_id: int) -> tuple[OpsContractTermination, OpsServiceContract]:
    t = db.scalar(select(OpsContractTermination).where(OpsContractTermination.id == termination_id).with_for_update())
    if t is None:
        raise _not_found("đề xuất chấm dứt")
    contract = service.get_contract(db, t.contract_id, for_update=True)
    if t.status != TerminationStatus.PENDING.value:
        raise _conflict(f"Đề xuất đã {t.status}")
    return t, contract


def decide_termination(
    db: Session, termination_id: int, approve: bool, note: str | None, actor: SysUser
) -> OpsContractTermination:
    from app.flows.operations import shipment_service

    t, contract = _termination(db, termination_id)
    if actor.id == t.requested_by:
        raise forbidden("Người duyệt phải khác người đề xuất")
    t.decided_by, t.decided_at, t.decision_note = actor.id, now_utc(), note
    if not approve:
        t.status = TerminationStatus.REJECTED.value
        db.flush()
        audit.record(
            db,
            "termination.reject",
            "contract_termination",
            t.id,
            actor.id,
            {"status": "pending"},
            {"status": t.status, "note": note},
        )
        db.commit()
        db.refresh(t)
        return t
    if contract.status not in (ContractStatus.ACTIVE.value, ContractStatus.SUSPENDED.value):
        raise _conflict(f"Hợp đồng đang {contract.status} – không chấm dứt được")
    shipments = list(
        db.scalars(
            select(OpsShipment)
            .where(OpsShipment.contract_id == contract.id, OpsShipment.status.not_in(TERMINAL_SHIPMENT_STATUSES))
            .order_by(OpsShipment.id)
            .with_for_update()
        )
    )
    reason = f"Chấm dứt hợp đồng {contract.contract_no} trước hạn: {t.reason}"[:500]
    cancelled, still_open = [], []
    for s in shipments:
        if s.status in NOT_STARTED:
            shipment_service.system_cancel(db, s, reason, actor.id)
            cancelled.append(s.id)
        else:
            still_open.append(s.id)
    t.status = TerminationStatus.APPROVED.value
    t.cancelled_shipment_ids, t.open_shipment_ids = cancelled, still_open
    before = {"status": contract.status, "status_reason": contract.status_reason}
    contract.status = ContractStatus.LIQUIDATING.value
    contract.status_reason = f"Chấm dứt trước hạn từ {t.effective_date.isoformat()}: {t.reason}"[:500]
    contract.liquidation_started_at = now_utc()
    contract.updated_by, contract.updated_at = actor.id, now_utc()
    db.flush()
    audit.record(
        db,
        "termination.approve",
        "contract_termination",
        t.id,
        actor.id,
        {"status": "pending"},
        {"status": t.status, "note": note, "cancelled_shipment_ids": cancelled, "open_shipment_ids": still_open},
    )
    audit.record(
        db,
        "contract.terminate",
        ENTITY,
        contract.id,
        actor.id,
        before,
        {"status": contract.status, "status_reason": contract.status_reason, "termination_id": t.id},
    )
    publisher.publish(
        db,
        ContractLiquidationStarted(contract_id=contract.id, reason="terminated", termination_id=t.id),
        ENTITY,
        contract.id,
        actor.id,
        contract.mode,
    )
    db.commit()
    db.refresh(t)
    return t


# --- Quyết toán BR-110 ------------------------------------------------------------------------------------------


def br110_problems(db: Session, contract: OpsServiceContract) -> list[tuple[str, str]]:
    problems = []
    if contract.status not in (ContractStatus.EXPIRED.value, ContractStatus.LIQUIDATING.value):
        problems.append(("status", f"Hợp đồng phải hết hạn hoặc đang thanh lý (đang {contract.status})"))
    open_ships = list(
        db.scalars(
            select(OpsShipment.shipment_no).where(
                OpsShipment.contract_id == contract.id, OpsShipment.status.not_in(TERMINAL_SHIPMENT_STATUSES)
            )
        )
    )
    if open_ships:
        problems.append(("shipments", f"Còn lô hàng chưa đóng / hủy: {', '.join(open_ships)}"))
    if db.scalar(
        select(func.count())
        .select_from(OpsContractAmendment)
        .where(
            OpsContractAmendment.contract_id == contract.id, OpsContractAmendment.status.in_(OPEN_AMENDMENT_STATUSES)
        )
    ):
        problems.append(("amendments", "Còn phụ lục đang chờ duyệt"))
    if db.scalar(
        select(func.count())
        .select_from(OpsContractTermination)
        .where(OpsContractTermination.contract_id == contract.id, OpsContractTermination.status == "pending")
    ):
        problems.append(("terminations", "Còn đề xuất chấm dứt đang chờ duyệt"))
    return problems


def _period_to(db: Session, contract: OpsServiceContract) -> date:
    approved = db.scalar(
        select(OpsContractTermination.effective_date).where(
            OpsContractTermination.contract_id == contract.id, OpsContractTermination.status == "approved"
        )
    )
    end = approved or contract.valid_to
    return min(end, today_vn())


def _kpi_snapshot(db: Session, contract: OpsServiceContract, period_from: date, period_to: date) -> dict | None:
    if "assessment" not in get_settings().enabled_flows:
        return None  # N6: Luồng 4 tắt – giảm trừ nhập tay
    from app.flows.assessment import public as assessment_public

    return assessment_public.get_kpi_values(db, contract.id, period_from, period_to)


def _recompute(db: Session, s: OpsContractSettlement, contract: OpsServiceContract, data: dict[str, Any]) -> None:
    for k in ("currency", "sla_deduction", "deduction_note", "compensation", "paid_amount"):
        if data.get(k) is not None:
            setattr(s, k, data[k])
    if s.sla_deduction and not s.deduction_note:
        raise _invalid("body.deduction_note", "Giảm trừ SLA phải có căn cứ (deduction_note)")
    s.period_from, s.period_to = contract.valid_from, _period_to(db, contract)
    totals: dict[str, Decimal] = {}
    count = 0
    for cur, n, total in db.execute(
        select(OpsCommercialInvoice.currency, func.count(), func.sum(OpsCommercialInvoice.total_amount))
        .where(OpsCommercialInvoice.contract_id == contract.id)
        .group_by(OpsCommercialInvoice.currency)
    ):
        totals[cur] = total
        if cur == s.currency:
            count = n
    s.invoice_count = count
    s.invoice_total = totals.pop(s.currency, Decimal("0"))
    s.other_currency_totals = {k: _money(v) for k, v in sorted(totals.items())}
    s.balance_due = s.invoice_total - Decimal(s.sla_deduction) - Decimal(s.compensation) - Decimal(s.paid_amount)
    s.kpi_snapshot = _kpi_snapshot(db, contract, s.period_from, s.period_to)


def _settlement_audit(s: OpsContractSettlement) -> dict[str, Any]:
    return {
        "status": s.status,
        "currency": s.currency,
        "invoice_count": s.invoice_count,
        "invoice_total": _money(s.invoice_total),
        "other_currency_totals": s.other_currency_totals,
        "sla_deduction": _money(s.sla_deduction),
        "compensation": _money(s.compensation),
        "paid_amount": _money(s.paid_amount),
        "balance_due": _money(s.balance_due),
        "below_target": (s.kpi_snapshot or {}).get("below_target"),
    }


def get_settlement(db: Session, contract_id: int) -> OpsContractSettlement:
    service.get_contract(db, contract_id)
    s = db.scalar(select(OpsContractSettlement).where(OpsContractSettlement.contract_id == contract_id))
    if s is None:
        raise _not_found("quyết toán")
    return s


def create_settlement(db: Session, contract_id: int, data: dict[str, Any], actor: SysUser) -> OpsContractSettlement:
    contract = service.get_contract(db, contract_id, for_update=True)
    problems = br110_problems(db, contract)
    if problems:
        raise service._requirements_error("Chưa đủ điều kiện lập quyết toán (BR-110)", problems)
    if db.scalar(select(OpsContractSettlement.id).where(OpsContractSettlement.contract_id == contract.id)):
        raise AppError(ErrorCode.DUPLICATE, 409, "Hợp đồng đã có bảng quyết toán – sửa bảng hiện có")
    s = OpsContractSettlement(
        settlement_no=next_number(db, "QT", today_vn().year),
        contract_id=contract.id,
        currency=data.get("currency") or "VND",
        invoice_count=0,
        invoice_total=Decimal("0"),
        other_currency_totals={},
        balance_due=Decimal("0"),
        sla_deduction=Decimal("0"),
        compensation=Decimal("0"),
        paid_amount=Decimal("0"),
        prepared_by=actor.id,
        created_by=actor.id,
        updated_by=actor.id,
        period_from=contract.valid_from,
        period_to=contract.valid_to,
    )
    _recompute(db, s, contract, {**data, "currency": data.get("currency") or "VND"})
    db.add(s)
    db.flush()
    audit.record(db, "settlement.create", "contract_settlement", s.id, actor.id, after=_settlement_audit(s))
    db.commit()
    db.refresh(s)
    return s


def _settlement_for_update(db: Session, contract_id: int) -> tuple[OpsContractSettlement, OpsServiceContract]:
    contract = service.get_contract(db, contract_id, for_update=True)
    s = db.scalar(
        select(OpsContractSettlement).where(OpsContractSettlement.contract_id == contract.id).with_for_update()
    )
    if s is None:
        raise _not_found("quyết toán")
    return s, contract


def update_settlement(db: Session, contract_id: int, data: dict[str, Any], actor: SysUser) -> OpsContractSettlement:
    s, contract = _settlement_for_update(db, contract_id)
    if s.status not in (SettlementStatus.DRAFT.value, SettlementStatus.REJECTED.value):
        raise _conflict(f"Quyết toán đang {s.status} – không sửa được")
    before = _settlement_audit(s)
    _recompute(db, s, contract, data)
    s.status, s.updated_by = SettlementStatus.DRAFT.value, actor.id
    db.flush()
    old, new = audit.changed(before, _settlement_audit(s))
    audit.record(db, "settlement.update", "contract_settlement", s.id, actor.id, old, new)
    db.commit()
    db.refresh(s)
    return s


def submit_settlement(db: Session, contract_id: int, actor: SysUser) -> OpsContractSettlement:
    s, contract = _settlement_for_update(db, contract_id)
    if s.status != SettlementStatus.DRAFT.value:
        raise _conflict(f"Quyết toán đang {s.status} – chỉ gửi duyệt bản nháp")
    problems = br110_problems(db, contract)
    if problems:
        raise service._requirements_error("Chưa đủ điều kiện quyết toán (BR-110)", problems)
    _recompute(db, s, contract, {})  # số liệu mới nhất lúc gửi duyệt
    s.status, s.submitted_at, s.updated_by = SettlementStatus.PENDING_APPROVAL.value, now_utc(), actor.id
    db.flush()
    audit.record(
        db, "settlement.submit", "contract_settlement", s.id, actor.id, {"status": "draft"}, _settlement_audit(s)
    )
    db.commit()
    db.refresh(s)
    return s


def decide_settlement(
    db: Session, contract_id: int, approve: bool, note: str | None, actor: SysUser
) -> OpsContractSettlement:
    s, contract = _settlement_for_update(db, contract_id)
    if s.status != SettlementStatus.PENDING_APPROVAL.value:
        raise _conflict(f"Quyết toán đang {s.status} – không duyệt / trả lại được")
    if actor.id == s.prepared_by:
        raise forbidden("Người duyệt phải khác người lập quyết toán")
    s.decision_note, s.updated_by = note, actor.id
    if approve:
        s.status, s.approved_by, s.approved_at = SettlementStatus.APPROVED.value, actor.id, now_utc()
        publisher.publish(
            db,
            SettlementApproved(
                settlement_id=s.id,
                settlement_no=s.settlement_no,
                contract_id=contract.id,
                currency=s.currency,
                invoice_count=s.invoice_count,
                invoice_total=s.invoice_total,
                sla_deduction=s.sla_deduction,
                compensation=s.compensation,
                paid_amount=s.paid_amount,
                balance_due=s.balance_due,
                period_from=s.period_from,
                period_to=s.period_to,
                approved_by=actor.id,
            ),
            "contract_settlement",
            s.id,
            actor.id,
            contract.mode,
        )
    else:
        s.status = SettlementStatus.REJECTED.value
    db.flush()
    audit.record(
        db,
        "settlement.approve" if approve else "settlement.reject",
        "contract_settlement",
        s.id,
        actor.id,
        {"status": "pending_approval"},
        {"status": s.status, "note": note},
    )
    db.commit()
    db.refresh(s)
    return s


def has_approved_settlement(db: Session, contract_id: int) -> bool:
    return bool(
        db.scalar(
            select(OpsContractSettlement.id).where(
                OpsContractSettlement.contract_id == contract_id,
                OpsContractSettlement.status == SettlementStatus.APPROVED.value,
            )
        )
    )
