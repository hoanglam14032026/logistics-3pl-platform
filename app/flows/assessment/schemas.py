"""Schema API báo cáo KPI Luồng 4 (Giai đoạn 14–15)."""

from datetime import date
from decimal import Decimal
from typing import Any

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


class KpiValueOut(BaseModel):
    name: str
    unit: str
    direction: str
    applicable: bool = Field(description="false: KPI không đo ở cấp này / chỉ có ở chế độ training")
    value: float | None = Field(description="% hoặc giờ (trung vị); null khi mẫu số 0")
    numerator: float
    denominator: int
    details: dict[str, Any] | None = None


class Metrics(BaseModel):
    shipments: ShipmentMetrics | None = Field(description="null khi xem theo nhân viên (lô không gán theo nhân viên)")
    tasks: TaskMetrics
    blocked: BlockMetrics
    alerts: AlertMetrics
    kpis: dict[str, KpiValueOut] = Field(description="KPI-01…14 theo danh mục (Giai đoạn 15)")


class TargetStatusOut(BaseModel):
    kpi_code: str
    target_value: str
    warning_threshold: str | None
    unit: str | None
    plan_id: int
    plan_version: int
    approved_at: UtcDatetime
    computed: bool
    actual_value: float | None
    status: str = Field(
        description="ok | warning (kém hơn mục tiêu) | critical (kém hơn ngưỡng) | no_data | not_computed"
    )


class SlaComplianceOut(BaseModel):
    sla_code: str
    kpi_code: str | None
    description: str | None
    target_value: str | None
    unit: str | None
    computed: bool = Field(description="false: mã KPI không có trong danh mục / không đo ở cấp hợp đồng")
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
    targets: list[TargetStatusOut] = Field(description="Mục tiêu từ kế hoạch được duyệt (EV-201)")


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


# --- Giai đoạn 15 ---------------------------------------------------------------------------------------------


class KpiDefinitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kpi_code: str
    version: int
    name: str
    description: str
    formula_key: str
    unit: str
    direction: str
    subject_levels: list[str]
    modes: list[str]
    source_events: list[str]
    status: str


class KpiTargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kpi_code: str
    subject_type: str
    subject_id: int
    target_value: Decimal | None
    warning_threshold: Decimal | None
    unit: str | None
    source_plan_id: int
    plan_version: int
    valid_from: UtcDatetime
    current: bool = False


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    calc_run_id: int
    kpi_code: str
    kpi_version: int
    subject_type: str
    subject_id: int
    period_type: str
    period_start: date
    period_end: date
    numerator: Decimal
    denominator: int
    value: Decimal | None
    details: dict[str, Any] | None
    computed_at: UtcDatetime


class CalcRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date | None = Field(None, description="Ngày cuối kỳ tính (mặc định hôm qua, giờ Việt Nam)")


class CalcRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trigger: str
    as_of: date
    status: str
    requested_by: int | None
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    stats: dict[str, Any] | None
    error: str | None


class KpiAlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kpi_code: str
    subject_type: str
    subject_id: int
    period_type: str
    period_start: date
    period_end: date
    value: Decimal
    target: Decimal
    warning_threshold: Decimal | None
    severity: str
    calc_run_id: int
    event_id: Any
    raised_at: UtcDatetime
