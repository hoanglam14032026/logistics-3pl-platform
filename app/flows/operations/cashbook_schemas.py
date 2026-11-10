"""Schemas Giai đoạn 20: khoản mục thu – chi, phiếu thu / chi, tạm ứng nhân viên, bảng kê đối soát (02 §3.10, §3.9)."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.timeutil import UtcDatetime
from app.flows.operations.models import CounterpartyType

Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
Text300 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Text1000 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
CashMethod = Literal["cash", "bank_transfer"]
FileIds = Annotated[list[Annotated[int, Field(gt=0)]], Field(min_length=1, max_length=20)]


# --- Khoản mục ----------------------------------------------------------------------------------------------------


class CategoryCreate(BaseModel):
    """Khoản mục con của một khoản mục gốc (vd. `C03.1` dưới `C03`) – kế thừa chiều thu / chi, cách tính kết quả."""

    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, StringConstraints(pattern=r"^[TC][0-9]{2}\.[0-9]{1,2}$")]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    direction: str
    result_class: str
    parent_code: str | None
    is_system: bool


# --- Phiếu thu / chi ----------------------------------------------------------------------------------------------


class VoucherCreate(BaseModel):
    """Lập phiếu nháp. Chứng từ gốc tải lên sau (`POST …/{id}/files`) – bắt buộc ≥ 1 file trước khi gửi."""

    model_config = ConfigDict(extra="forbid")

    voucher_type: Literal["receipt", "payment"]
    contract_id: int = Field(gt=0)
    shipment_id: int | None = Field(None, gt=0)
    category_code: Annotated[str, StringConstraints(max_length=10)]
    counterparty_type: CounterpartyType
    counterparty_id: int | None = Field(None, gt=0)
    counterparty_name: Text300
    description: Text1000
    voucher_date: date
    amount: Amount
    currency: Currency = "VND"
    exchange_rate: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)] = Decimal("1")
    payment_method: CashMethod
    external_invoice_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    settlement_id: int | None = Field(None, gt=0, description="Bảng quyết toán (tất toán số S – BR-112)")
    replaces_id: int | None = Field(None, gt=0, description="Phiếu đã hủy (void) mà phiếu này thay thế")


class VoucherOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    voucher_no: str
    voucher_type: str
    contract_id: int
    shipment_id: int | None
    category_code: str
    counterparty_type: str
    counterparty_id: int | None
    counterparty_name: str
    description: str
    voucher_date: date
    amount: Decimal
    currency: str
    exchange_rate: Decimal
    amount_base: Decimal
    payment_method: str
    supporting_file_ids: list[int]
    external_invoice_no: str | None
    advance_id: int | None
    settlement_id: int | None
    replaces_id: int | None
    status: str
    prepared_by: int
    submitted_at: UtcDatetime | None
    approved_by: int | None
    approved_at: UtcDatetime | None
    recorded_by: int | None
    recorded_at: UtcDatetime | None
    decision_note: str | None
    void_reason: str | None
    voided_by: int | None
    voided_at: UtcDatetime | None
    row_version: int


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: Reason | None = None


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Reason


class MethodBalance(BaseModel):
    receipts: Decimal
    payments: Decimal
    balance: Decimal


class BalanceOut(BaseModel):
    """Số dư quỹ = tổng thu − tổng chi của phiếu `recorded` (quy đổi VND), không tính phiếu bù trừ `offset`."""

    base_currency: str
    contract_id: int | None
    receipts: Decimal
    payments: Decimal
    balance: Decimal
    by_method: dict[str, MethodBalance]
    offset_total: Decimal
    voucher_count: int


class LedgerRow(BaseModel):
    voucher_id: int
    voucher_no: str
    voucher_date: date
    voucher_type: str
    category_code: str
    shipment_id: int | None
    counterparty_name: str
    description: str
    payment_method: str
    amount_base: Decimal
    affects_cash: bool
    running_balance: Decimal


class LedgerOut(BaseModel):
    """BC-TC-01 – sổ chi tiết thu – chi theo hợp đồng (phiếu `recorded`)."""

    contract_id: int
    base_currency: str
    rows: list[LedgerRow]
    receipts: Decimal
    payments: Decimal
    balance: Decimal


# --- Tạm ứng ------------------------------------------------------------------------------------------------------


class AdvanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: int = Field(gt=0)
    shipment_id: int | None = Field(None, gt=0)
    purpose: Text1000
    amount: Amount
    currency: Currency = "VND"
    due_date: date | None = Field(None, description="Hạn hoàn ứng – mặc định hôm nay + OPS_ADVANCE_DUE_DAYS")


class DisburseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_method: CashMethod
    file_ids: FileIds = Field(description="File đã tải lên tạm ứng (phiếu chi / ủy nhiệm chi có chữ ký)")
    voucher_date: date | None = None


class ExpenseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_code: Annotated[str, StringConstraints(max_length=10)]
    shipment_id: int | None = Field(None, gt=0)
    description: Text1000
    amount: Amount
    expense_date: date | None = None
    counterparty_name: Text300 | None = None
    external_invoice_no: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    file_ids: FileIds


class SettlementSubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expenses: Annotated[list[ExpenseIn], Field(min_length=1, max_length=50)]


class SettlementApproveIn(BaseModel):
    """Chênh lệch ≠ 0 → bắt buộc phương thức + file (biên lai nhân viên nộp lại / phiếu chi bổ sung)."""

    model_config = ConfigDict(extra="forbid")

    payment_method: CashMethod | None = None
    file_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=20)
    note: Reason | None = None


class AdvanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    advance_no: str
    staff_id: int
    contract_id: int
    shipment_id: int | None
    purpose: str
    amount: Decimal
    currency: str
    due_date: date
    status: str
    approved_by: int | None
    approved_at: UtcDatetime | None
    decision_note: str | None
    disbursed_by: int | None
    disbursed_at: UtcDatetime | None
    settlement_submitted_at: UtcDatetime | None
    spent_total: Decimal | None
    settled_by: int | None
    settled_at: UtcDatetime | None
    row_version: int


class AdvanceDetailOut(AdvanceOut):
    outstanding: Decimal = Field(description="Công nợ tạm ứng còn lại = chi C08 − hoàn ứng (offset) − thu T05")
    overdue: bool
    file_ids: list[int]
    vouchers: list[VoucherOut]


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    mime_type: str
    size_bytes: int


# --- Bảng kê đối soát (SOA) ---------------------------------------------------------------------------------------


class StatementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int = Field(gt=0)
    month: Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = Field(description="yyyy-mm")
    currency: Currency = "VND"


class StatementResponseIn(BaseModel):
    """Khách hàng tự xác nhận / khiếu nại; `manager` ghi nhận thay khi khách báo ngoài hệ thống – `note` bắt buộc."""

    model_config = ConfigDict(extra="forbid")
    note: Text1000 | None = None


class StatementDisputeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Text1000
    note: Text1000 | None = None


class StatementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    settlement_no: str
    settlement_type: str
    customer_id: int | None
    period_from: date
    period_to: date
    currency: str
    opening_balance: Decimal
    invoice_count: int
    invoice_total: Decimal = Field(description="Phát sinh tăng – hóa đơn thương mại trong tháng")
    paid_amount: Decimal = Field(description="Phát sinh giảm – phiếu thu T01 + T03 − phiếu chi C07 đã ghi sổ")
    balance_due: Decimal = Field(description="Số dư cuối kỳ = đầu kỳ + tăng − giảm")
    other_currency_totals: dict[str, Any]
    status: str
    previous_id: int | None
    prepared_by: int
    sent_by: int | None
    sent_at: UtcDatetime | None
    responded_by: int | None
    responded_at: UtcDatetime | None
    response_via: str | None
    response_note: str | None
    dispute_reason: str | None
    customer_confirmed_by: int | None
    customer_confirmed_at: UtcDatetime | None
    closed_by: int | None
    closed_at: UtcDatetime | None
    row_version: int


class StatementDetailOut(StatementOut):
    statement_lines: dict[str, Any] | None


class PortalStatementOut(BaseModel):
    """Cổng khách hàng: không có người lập / gửi nội bộ, `row_version`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    settlement_no: str
    period_from: date
    period_to: date
    currency: str
    opening_balance: Decimal
    invoice_count: int
    invoice_total: Decimal
    paid_amount: Decimal
    balance_due: Decimal
    other_currency_totals: dict[str, Any]
    status: str
    sent_at: UtcDatetime | None
    responded_at: UtcDatetime | None
    response_via: str | None
    dispute_reason: str | None
    statement_lines: dict[str, Any] | None
