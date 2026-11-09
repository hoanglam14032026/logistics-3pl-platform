"""Schema API hợp đồng dịch vụ `/api/v2/ops/service-contracts` (02 §3.1, §8)."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

from app.core.timeutil import UtcDatetime
from app.flows.operations.models import (
    AttachmentKind,
    ContractMode,
    ContractStatus,
    MeasureLevel,
    RateBasis,
    ServiceCode,
)
from app.shared.files.schemas import FileOut

Ref = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Terms = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Code = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
    StringConstraints(min_length=1, max_length=40, pattern=r"^[A-Z0-9][A-Z0-9_.-]*$"),
]
Currency = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
    StringConstraints(pattern=r"^[A-Z]{3}$"),
]
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Quantity = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=3)]
TaxRate = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


# --- Phạm vi dịch vụ, SLA, biểu giá (02 §3.1, §3.8) ------------------------------------------------------------


class ScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_code: ServiceCode
    params: dict[str, Any] = Field(default_factory=dict, description="Tham số riêng của dịch vụ (cảng, kho, tuyến…)")


class SlaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sla_code: Code
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    target_value: Quantity
    unit: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
    kpi_code: Code | None = None
    measure_level: MeasureLevel
    penalty_rule: dict[str, Any] | None = None


class RateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate_code: Code
    service_code: ServiceCode
    charge_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    basis: RateBasis
    unit_price: Money
    currency: Currency = "VND"
    min_charge: Money | None = None
    tax_rate: TaxRate
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def _period(self):
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from phải ≤ valid_to")
        return self


class ScopeOut(ScopeIn):
    model_config = ConfigDict(from_attributes=True)


class SlaOut(SlaIn):
    model_config = ConfigDict(from_attributes=True)


class RateOut(RateIn):
    model_config = ConfigDict(from_attributes=True)


def _unique(items, key: str, label: str):
    if items is None:
        return items
    seen = [getattr(i, key) for i in items]
    dup = sorted({str(v) for v in seen if seen.count(v) > 1})
    if dup:
        raise ValueError(f"{label} bị trùng: {', '.join(dup)}")
    return items


ScopeList = Annotated[list[ScopeIn], Field(max_length=50)]
SlaList = Annotated[list[SlaIn], Field(max_length=100)]
RateList = Annotated[list[RateIn], Field(max_length=500)]


class _ContractFields(BaseModel):
    customer_contract_ref: Ref | None = None
    signed_date: date | None = None
    liability_terms: Terms | None = None
    payment_terms: Terms | None = None
    advance_required: bool = False
    payment_due_days: int | None = Field(None, ge=0, le=3650)
    portal_enabled: bool = Field(True, description="Cho phép khách hàng dùng cổng (02 §9)")
    service_scopes: ScopeList = Field(default_factory=list)
    sla_terms: SlaList = Field(default_factory=list)
    rates: RateList = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_codes(self):
        _unique(self.service_scopes, "service_code", "service_code")
        _unique(self.sla_terms, "sla_code", "sla_code")
        _unique(self.rates, "rate_code", "rate_code")
        return self


class ContractCreate(_ContractFields):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "customer_id": 1,
                "customer_contract_ref": "MA-2026/HD-07",
                "signed_date": "2026-11-01",
                "valid_from": "2026-11-01",
                "valid_to": "2027-10-31",
                "payment_terms": "Thanh toán trong 30 ngày kể từ ngày nhận bảng kê",
                "payment_due_days": 30,
                "portal_enabled": True,
            }
        },
    )

    # Giai đoạn 3: chỉ `production`; `training` cần lớp/người học (Luồng 3, 4 – BR-01)
    mode: Literal[ContractMode.PRODUCTION] = ContractMode.PRODUCTION
    customer_id: int = Field(gt=0)
    valid_from: date
    valid_to: date

    @model_validator(mode="after")
    def _period(self):
        if self.valid_from > self.valid_to:
            raise ValueError("valid_from phải ≤ valid_to")
        return self


class ContractUpdate(BaseModel):
    """PATCH bản nháp (JSON Merge Patch – 02 §8: chỉ `draft`). Trạng thái đổi qua hành động `:activate`…"""

    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = Field(None, gt=0)
    customer_contract_ref: Ref | None = None
    signed_date: date | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    liability_terms: Terms | None = None
    payment_terms: Terms | None = None
    advance_required: bool | None = None
    payment_due_days: int | None = Field(None, ge=0, le=3650)
    portal_enabled: bool | None = None
    # JSON Merge Patch: mảng gửi lên thay thế toàn bộ mảng cũ (RFC 7396)
    service_scopes: ScopeList | None = None
    sla_terms: SlaList | None = None
    rates: RateList | None = None

    @model_validator(mode="after")
    def _not_null(self):
        nullable = ("customer_contract_ref", "signed_date", "liability_terms", "payment_terms", "payment_due_days")
        for field in self.model_fields_set - set(nullable):
            if getattr(self, field) is None:
                raise ValueError(f"Trường '{field}' không được null")
        _unique(self.service_scopes, "service_code", "service_code")
        _unique(self.sla_terms, "sla_code", "sla_code")
        _unique(self.rates, "rate_code", "rate_code")
        return self


class StatusReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class LiquidationStart(BaseModel):
    """Lý do bắt buộc khi thanh lý trước hạn (hợp đồng còn hiệu lực) – 02 §3.9.2."""

    model_config = ConfigDict(extra="forbid")

    reason: Reason | None = None


class VersionCreate(BaseModel):
    """Phiên bản mới từ phụ lục đã ký (tới khi có quy trình phụ lục 02 §3.7 – phê duyệt qua Luồng 2).

    Trường không gửi giữ nguyên giá trị của phiên bản hiện tại; mảng gửi lên thay thế toàn bộ."""

    model_config = ConfigDict(extra="forbid")

    amendment_ref: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
    reason: Reason
    effective_from: date
    attachment_id: int = Field(gt=0, description="File đính kèm loại `amendment` (bản scan phụ lục đã ký)")
    valid_to: date | None = None
    liability_terms: Terms | None = None
    payment_terms: Terms | None = None
    advance_required: bool | None = None
    payment_due_days: int | None = Field(None, ge=0, le=3650)
    service_scopes: ScopeList | None = None
    sla_terms: SlaList | None = None
    rates: RateList | None = None

    @model_validator(mode="after")
    def _validate(self):
        _unique(self.service_scopes, "service_code", "service_code")
        _unique(self.sla_terms, "sla_code", "sla_code")
        _unique(self.rates, "rate_code", "rate_code")
        changes = self.model_fields_set - {"amendment_ref", "reason", "effective_from", "attachment_id"}
        if not changes:
            raise ValueError("Phiên bản mới phải thay đổi ít nhất một điều khoản")
        return self


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contract_version: int
    effective_from: date
    amendment_ref: str | None
    reason: str | None
    attachment_id: int | None
    snapshot: dict[str, Any]
    created_at: UtcDatetime


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: AttachmentKind
    description: str | None
    customer_visible: bool
    file: FileOut
    created_at: UtcDatetime


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_no: str
    customer_contract_ref: str | None
    mode: ContractMode
    customer_id: int
    customer_name: str
    customer_tax_code: str | None
    signed_date: date | None
    valid_from: date
    valid_to: date
    liability_terms: str | None
    payment_terms: str | None
    advance_required: bool
    payment_due_days: int | None
    portal_enabled: bool
    status: ContractStatus
    status_reason: str | None
    contract_version: int
    activated_at: UtcDatetime | None
    liquidation_started_at: UtcDatetime | None
    closed_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    source_doc_job_id: int | None = None  # job trích xuất AI đã duyệt (Giai đoạn 9)
    row_version: int


class ContractDetailOut(ContractOut):
    """Chi tiết kèm phạm vi, SLA, biểu giá của phiên bản hiện tại."""

    service_scopes: list[ScopeOut] = []
    sla_terms: list[SlaOut] = []
    rates: list[RateOut] = []


# --- Cổng khách hàng (PRT-01; BR-111: không có thông tin nội bộ) ------------------------------------------------


class PortalContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_no: str
    customer_contract_ref: str | None
    signed_date: date | None
    valid_from: date
    valid_to: date
    status: ContractStatus
    contract_version: int
    payment_due_days: int | None
    advance_required: bool


class PortalContractDetailOut(PortalContractOut):
    liability_terms: str | None
    payment_terms: str | None
    service_scopes: list[ScopeOut] = []
    sla_terms: list[SlaOut] = []
    rates: list[RateOut] = []


class PortalVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contract_version: int
    effective_from: date
    amendment_ref: str | None
    snapshot: dict[str, Any]


class PortalAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: AttachmentKind
    description: str | None
    file: FileOut
