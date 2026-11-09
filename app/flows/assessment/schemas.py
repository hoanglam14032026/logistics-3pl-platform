"""Schema API báo cáo KPI Luồng 4 (Giai đoạn 14)."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.core.timeutil import UtcDatetime


class PeriodOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from", serialization_alias="from")
    to: date
    timezone: str


class ShipmentMetrics(BaseModel):
    delivered: int = Field(description="Số lô giao trong kỳ")
    on_time: int = Field(description="Lô giao ≤ hạn SLA (`sla_due_at`)")
    late: int
    on_time_rate: float | None = Field(description="KPI-07 – % lô giao đúng hạn; null khi chưa có lô giao")
    avg_late_hours: float | None = Field(description="Giờ trễ trung bình của các lô trễ")
    max_late_hours: float | None


class TaskMetrics(BaseModel):
    completed: int = Field(description="Số bước hoàn thành trong kỳ")
    on_time: int = Field(description="Bước xong ≤ hạn kế hoạch (`planned_end`)")
    late: int
    on_time_rate: float | None = Field(description="KPI-03 – % bước hoàn thành đúng hạn")
    avg_delay_hours: float | None = Field(description="Giờ trễ trung bình của các bước trễ")
    max_delay_hours: float | None


class BlockMetrics(BaseModel):
    blocked_tasks: int = Field(description="Số bước bị chặn (khác nhau) trong kỳ")
    block_events: int = Field(description="Số lần chặn")


class AlertMetrics(BaseModel):
    alerts: int
    critical: int
    escalated: int = Field(description="Đã leo thang lên business_manager (BR-205)")


class Metrics(BaseModel):
    shipments: ShipmentMetrics | None = Field(description="null khi xem theo nhân viên (lô không gán theo nhân viên)")
    tasks: TaskMetrics
    blocked: BlockMetrics
    alerts: AlertMetrics


class SlaComplianceOut(BaseModel):
    sla_code: str
    kpi_code: str | None
    description: str | None
    target_value: str | None
    unit: str | None
    computed: bool = Field(description="false: Luồng 4 chưa tính KPI này (chỉ KPI-03, KPI-07)")
    actual_value: float | None
    met: bool | None


class KpiSummaryOut(BaseModel):
    period: PeriodOut
    mode: str
    generated_at: UtcDatetime
    metrics: Metrics


class ContractKpiOut(BaseModel):
    contract_id: int
    contract_no: str | None
    customer_id: int | None
    valid_from: date | None
    valid_to: date | None
    metrics: Metrics
    sla: list[SlaComplianceOut]


class ShipmentKpiOut(BaseModel):
    shipment_id: int
    shipment_no: str | None
    contract_id: int | None
    status: str | None
    required_delivery_at: UtcDatetime | None
    delivered_at: UtcDatetime | None
    sla_due_at: UtcDatetime | None
    on_time: bool | None = Field(description="Lô đã giao: giao ≤ hạn SLA; chưa giao: null")
    metrics: Metrics


class StaffKpiOut(BaseModel):
    user_id: int
    username: str | None
    full_name: str | None
    metrics: Metrics


class ContractKpiList(BaseModel):
    period: PeriodOut
    mode: str
    items: list[ContractKpiOut]
    total: int


class ShipmentKpiList(BaseModel):
    period: PeriodOut
    mode: str
    items: list[ShipmentKpiOut]
    total: int
    page: int
    page_size: int


class StaffKpiList(BaseModel):
    period: PeriodOut
    mode: str
    items: list[StaffKpiOut]
    total: int


class ContractKpiDetail(ContractKpiOut):
    period: PeriodOut
    mode: str


class ShipmentKpiDetail(ShipmentKpiOut):
    period: PeriodOut
    mode: str


class StaffKpiDetail(StaffKpiOut):
    period: PeriodOut
    mode: str
