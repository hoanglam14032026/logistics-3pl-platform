"""
Bảng kê đối soát định kỳ – SOA (02 §3.9 `settlement_type = periodic_statement`, §6.5 – Giai đoạn 20).

Một bảng kê cho mỗi **khách hàng + tháng** (hợp đồng `production` của khách hàng), lưu ở `ops_contract_settlements`
(`contract_id` NULL, `customer_id`):
- Số dư đầu kỳ = số dư cuối kỳ của bảng kê tháng trước (bản đang sống – không `disputed`; không có → 0).
- Phát sinh tăng = hóa đơn thương mại (`ops_commercial_invoices`) có ngày hóa đơn trong tháng – theo đồng tiền bảng kê;
  đồng tiền khác liệt kê riêng, không quy đổi (như quyết toán Giai đoạn 19).
- Phát sinh giảm = phiếu thu T01 + T03 − phiếu chi C07 đã ghi sổ trong tháng (cùng đồng tiền).
- Số dư cuối kỳ = đầu kỳ + tăng − giảm (CHECK CSDL).

`draft` (`ops_staff` / `manager` lập, tính lại được) → `sent` (`manager` / `business_manager` gửi – số liệu chốt) →
`confirmed` / `disputed` → `closed`. Xác nhận / khiếu nại: `customer` tự bấm cho bảng kê của chính mình (`response_via =
customer`), hoặc `manager` ghi nhận thay khi khách báo ngoài hệ thống (bắt buộc ghi chú – `response_via = staff`).
`confirmed` / `closed` → khóa hóa đơn thương mại của kỳ (service + trigger CSDL). `disputed` không khóa; phát hành lại =
`:reissue` tạo **bản mới** (`previous_id`), bản cũ giữ nguyên (trigger chặn sửa). Mọi thao tác ghi `sys_audit_logs`
(`statement.<thao tác>`). Xuất CSV + trang HTML để in (không làm PDF).
"""

import csv
import html
import io
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.numbering import next_number
from app.core.pagination import PageParams
from app.core.rbac import Role, forbidden
from app.core.timeutil import now_utc, today_vn
from app.flows.operations import cashbook_service as cb
from app.flows.operations.cashbook_schemas import StatementCreate
from app.flows.operations.models import (
    LOCKING_STATEMENT_STATUSES,
    CashDirection,
    ContractMode,
    MdCashbookCategory,
    OpsCashVoucher,
    OpsCommercialInvoice,
    OpsContractSettlement,
    OpsServiceContract,
    SettlementType,
    StatementStatus,
    VoucherStatus,
)
from app.shared.audit import service as audit
from app.shared.identity.models import SysUser
from app.shared.masterdata.models import MdParty

ENTITY = "statement"
PERIODIC = SettlementType.PERIODIC_STATEMENT.value
DECREASE_RECEIPT_ROOTS = ("T01", "T03")
DECREASE_PAYMENT_ROOTS = ("C07",)
SEND_ROLES = {Role.MANAGER.value, Role.BUSINESS_MANAGER.value}


def _month(value: str) -> tuple[date, date]:
    y, m = (int(x) for x in value.split("-"))
    first = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def _prev_month_first(first: date) -> date:
    return (first - timedelta(days=1)).replace(day=1)


def _m(v) -> str:
    return str(cb.money(Decimal(v)))


def _audit(s: OpsContractSettlement) -> dict[str, Any]:
    return {
        "settlement_no": s.settlement_no,
        "customer_id": s.customer_id,
        "period_from": s.period_from.isoformat(),
        "currency": s.currency,
        "opening_balance": _m(s.opening_balance),
        "invoice_count": s.invoice_count,
        "invoice_total": _m(s.invoice_total),
        "paid_amount": _m(s.paid_amount),
        "balance_due": _m(s.balance_due),
        "status": s.status,
        "previous_id": s.previous_id,
    }


# --- Tính số liệu ---------------------------------------------------------------------------------------------------


def _roots(db: Session, roots: tuple[str, ...]) -> list[str]:
    return list(
        db.scalars(
            select(MdCashbookCategory.code).where(
                or_(MdCashbookCategory.code.in_(roots), MdCashbookCategory.parent_code.in_(roots))
            )
        )
    )


def _compute(db: Session, s: OpsContractSettlement) -> None:
    prod_contract = (OpsServiceContract.customer_id == s.customer_id) & (
        OpsServiceContract.mode == ContractMode.PRODUCTION.value
    )
    invoices = db.execute(
        select(OpsCommercialInvoice, OpsServiceContract.contract_no)
        .join(OpsServiceContract, OpsServiceContract.id == OpsCommercialInvoice.contract_id)
        .where(prod_contract, OpsCommercialInvoice.invoice_date.between(s.period_from, s.period_to))
        .order_by(OpsCommercialInvoice.invoice_date, OpsCommercialInvoice.id)
    ).all()
    inv_lines, others = [], {}
    total = Decimal("0.00")
    for inv, contract_no in invoices:
        if inv.currency == s.currency:
            total += inv.total_amount
            inv_lines.append(
                {
                    "invoice_id": inv.id,
                    "invoice_no": inv.invoice_no,
                    "invoice_date": inv.invoice_date.isoformat(),
                    "contract_no": contract_no,
                    "shipment_id": inv.shipment_id,
                    "seller_name": inv.seller_name,
                    "amount": _m(inv.total_amount),
                }
            )
        else:
            others[inv.currency] = others.get(inv.currency, Decimal("0")) + inv.total_amount
    receipt_codes = _roots(db, DECREASE_RECEIPT_ROOTS)
    refund_codes = _roots(db, DECREASE_PAYMENT_ROOTS)
    vouchers = db.execute(
        select(OpsCashVoucher, OpsServiceContract.contract_no)
        .join(OpsServiceContract, OpsServiceContract.id == OpsCashVoucher.contract_id)
        .where(
            prod_contract,
            OpsCashVoucher.status == VoucherStatus.RECORDED.value,
            OpsCashVoucher.currency == s.currency,
            OpsCashVoucher.voucher_date.between(s.period_from, s.period_to),
            or_(
                (OpsCashVoucher.voucher_type == CashDirection.RECEIPT.value)
                & OpsCashVoucher.category_code.in_(receipt_codes),
                (OpsCashVoucher.voucher_type == CashDirection.PAYMENT.value)
                & OpsCashVoucher.category_code.in_(refund_codes),
            ),
        )
        .order_by(OpsCashVoucher.voucher_date, OpsCashVoucher.id)
    ).all()
    pay_lines = []
    paid = Decimal("0.00")
    for v, contract_no in vouchers:
        sign = 1 if v.voucher_type == CashDirection.RECEIPT.value else -1
        paid += sign * v.amount
        pay_lines.append(
            {
                "voucher_id": v.id,
                "voucher_no": v.voucher_no,
                "voucher_date": v.voucher_date.isoformat(),
                "contract_no": contract_no,
                "category_code": v.category_code,
                "description": v.description,
                "amount": _m(sign * v.amount),
            }
        )
    prev = db.scalar(
        select(OpsContractSettlement).where(
            OpsContractSettlement.settlement_type == PERIODIC,
            OpsContractSettlement.customer_id == s.customer_id,
            OpsContractSettlement.period_from == _prev_month_first(s.period_from),
            OpsContractSettlement.status != StatementStatus.DISPUTED.value,
        )
    )
    opening = prev.balance_due if prev is not None and prev.currency == s.currency else Decimal("0.00")
    s.opening_balance = opening
    s.invoice_count, s.invoice_total = len(inv_lines), total
    s.other_currency_totals = {k: _m(v) for k, v in sorted(others.items())}
    s.paid_amount = paid
    s.balance_due = opening + total - paid
    s.statement_lines = {
        "opening_from": prev.settlement_no if prev is not None else None,
        "invoices": inv_lines,
        "payments": pay_lines,
    }


# --- Truy vấn -------------------------------------------------------------------------------------------------------


def get(db: Session, statement_id: int, for_update: bool = False) -> OpsContractSettlement:
    stmt = select(OpsContractSettlement).where(
        OpsContractSettlement.id == statement_id, OpsContractSettlement.settlement_type == PERIODIC
    )
    s = db.scalar(stmt.with_for_update() if for_update else stmt)
    if s is None:
        raise cb.not_found("bảng kê đối soát")
    return s


def get_for_customer(db: Session, party_id: int | None, statement_id: int) -> OpsContractSettlement:
    """BR-111: bảng kê của khách hàng khác hoặc còn nháp → 404 như không tồn tại."""
    s = db.scalar(
        select(OpsContractSettlement).where(
            OpsContractSettlement.id == statement_id, OpsContractSettlement.settlement_type == PERIODIC
        )
    )
    if s is None or party_id is None or s.customer_id != party_id or s.status == StatementStatus.DRAFT.value:
        raise cb.not_found("bảng kê đối soát")
    return s


def list_statements(
    db: Session,
    params: PageParams,
    customer_id: int | None = None,
    status: str | None = None,
    month: str | None = None,
    portal: bool = False,
) -> tuple[list[OpsContractSettlement], int]:
    stmt = select(OpsContractSettlement).where(OpsContractSettlement.settlement_type == PERIODIC)
    if customer_id is not None:
        stmt = stmt.where(OpsContractSettlement.customer_id == customer_id)
    if status is not None:
        stmt = stmt.where(OpsContractSettlement.status == status)
    if month is not None:
        stmt = stmt.where(OpsContractSettlement.period_from == _month(month)[0])
    if portal:
        stmt = stmt.where(OpsContractSettlement.status != StatementStatus.DRAFT.value)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(
        stmt.order_by(OpsContractSettlement.period_from.desc(), OpsContractSettlement.id.desc())
        .offset((params.page - 1) * params.page_size)
        .limit(params.page_size)
    )
    return list(items), total


# --- Lập, tính lại, gửi ---------------------------------------------------------------------------------------------


def create(db: Session, data: StatementCreate, actor: SysUser) -> OpsContractSettlement:
    party = db.get(MdParty, data.customer_id)
    if party is None:
        raise cb.invalid("body.customer_id", "Không có khách hàng này", "not_found")
    first, last = _month(data.month)
    if last >= today_vn():
        raise cb.invalid("body.month", "Chỉ lập bảng kê cho tháng đã kết thúc")
    db.scalar(select(MdParty.id).where(MdParty.id == party.id).with_for_update())  # tuần tự theo khách hàng
    rows = list(
        db.scalars(
            select(OpsContractSettlement).where(
                OpsContractSettlement.settlement_type == PERIODIC,
                OpsContractSettlement.customer_id == party.id,
                OpsContractSettlement.period_from == first,
            )
        )
    )
    live = [r for r in rows if r.status != StatementStatus.DISPUTED.value]
    if live:
        raise AppError(ErrorCode.DUPLICATE, 409, f"Tháng {data.month} đã có bảng kê {live[0].settlement_no}")
    if rows:
        raise cb.conflict(f"Bảng kê {rows[-1].settlement_no} đang bị khiếu nại – phát hành lại bằng :reissue")
    s = _new(db, party.id, first, last, data.currency, actor, None)
    audit.record(db, "statement.create", ENTITY, s.id, actor.id, after=_audit(s))
    db.commit()
    db.refresh(s)
    return s


def _new(
    db: Session, customer_id: int, first: date, last: date, currency: str, actor: SysUser, previous_id: int | None
) -> OpsContractSettlement:
    s = OpsContractSettlement(
        settlement_no=next_number(db, "BK", today_vn().year),
        settlement_type=PERIODIC,
        contract_id=None,
        customer_id=customer_id,
        period_from=first,
        period_to=last,
        currency=currency,
        invoice_count=0,
        invoice_total=Decimal("0"),
        other_currency_totals={},
        opening_balance=Decimal("0"),
        paid_amount=Decimal("0"),
        balance_due=Decimal("0"),
        sla_deduction=Decimal("0"),
        compensation=Decimal("0"),
        status=StatementStatus.DRAFT.value,
        previous_id=previous_id,
        prepared_by=actor.id,
        created_by=actor.id,
        updated_by=actor.id,
    )
    _compute(db, s)
    db.add(s)
    db.flush()
    return s


def recompute(db: Session, statement_id: int, actor: SysUser) -> OpsContractSettlement:
    s = get(db, statement_id, for_update=True)
    if s.status != StatementStatus.DRAFT.value:
        raise cb.conflict(f"Bảng kê đang {s.status} – chỉ tính lại bản nháp")
    before = _audit(s)
    _compute(db, s)
    s.updated_by = actor.id
    db.flush()
    old, new = audit.changed(before, _audit(s))
    audit.record(db, "statement.recompute", ENTITY, s.id, actor.id, old, new)
    db.commit()
    db.refresh(s)
    return s


def send(db: Session, statement_id: int, actor: SysUser) -> OpsContractSettlement:
    s = get(db, statement_id, for_update=True)
    if s.status != StatementStatus.DRAFT.value:
        raise cb.conflict(f"Bảng kê đang {s.status} – chỉ gửi bản nháp")
    _compute(db, s)  # số liệu mới nhất lúc gửi – từ đây không đổi (trigger)
    s.status, s.sent_by, s.sent_at, s.updated_by = StatementStatus.SENT.value, actor.id, now_utc(), actor.id
    db.flush()
    audit.record(db, "statement.send", ENTITY, s.id, actor.id, {"status": "draft"}, _audit(s))
    db.commit()
    db.refresh(s)
    return s


# --- Khách hàng xác nhận / khiếu nại --------------------------------------------------------------------------------


def respond(
    db: Session, statement_id: int, confirm: bool, reason: str | None, note: str | None, actor: SysUser
) -> OpsContractSettlement:
    """Cùng một API cho `customer` (bảng kê của chính mình) và `manager` (ghi nhận thay – bắt buộc ghi chú)."""
    roles = set(actor.role_codes)
    is_customer = Role.CUSTOMER.value in roles
    if is_customer:
        get_for_customer(db, actor.party_id, statement_id)
        via = "customer"
    elif Role.MANAGER.value in roles:
        if not note:
            raise cb.invalid("body.note", "Ghi nhận thay khách hàng phải có ghi chú phản hồi của khách", "required")
        via = "staff"
    else:
        raise forbidden()
    s = get(db, statement_id, for_update=True)
    if s.status != StatementStatus.SENT.value:
        raise cb.conflict(f"Bảng kê đang {s.status} – chỉ xác nhận / khiếu nại bản đã gửi")
    now = now_utc()
    s.responded_by, s.responded_at, s.response_via, s.response_note = actor.id, now, via, note
    if confirm:
        s.status = StatementStatus.CONFIRMED.value
        s.customer_confirmed_by, s.customer_confirmed_at = actor.id, now
    else:
        s.status, s.dispute_reason = StatementStatus.DISPUTED.value, reason
    s.updated_by = actor.id
    db.flush()
    audit.record(
        db,
        "statement.confirm" if confirm else "statement.dispute",
        ENTITY,
        s.id,
        actor.id,
        {"status": "sent"},
        {"status": s.status, "response_via": via, "note": note, "reason": reason},
    )
    db.commit()
    db.refresh(s)
    return s


def close(db: Session, statement_id: int, actor: SysUser) -> OpsContractSettlement:
    s = get(db, statement_id, for_update=True)
    if s.status != StatementStatus.CONFIRMED.value:
        raise cb.conflict(f"Bảng kê đang {s.status} – chỉ đóng bản đã xác nhận")
    s.status, s.closed_by, s.closed_at, s.updated_by = StatementStatus.CLOSED.value, actor.id, now_utc(), actor.id
    db.flush()
    audit.record(db, "statement.close", ENTITY, s.id, actor.id, {"status": "confirmed"}, {"status": s.status})
    db.commit()
    db.refresh(s)
    return s


def reissue(db: Session, statement_id: int, actor: SysUser) -> OpsContractSettlement:
    """Bản `disputed` giữ nguyên; tạo bản nháp mới cùng khách hàng, kỳ, đồng tiền (`previous_id`), tính lại số liệu."""
    old = get(db, statement_id, for_update=True)
    if old.status != StatementStatus.DISPUTED.value:
        raise cb.conflict(f"Bảng kê đang {old.status} – chỉ phát hành lại bản bị khiếu nại")
    successor = db.scalar(
        select(OpsContractSettlement.settlement_no).where(OpsContractSettlement.previous_id == old.id)
    )
    if successor:
        raise AppError(ErrorCode.DUPLICATE, 409, f"Bảng kê {old.settlement_no} đã được phát hành lại: {successor}")
    s = _new(db, old.customer_id, old.period_from, old.period_to, old.currency, actor, old.id)
    audit.record(db, "statement.reissue", ENTITY, old.id, actor.id, {"status": old.status}, {"reissued_as": s.id})
    audit.record(db, "statement.create", ENTITY, s.id, actor.id, after=_audit(s))
    db.commit()
    db.refresh(s)
    return s


# --- Khóa hóa đơn thương mại (guard thêm vào invoice_service – Giai đoạn 10) ----------------------------------------


def locked_by(db: Session, contract_id: int, invoice_date: date) -> str | None:
    """Số bảng kê `confirmed` / `closed` của khách hàng có kỳ chứa `invoice_date` (hợp đồng production) – None: mở."""
    return db.scalar(
        select(OpsContractSettlement.settlement_no)
        .join(OpsServiceContract, OpsServiceContract.customer_id == OpsContractSettlement.customer_id)
        .where(
            OpsServiceContract.id == contract_id,
            OpsServiceContract.mode == ContractMode.PRODUCTION.value,
            OpsContractSettlement.settlement_type == PERIODIC,
            OpsContractSettlement.status.in_(LOCKING_STATEMENT_STATUSES),
            OpsContractSettlement.period_from <= invoice_date,
            OpsContractSettlement.period_to >= invoice_date,
        )
        .limit(1)
    )


# --- Xuất CSV / trang in --------------------------------------------------------------------------------------------


def _summary(db: Session, s: OpsContractSettlement) -> list[tuple[str, str]]:
    party = db.get(MdParty, s.customer_id)
    return [
        ("Bảng kê", s.settlement_no),
        ("Khách hàng", party.name if party else str(s.customer_id)),
        ("Mã số thuế", (party.tax_code or "") if party else ""),
        ("Kỳ", f"{s.period_from.isoformat()} – {s.period_to.isoformat()}"),
        ("Đồng tiền", s.currency),
        ("Trạng thái", s.status),
        ("Số dư đầu kỳ", _m(s.opening_balance)),
        ("Phát sinh tăng (hóa đơn)", _m(s.invoice_total)),
        ("Phát sinh giảm (thanh toán)", _m(s.paid_amount)),
        ("Số dư cuối kỳ", _m(s.balance_due)),
    ]


def export_csv(db: Session, s: OpsContractSettlement) -> str:
    buf = io.StringIO()
    buf.write("﻿")  # BOM – Excel đọc đúng UTF-8
    w = csv.writer(buf)
    for k, v in _summary(db, s):
        w.writerow([k, v])
    for cur, amount in (s.other_currency_totals or {}).items():
        w.writerow([f"Hóa đơn đồng tiền khác ({cur}) – không quy đổi", amount])
    lines = s.statement_lines or {}
    w.writerow([])
    w.writerow(["Loại", "Số", "Ngày", "Hợp đồng", "Nội dung", "Số tiền"])
    for i in lines.get("invoices", []):
        w.writerow(["Hóa đơn", i["invoice_no"], i["invoice_date"], i["contract_no"], i["seller_name"], i["amount"]])
    for p in lines.get("payments", []):
        w.writerow(["Thanh toán", p["voucher_no"], p["voucher_date"], p["contract_no"], p["description"], p["amount"]])
    return buf.getvalue()


def render_html(db: Session, s: OpsContractSettlement) -> str:
    e = html.escape
    summary = "".join(f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>" for k, v in _summary(db, s))
    others = "".join(
        f"<tr><th>Hóa đơn {e(c)} (không quy đổi)</th><td>{e(a)}</td></tr>"
        for c, a in (s.other_currency_totals or {}).items()
    )
    lines = s.statement_lines or {}
    body = "".join(
        f"<tr><td>Hóa đơn</td><td>{e(i['invoice_no'])}</td><td>{e(i['invoice_date'])}</td>"
        f"<td>{e(i['contract_no'])}</td><td>{e(i['seller_name'])}</td><td class='n'>{e(i['amount'])}</td></tr>"
        for i in lines.get("invoices", [])
    ) + "".join(
        f"<tr><td>Thanh toán</td><td>{e(p['voucher_no'])}</td><td>{e(p['voucher_date'])}</td>"
        f"<td>{e(p['contract_no'])}</td><td>{e(p['description'])}</td><td class='n'>{e(p['amount'])}</td></tr>"
        for p in lines.get("payments", [])
    )
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><title>Bảng kê đối soát {e(s.settlement_no)}</title>
<style>
body{{font-family:Arial,sans-serif;font-size:13px;margin:24px;color:#000;background:#fff}}
h1{{font-size:18px}} table{{border-collapse:collapse;width:100%;margin-bottom:16px}}
th,td{{border:1px solid #888;padding:4px 6px;text-align:left}} td.n{{text-align:right}}
.sign{{display:flex;justify-content:space-around;margin-top:48px}} .sign div{{text-align:center;width:40%}}
@media print{{body{{margin:0}} button{{display:none}}}}
</style></head><body>
<button onclick="window.print()">In</button>
<h1>BẢNG KÊ ĐỐI SOÁT CÔNG NỢ – {e(s.settlement_no)}</h1>
<table>{summary}{others}</table>
<table><thead><tr><th>Loại</th><th>Số</th><th>Ngày</th><th>Hợp đồng</th><th>Nội dung</th><th>Số tiền</th></tr></thead>
<tbody>{body}</tbody></table>
<div class="sign"><div>ĐẠI DIỆN KHÁCH HÀNG<br><br><br><br>(ký, ghi rõ họ tên)</div>
<div>ĐẠI DIỆN CÔNG TY<br><br><br><br>(ký, ghi rõ họ tên)</div></div>
</body></html>"""
