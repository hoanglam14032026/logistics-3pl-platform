"""Schema API hợp đồng dịch vụ `/api/v2/ops/service-contracts` (02 §3.1, §8)."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.timeutil import UtcDatetime
from app.flows.operations.models import ContractMode, ContractStatus

Ref = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Terms = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class _ContractFields(BaseModel):
    customer_contract_ref: Ref | None = None
    signed_date: date | None = None
    liability_terms: Terms | None = None
    payment_terms: Terms | None = None
    advance_required: bool = False
    payment_due_days: int | None = Field(None, ge=0, le=3650)
    portal_enabled: bool = Field(True, description="Cho phép khách hàng dùng cổng (02 §9)")


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

    @model_validator(mode="after")
    def _not_null(self):
        for field in ("customer_id", "valid_from", "valid_to", "advance_required", "portal_enabled"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"Trường '{field}' không được null")
        return self


class StatusReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


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
    activated_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    row_version: int
