"""Schema API hóa đơn thương mại `/api/v2/ops/commercial-invoices` (Giai đoạn 10).

Số tiền: NUMERIC trong CSDL, **chuỗi** trong JSON (vd. `"12500.50"`) – không mất chính xác như số thực.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.timeutil import UtcDatetime
from app.flows.operations.schemas import Currency
from app.flows.operations.shipment_schemas import Incoterm


def _scale(places: int):
    """Chuẩn hóa số chữ số thập phân như cột NUMERIC (12500.5 → 12500.50) – đầu ra giống nhau trước/sau khi lưu."""
    quantum = Decimal(1).scaleb(-places)
    return AfterValidator(lambda v: v.quantize(quantum) if v is not None else v)


Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2), _scale(2)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class InvoiceItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    hs_code: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9][0-9.]{1,11}$")] | None = None
    quantity: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=3), _scale(3)] | None = None
    unit: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)] | None = None
    unit_price: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4), _scale(4)] | None = None
    amount: Money | None = None


class InvoiceCreate(BaseModel):
    """Có `shipment_id` → hợp đồng lấy theo lô (`contract_id` gửi kèm phải trùng). Không có lô → cần `contract_id`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "shipment_id": 1,
                "invoice_no": "INV-2026-001",
                "invoice_date": "2026-11-05",
                "seller_name": "Shanghai Export Co.",
                "buyer_name": "Công ty TNHH Minh An",
                "incoterm": "CIF",
                "currency": "USD",
                "total_amount": "12500.50",
                "items": [{"description": "Máy in", "hs_code": "8443.32", "quantity": "10", "unit_price": "1250.05"}],
            }
        },
    )

    shipment_id: int | None = Field(None, gt=0)
    contract_id: int | None = Field(None, gt=0)
    invoice_no: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    invoice_date: date
    seller_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    buyer_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    incoterm: Incoterm | None = None
    currency: Currency
    total_amount: Money
    items: list[InvoiceItemIn] = Field(default_factory=list, max_length=500)
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None

    @model_validator(mode="after")
    def _owner(self):
        if self.shipment_id is None and self.contract_id is None:
            raise ValueError("Cần shipment_id hoặc contract_id")
        return self


class InvoiceItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_no: int
    description: str
    hs_code: str | None
    quantity: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    amount: Decimal | None


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    shipment_id: int | None
    invoice_no: str
    invoice_date: date
    seller_name: str
    buyer_name: str
    incoterm: str | None
    currency: str
    total_amount: Decimal = Field(description='Chuỗi số thập phân 2 chữ số, vd. "12500.50"')
    note: str | None
    source_doc_job_id: int | None
    created_at: UtcDatetime
    created_by: int | None
    row_version: int


class InvoiceDetailOut(InvoiceOut):
    items: list[InvoiceItemOut] = []
