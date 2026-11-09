"""Schema API lô hàng `/api/v2/ops/shipments` và cổng `/api/v2/portal/shipments` (02 §3.2, §6.2, §8, §9.4)."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

from app.core.timeutil import UtcDatetime
from app.flows.operations.models import INCOTERMS_2020, Direction, Priority, ShipmentStatus, TransportMode

Place = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Incoterm = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
    StringConstraints(pattern=r"^(" + "|".join(INCOTERMS_2020) + r")$"),
]


def _aware(value: datetime | None, field: str) -> None:
    if value is not None and value.tzinfo is None:
        raise ValueError(f"{field} phải có múi giờ (ISO 8601, vd. 2026-11-20T08:00:00+07:00)")


class _ShipmentFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Direction
    transport_mode: TransportMode
    incoterm: Incoterm | None = Field(None, description="Incoterms 2020 – bắt buộc khi import/export")
    incoterm_place: Place | None = None
    shipper_id: int = Field(gt=0, description="md_parties – người gửi hàng")
    consignee_id: int = Field(gt=0, description="md_parties – người nhận hàng")
    notify_party_id: int | None = Field(None, gt=0)
    origin_location_id: int = Field(gt=0, description="Điểm đi – md_locations đang hoạt động (Giai đoạn 17)")
    destination_location_id: int = Field(gt=0, description="Điểm đến – md_locations đang hoạt động")
    cargo_summary: dict[str, Any] | None = Field(
        None, description="Số kiện, trọng lượng cả bì (kg), thể tích (m³), container (số, loại, seal)"
    )
    etd: datetime | None = Field(None, description="Dự kiến khởi hành")
    eta: datetime | None = Field(None, description="Dự kiến đến")
    required_delivery_at: datetime = Field(description="Ngày giao yêu cầu – hạn SLA")
    claims_preferential_tariff: bool = False
    priority: Priority = Priority.NORMAL


def _check_rules(direction, incoterm, etd, eta, required) -> None:
    for name, value in (("etd", etd), ("eta", eta), ("required_delivery_at", required)):
        _aware(value, name)
    if direction in (Direction.IMPORT, Direction.EXPORT) and not incoterm:
        raise ValueError("incoterm bắt buộc với lô nhập/xuất khẩu")
    if etd and eta and etd > eta:
        raise ValueError("etd phải ≤ eta")


class ShipmentCreate(_ShipmentFields):
    contract_id: int = Field(gt=0, description="Hợp đồng đang hiệu lực (active)")

    @model_validator(mode="after")
    def _rules(self):
        _check_rules(self.direction, self.incoterm, self.etd, self.eta, self.required_delivery_at)
        if self.origin_location_id == self.destination_location_id:
            raise ValueError("Điểm đi và điểm đến phải khác nhau")
        return self


class ShipmentUpdate(BaseModel):
    """PATCH khi còn `draft` (JSON Merge Patch). Không đổi hợp đồng – hủy và tạo lô mới. Quy tắc chéo trường kiểm
    lại trên dữ liệu sau khi gộp (service)."""

    model_config = ConfigDict(extra="forbid")

    direction: Direction | None = None
    transport_mode: TransportMode | None = None
    incoterm: Incoterm | None = None
    incoterm_place: Place | None = None
    shipper_id: int | None = Field(None, gt=0)
    consignee_id: int | None = Field(None, gt=0)
    notify_party_id: int | None = Field(None, gt=0)
    origin_location_id: int | None = Field(None, gt=0)
    destination_location_id: int | None = Field(None, gt=0)
    cargo_summary: dict[str, Any] | None = None
    etd: datetime | None = None
    eta: datetime | None = None
    required_delivery_at: datetime | None = None
    claims_preferential_tariff: bool | None = None
    priority: Priority | None = None

    @model_validator(mode="after")
    def _not_null(self):
        required = ("direction", "transport_mode", "shipper_id", "consignee_id", "origin_location_id")
        for name in (*required, "destination_location_id", "required_delivery_at", "claims_preferential_tariff"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} không được null")
        if "priority" in self.model_fields_set and self.priority is None:
            raise ValueError("priority không được null")
        return self


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Reason


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class DeliveryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivered_at: datetime
    receiver_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    in_full: bool
    damage_free: bool
    remarks: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None

    @model_validator(mode="after")
    def _tz(self):
        _aware(self.delivered_at, "delivered_at")
        return self


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignee_id: int = Field(gt=0, description="Tài khoản ops_staff đang hoạt động")


# --- Đầu ra nội bộ --------------------------------------------------------------------------------------------------


class StatusHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_status: ShipmentStatus | None
    to_status: ShipmentStatus
    changed_at: UtcDatetime
    changed_by: int | None
    note: str | None


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivered_at: UtcDatetime
    receiver_name: str
    in_full: bool
    damage_free: bool
    remarks: str | None


class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_no: str
    contract_id: int
    contract_version: int
    mode: str
    customer_id: int
    direction: Direction
    transport_mode: TransportMode
    incoterm: str | None
    incoterm_place: str | None
    shipper_id: int
    shipper_name: str
    consignee_id: int
    consignee_name: str
    notify_party_id: int | None
    notify_party_name: str | None
    origin_location: str
    destination_location: str
    origin_location_id: int | None  # Giai đoạn 17 – lô cũ có thể null
    destination_location_id: int | None
    cargo_summary: dict[str, Any] | None
    etd: UtcDatetime | None
    eta: UtcDatetime | None
    required_delivery_at: UtcDatetime
    claims_preferential_tariff: bool
    priority: Priority
    assignee_id: int | None
    status: ShipmentStatus
    status_reason: str | None
    delivered_at: UtcDatetime | None
    closed_at: UtcDatetime | None
    cancelled_at: UtcDatetime | None
    created_at: UtcDatetime
    created_by: int | None
    updated_at: UtcDatetime
    source_doc_job_id: int | None = None  # job trích xuất AI đã duyệt (Giai đoạn 9)
    row_version: int


class ShipmentDetailOut(ShipmentOut):
    history: list[StatusHistoryOut] = []
    delivery: DeliveryOut | None = None


# --- Cổng khách hàng (PRT-06; BR-111 – không có người phụ trách, ưu tiên, lý do hủy, ghi chú, người thao tác) -------


class PortalMilestoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: ShipmentStatus
    at: UtcDatetime


class PortalShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_no: str
    contract_id: int
    contract_no: str
    direction: Direction
    transport_mode: TransportMode
    incoterm: str | None
    incoterm_place: str | None
    shipper_name: str
    consignee_name: str
    origin_location: str
    destination_location: str
    etd: UtcDatetime | None
    eta: UtcDatetime | None
    required_delivery_at: UtcDatetime
    status: ShipmentStatus
    delivered_at: UtcDatetime | None


class PortalShipmentDetailOut(PortalShipmentOut):
    cargo_summary: dict[str, Any] | None
    milestones: list[PortalMilestoneOut] = []
