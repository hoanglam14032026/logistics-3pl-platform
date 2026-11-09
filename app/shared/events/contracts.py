"""
Hợp đồng dữ liệu (`data`) của sự kiện tích hợp (01 §5) – Giai đoạn 8: EV-101, EV-111, EV-112, EV-142.

Quy định 01 §5: thêm trường vào `data` giữ `schema_version`; xóa/đổi nghĩa trường → tăng `schema_version`.
Trường thêm so với bảng 01 §5 (giữ schema_version 1): `shipment_no`, `customer_id` ở EV-111.
"""

from datetime import date
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.core.timeutil import UtcDatetime


class EventData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    code: ClassVar[str]
    name: ClassVar[str]
    schema_version: ClassVar[int] = 1


class ContractActivated(EventData):
    """EV-101 `ops.contract.activated` – Luồng 1 → 2, 4."""

    code: ClassVar[str] = "EV-101"
    name: ClassVar[str] = "ops.contract.activated"

    contract_id: int
    contract_no: str
    customer_id: int
    service_scope: list[dict[str, Any]]  # [{service_code, params}]
    sla: dict[str, dict[str, Any]]  # sla_code → {description, target_value, unit, kpi_code, measure_level}
    valid_from: date
    valid_to: date


class PlannedTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_code: str
    planned_start: UtcDatetime
    planned_end: UtcDatetime
    is_checkpoint: bool


class ShipmentCreated(EventData):
    """EV-111 `ops.shipment.created` – Luồng 1 → 2, 4. Chưa có quy trình mẫu, bước (`ops_tasks`): `process_*` null,
    `planned_tasks` rỗng (02 OPS-F02 – làm sau)."""

    code: ClassVar[str] = "EV-111"
    name: ClassVar[str] = "ops.shipment.created"

    shipment_id: int
    shipment_no: str
    contract_id: int
    customer_id: int
    process_code: str | None = None
    process_version: int | None = None
    planned_tasks: list[PlannedTask] = []
    scenario_assignment_id: int | None = None
    learner_id: int | None = None


class ShipmentStatusChanged(EventData):
    """EV-112 `ops.shipment.status_changed` – Luồng 1 → 2, 4."""

    code: ClassVar[str] = "EV-112"
    name: ClassVar[str] = "ops.shipment.status_changed"

    shipment_id: int
    from_: str = Field(alias="from")
    to: str


class ShipmentDelivered(EventData):
    """EV-142 `ops.shipment.delivered` – Luồng 1 → 2, 4."""

    code: ClassVar[str] = "EV-142"
    name: ClassVar[str] = "ops.shipment.delivered"

    shipment_id: int
    delivered_at: UtcDatetime
    sla_due_at: UtcDatetime
    in_full: bool
    damage_free: bool
