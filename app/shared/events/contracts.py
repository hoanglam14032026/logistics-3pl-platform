"""
Hợp đồng dữ liệu (`data`) của sự kiện tích hợp (01 §5) – Giai đoạn 8: EV-101, EV-111, EV-112, EV-142; Giai đoạn 10:
`doc.*` (job trích xuất chứng từ AI local – chỉ id, trạng thái, model; **không** chứa giá trị trích được).

`consumers`: luồng nhận (01 §5 cột "Luồng nhận") – `control` = Luồng 2, `assessment` = Luồng 4 (mã trong ENABLED_FLOWS).

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
    consumers: ClassVar[tuple[str, ...]] = ("control", "assessment")


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
    # Thêm Giai đoạn 11 (giữ schema_version 1): Luồng 2 chọn quy trình mẫu, tính thời gian kế hoạch. Sự kiện cũ thiếu
    # các trường này → Luồng 2 vẫn tạo kế hoạch lô, không sinh bước.
    direction: str | None = None
    transport_mode: str | None = None
    required_delivery_at: UtcDatetime | None = None
    service_codes: list[str] = []


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


class AssignmentChanged(EventData):
    """EV-202 `ctl.assignment.changed` – Luồng 2 → 1 (Giai đoạn 11): người phụ trách lô (`task_id` null) hoặc bước."""

    code: ClassVar[str] = "EV-202"
    name: ClassVar[str] = "ctl.assignment.changed"
    consumers: ClassVar[tuple[str, ...]] = ("ops",)

    shipment_id: int
    task_id: int | None = None
    assignee_id: int | None
    previous_assignee_id: int | None = None
    method: str  # auto | manual
    reason: str | None = None


# --- ctl.* – theo dõi thực thi của Luồng 2 (Giai đoạn 12) – luồng nhận: Luồng 4 (KPI) --------------------------------
# Danh mục 01 §5 chưa có mã cho các sự kiện này (EV-131/132/133 là bước của Luồng 1) – dùng mã dạng tên (CẦN XÁC NHẬN).


class _CtlTaskEvent(EventData):
    consumers: ClassVar[tuple[str, ...]] = ("assessment",)

    shipment_id: int
    task_id: int
    step_code: str
    plan_id: int | None
    assignee_id: int | None
    planned_start: UtcDatetime
    planned_end: UtcDatetime
    # Thêm Giai đoạn 14 (giữ schema_version 1): revision của bước sau thay đổi – Luồng 4 khử trùng với
    # `ctl.task.synced` / EV-13x của cùng thay đổi. Sự kiện cũ không có → null.
    revision: int | None = None


class CtlTaskStarted(_CtlTaskEvent):
    code: ClassVar[str] = "ctl.task.started"
    name: ClassVar[str] = "ctl.task.started"

    actual_start: UtcDatetime
    start_delay_hours: float


class CtlTaskCompleted(_CtlTaskEvent):
    code: ClassVar[str] = "ctl.task.completed"
    name: ClassVar[str] = "ctl.task.completed"

    actual_start: UtcDatetime | None
    actual_end: UtcDatetime
    delay_hours: float  # > 0: xong sau planned_end
    is_checkpoint: bool


class CtlTaskBlocked(_CtlTaskEvent):
    code: ClassVar[str] = "ctl.task.blocked"
    name: ClassVar[str] = "ctl.task.blocked"

    reason: str


class CtlTaskDelayed(_CtlTaskEvent):
    code: ClassVar[str] = "ctl.task.delayed"
    name: ClassVar[str] = "ctl.task.delayed"

    overdue_hours: float
    status: str


class CtlTaskSynced(EventData):
    """Đồng bộ bước Luồng 2 → Luồng 1 (Giai đoạn 13): trạng thái **đầy đủ** của bước + `revision` (Luồng 2 là nguồn
    chuẩn, tăng mỗi lần đổi). Luồng 1 ghi đè `ops_tasks` khi `revision` ≥ bản đang có. `origin`: phía khởi phát thay
    đổi (`control` – thao tác ở Luồng 2; `ops` – xác nhận thay đổi Luồng 1 gửi lên). `conflict`: thay đổi của Luồng 1 bị
    từ chối (xung đột) – đây là trạng thái chuẩn để Luồng 1 quay về."""

    code: ClassVar[str] = "ctl.task.synced"
    name: ClassVar[str] = "ctl.task.synced"
    consumers: ClassVar[tuple[str, ...]] = ("ops", "assessment")  # Giai đoạn 14: Luồng 4 dựng dữ kiện bước

    task_id: int
    shipment_id: int
    step_code: str
    seq: int
    name: str
    responsible_role: str
    depends_on: list[str]
    is_checkpoint: bool
    planned_start: UtcDatetime
    planned_end: UtcDatetime
    assignee_id: int | None
    status: str
    progress_pct: int
    actual_start: UtcDatetime | None
    actual_end: UtcDatetime | None
    blocked_reason: str | None
    revision: int
    origin: str
    conflict: bool = False


class CtlAlertEscalated(EventData):
    """BR-205 (Giai đoạn 13): cảnh báo chưa xử lý quá hạn → leo thang lên business_manager."""

    code: ClassVar[str] = "ctl.alert.escalated"
    name: ClassVar[str] = "ctl.alert.escalated"
    consumers: ClassVar[tuple[str, ...]] = ("assessment",)

    alert_id: int
    alert_type: str
    severity: str
    shipment_id: int
    escalated_to_role: str
    unresolved_hours: float


class CtlAlertRaised(EventData):
    code: ClassVar[str] = "ctl.alert.raised"
    name: ClassVar[str] = "ctl.alert.raised"
    consumers: ClassVar[tuple[str, ...]] = ("assessment",)

    alert_id: int
    alert_type: str
    severity: str
    shipment_id: int
    task_id: int | None
    plan_id: int | None
    recipient_id: int | None


# --- EV-131/132/133 – bước của Luồng 1 (Giai đoạn 13: đồng bộ với Luồng 2) ---------------------------------------
# Trường thêm so với 01 §5 (giữ schema_version 1): `ctl_task_id` (bước tương ứng ở Luồng 2), `base_revision` (revision
# Luồng 1 đang có khi đổi), `origin` (`ops`: thao tác ở Luồng 1; `control`: chuyển tiếp thay đổi từ Luồng 2).


class _OpsTaskEvent(EventData):
    task_id: int
    ctl_task_id: int
    shipment_id: int
    step_code: str
    base_revision: int
    origin: str


class OpsTaskStarted(_OpsTaskEvent):
    code: ClassVar[str] = "EV-131"
    name: ClassVar[str] = "ops.task.started"

    performer_id: int | None
    actual_start: UtcDatetime


class OpsTaskCompleted(_OpsTaskEvent):
    code: ClassVar[str] = "EV-132"
    name: ClassVar[str] = "ops.task.completed"

    performer_id: int | None
    planned_end: UtcDatetime
    actual_end: UtcDatetime
    sequence_ok: bool
    evidence_ok: bool | None = None  # chưa kiểm chứng từ đầu ra (CẦN XÁC NHẬN)


class OpsTaskBlocked(_OpsTaskEvent):
    code: ClassVar[str] = "EV-133"
    name: ClassVar[str] = "ops.task.blocked"
    consumers: ClassVar[tuple[str, ...]] = ("control", "assessment")  # Giai đoạn 14: + Luồng 4 (số bước bị chặn)

    reason_code: str
    note: str | None


class OpsTaskUnblocked(_OpsTaskEvent):
    """Gỡ chặn – 01 §5 chưa có mã EV (CẦN XÁC NHẬN)."""

    code: ClassVar[str] = "ops.task.unblocked"
    name: ClassVar[str] = "ops.task.unblocked"
    consumers: ClassVar[tuple[str, ...]] = ("control", "assessment")

    note: str | None


# --- doc.* – job trích xuất chứng từ AI local (Giai đoạn 10) -------------------------------------------------------


class _DocJobEvent(EventData):
    job_id: int
    doc_type: str
    status: str
    target_type: str | None
    ai_model_id: int
    model_code: str


class DocExtracted(_DocJobEvent):
    code: ClassVar[str] = "doc.extracted"
    name: ClassVar[str] = "doc.job.extracted"

    overall_confidence: float | None
    low_confidence_count: int
    missing_required_count: int


class DocFailed(_DocJobEvent):
    code: ClassVar[str] = "doc.failed"
    name: ClassVar[str] = "doc.job.failed"

    attempts: int


class DocApprovalPending(_DocJobEvent):
    """Duyệt lần 1 xong, chờ người duyệt thứ 2 (duyệt 2 người)."""

    code: ClassVar[str] = "doc.approval_pending"
    name: ClassVar[str] = "doc.job.approval_pending"

    first_approved_by: int


class DocApproved(_DocJobEvent):
    code: ClassVar[str] = "doc.approved"
    name: ClassVar[str] = "doc.job.approved"

    approved_by: int
    first_approved_by: int | None = None


class DocRejected(_DocJobEvent):
    code: ClassVar[str] = "doc.rejected"
    name: ClassVar[str] = "doc.job.rejected"

    rejected_by: int


class DocLinked(_DocJobEvent):
    code: ClassVar[str] = "doc.linked"
    name: ClassVar[str] = "doc.job.linked"

    linked_entity_type: str
    linked_entity_id: int
