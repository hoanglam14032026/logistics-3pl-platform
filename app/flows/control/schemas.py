"""Schema API Luồng 2 `/api/v2/control/...` (Giai đoạn 11)."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.core.timeutil import UtcDatetime

SkillCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,39}$")]


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    role_in_contract: str
    is_role_play: bool
    added_at: UtcDatetime
    added_by: int | None


class KpiTargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kpi_code: str
    description: str | None
    target_value: Decimal | None
    unit: str | None
    warning_threshold: Decimal | None
    source: str


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    contract_no: str
    customer_id: int
    mode: str
    valid_from: date
    valid_to: date
    service_scopes: list[dict[str, Any]]
    account_manager_id: int | None
    plan_version: int
    status: str
    created_at: UtcDatetime
    row_version: int


class ShipmentPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_id: int
    shipment_no: str
    contract_id: int
    plan_id: int | None
    direction: str | None
    transport_mode: str | None
    required_delivery_at: UtcDatetime | None
    process_code: str | None
    process_version: int | None
    assignee_id: int | None
    status: str
    shipment_status: str
    closed_at: UtcDatetime | None
    created_at: UtcDatetime


class PlanDetailOut(PlanOut):
    members: list[MemberOut] = []
    kpi_targets: list[KpiTargetOut] = []
    shipments: list[ShipmentPlanOut] = []


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_plan_id: int
    step_code: str
    seq: int
    name: str
    responsible_role: str
    required_skill: str | None
    standard_duration_hours: Decimal
    depends_on: list[str]
    is_checkpoint: bool
    checkpoint_type: str | None
    planned_start: UtcDatetime
    planned_end: UtcDatetime
    assignee_id: int | None
    controller_id: int | None
    status: str


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_id: int
    task_id: int | None
    assignee_id: int | None
    previous_assignee_id: int | None
    assigned_by: int | None
    assigned_at: UtcDatetime
    method: str
    reason: str | None
    score: dict[str, Any] | None
    is_current: bool


class ShipmentPlanDetailOut(ShipmentPlanOut):
    tasks: list[TaskOut] = []
    assignments: list[AssignmentOut] = []


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_id: int = Field(gt=0)
    assignee_id: int = Field(gt=0)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class AutoAssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_id: int = Field(gt=0)


class WorkloadOut(BaseModel):
    user_id: int
    username: str
    full_name: str
    open_shipments: int
    open_tasks: int
    open_hours: Decimal
    max_load_hours: Decimal
    load_ratio: float
    is_available: bool
    skills: dict[str, int]


class ProfileIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"skills": {"CUSTOMS_CLEARANCE": 4, "DOCS": 3}, "max_load_hours": "40", "is_available": True}
        },
    )

    skills: dict[SkillCode, int] = Field(default_factory=dict, max_length=50)
    max_load_hours: Decimal = Field(Decimal("40"), gt=0, le=1000, max_digits=7, decimal_places=2)
    is_available: bool = True
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    zalo_user_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9A-Za-z_-]{1,64}$")] | None = (
        Field(None, description="user_id người theo dõi Zalo OA – nhận cảnh báo qua Zalo (Giai đoạn 13)")
    )

    @field_validator("skills")
    @classmethod
    def _levels(cls, value: dict[str, int]) -> dict[str, int]:
        bad = sorted(k for k, v in value.items() if not 1 <= v <= 5)
        if bad:
            raise ValueError(f"Mức năng lực 1–5: {', '.join(bad)}")
        return value


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    skills: dict[str, int]
    max_load_hours: Decimal
    is_available: bool
    note: str | None
    zalo_user_id: str | None
    updated_at: UtcDatetime
    row_version: int


# --- Theo dõi thực thi, cảnh báo (Giai đoạn 12) -----------------------------------------------------------------

Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class TaskActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Note | None = None


class TaskBlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class TaskProgressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    progress_pct: int = Field(ge=0, le=99, description="100% = dùng :complete")
    note: Note | None = None


class TaskTrackOut(TaskOut):
    actual_start: UtcDatetime | None
    actual_end: UtcDatetime | None
    progress_pct: int
    blocked_reason: str | None
    blocked_at: UtcDatetime | None
    note: str | None
    updated_at: UtcDatetime | None
    delay_hours: float = Field(0.0, description="Trễ so với planned_end (xong: thực tế; chưa xong: tới hiện tại)")


class TaskUpdateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action: str
    from_status: str
    to_status: str
    progress_pct: int
    note: str | None
    actor_id: int | None
    at: UtcDatetime


class TaskDetailOut(TaskTrackOut):
    updates: list[TaskUpdateOut] = []


class HealthOut(BaseModel):
    health: str = Field(
        description="on_track | at_risk (có bước quá hạn) | late (dự kiến trễ hạn giao) | blocked | completed | closed"
    )
    progress_pct: float
    task_counts: dict[str, int]
    overdue_tasks: list[str]
    current_step: str | None
    planned_end: UtcDatetime | None
    forecast_end: UtcDatetime | None
    required_delivery_at: UtcDatetime | None
    late_vs_required_hours: float


class ShipmentTrackOut(ShipmentPlanOut):
    tracking: HealthOut


class ShipmentTrackDetailOut(ShipmentTrackOut):
    tasks: list[TaskTrackOut] = []


class PlanTrackOut(BaseModel):
    plan_id: int
    contract_id: int
    contract_no: str
    shipments: int
    by_health: dict[str, int]
    progress_pct: float
    open_alerts: int


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_type: str
    severity: str
    shipment_id: int | None
    contract_id: int | None = None
    task_id: int | None
    plan_id: int | None
    recipient_id: int | None
    message: str
    details: dict[str, Any] | None
    status: str
    raised_at: UtcDatetime
    acknowledged_at: UtcDatetime | None
    acknowledged_by: int | None
    resolved_at: UtcDatetime | None
    resolved_by: int | None
    resolution_note: str | None
    escalated_at: UtcDatetime | None = None
    escalated_to_role: str | None = None


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


# --- Giai đoạn 15: mục tiêu KPI + duyệt kế hoạch (EV-201) ------------------------------------------------------------


class KpiTargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpi_code: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z][A-Z0-9_-]{1,39}$")]
    target_value: Decimal = Field(max_digits=18, decimal_places=3)
    warning_threshold: Decimal | None = Field(None, max_digits=18, decimal_places=3)
    unit: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)] | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class KpiTargetsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpi_targets: list[KpiTargetIn] = Field(max_length=50)

    @field_validator("kpi_targets")
    @classmethod
    def _unique(cls, items: list[KpiTargetIn]) -> list[KpiTargetIn]:
        codes = [i.kpi_code for i in items]
        if len(codes) != len(set(codes)):
            raise ValueError("kpi_code bị trùng")
        return items


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
