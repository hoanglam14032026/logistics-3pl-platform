"""
Luồng 4 – bảng dữ kiện `asm_fact_*` (05 §6) – Giai đoạn 14.

Dựng **chỉ từ sự kiện** nhận qua Redis Stream `l3pl:events:assessment` (EV-101, EV-111/112/142, EV-131/132/133,
`ops.task.unblocked`, `ctl.task.*`, `ctl.alert.*`) – không FK, không đọc bảng của Luồng 1, 2 (N2, N3, N4). Mỗi dòng
ghi `mode` của sự kiện (BR-01) và `event_id` nguồn gần nhất; không xử lý trùng nhờ `sys_event_consumptions`.

KPI tính từ các bảng này (`kpi.py`): khi gọi API và hằng ngày trong worker (Giai đoạn 15 – `calc.py`, lưu số đo vào
`asm_fact_kpi_measurements`, mỗi lần tính một `asm_calc_runs`).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class _FactMixin:
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'production'"))
    last_event_id: Mapped[Any] = mapped_column(Uuid, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


MODE_CHECK = "mode IN ('production', 'training')"


class AsmFactContract(_FactMixin, Base):
    """EV-101: hợp đồng có hiệu lực + SLA (mục tiêu KPI theo `kpi_code`)."""

    __tablename__ = "asm_fact_contracts"
    __table_args__ = (CheckConstraint(MODE_CHECK, name="mode_valid"),)

    contract_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    contract_no: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sla: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AsmFactShipment(_FactMixin, Base):
    """EV-111 (tạo), EV-112 (trạng thái), EV-142 (giao hàng: đúng hạn so với `sla_due_at`)."""

    __tablename__ = "asm_fact_shipments"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        Index("ix_asm_fact_shipments_contract_id", "contract_id"),
        Index("ix_asm_fact_shipments_delivered_at", "delivered_at"),
    )

    shipment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    shipment_no: Mapped[str | None] = mapped_column(String(40))
    contract_id: Mapped[int | None] = mapped_column(BigInteger)
    customer_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    required_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    in_full: Mapped[bool | None] = mapped_column(Boolean)
    damage_free: Mapped[bool | None] = mapped_column(Boolean)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Giai đoạn 15 – KPI-02: chứng từ đầu tiên (EV-121) → lô chuyển docs_verified (EV-112, người chuyển = actor)
    first_doc_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    docs_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    docs_verified_by: Mapped[int | None] = mapped_column(BigInteger)


class AsmFactTask(_FactMixin, Base):
    """Bước của lô, khóa theo id bước Luồng 2 (nguồn chuẩn, `revision` tăng mỗi lần đổi).

    Trạng thái lấy từ `ctl.task.synced` / `ctl.task.*` / EV-13x chuyển tiếp (`origin = control`) – chỉ ghi đè khi
    `revision` ≥ bản đang có. EV-13x `origin = ops` là thay đổi **chưa được Luồng 2 xác nhận** (có thể bị từ chối do
    xung đột) → không đổi trạng thái; Luồng 2 xác nhận bằng `ctl.task.synced`. EV-132 cho `sequence_ok`, `evidence_ok`.
    """

    __tablename__ = "asm_fact_tasks"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        CheckConstraint("revision >= 0", name="revision_non_negative"),
        CheckConstraint("block_count >= 0", name="block_count_non_negative"),
        Index("ix_asm_fact_tasks_shipment_id", "shipment_id"),
        Index("ix_asm_fact_tasks_actual_end", "actual_end"),
    )

    task_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    ops_task_id: Mapped[int | None] = mapped_column(BigInteger)
    shipment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    step_code: Mapped[str] = mapped_column(String(20), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(BigInteger)
    performer_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str | None] = mapped_column(String(16))
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sequence_ok: Mapped[bool | None] = mapped_column(Boolean)
    evidence_ok: Mapped[bool | None] = mapped_column(Boolean)
    block_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_overdue_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))


class AsmFactTaskBlock(Base):
    """Một lần bước chuyển sang bị chặn – khóa (bước, revision): `ctl.task.blocked`, EV-133 chuyển tiếp và
    `ctl.task.synced` của cùng thay đổi chỉ tính một lần."""

    __tablename__ = "asm_fact_task_blocks"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        UniqueConstraint("task_id", "revision", name="uq_asm_fact_task_blocks_task_revision"),
        Index("ix_asm_fact_task_blocks_blocked_at", "blocked_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    shipment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(BigInteger)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'production'"))
    reason: Mapped[str | None] = mapped_column(String(500))
    blocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_event_id: Mapped[Any] = mapped_column(Uuid, nullable=False)


class AsmFactAlert(_FactMixin, Base):
    """`ctl.alert.raised`, `ctl.alert.escalated` (BR-205)."""

    __tablename__ = "asm_fact_alerts"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        Index("ix_asm_fact_alerts_raised_at", "raised_at"),
    )

    alert_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    shipment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[int | None] = mapped_column(BigInteger)
    recipient_id: Mapped[int | None] = mapped_column(BigInteger)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_to_role: Mapped[str | None] = mapped_column(String(32))


# --- Giai đoạn 15 ---------------------------------------------------------------------------------------------


class AsmKpiDefinition(Base):
    """Danh mục KPI (05 §3) – seed ở migration 0016. Không chứa mục tiêu: mục tiêu đến từ kế hoạch được duyệt (EV-201).
    `formula_key` trỏ tới hàm trong `kpi.FORMULAS`; đổi công thức → tăng `version` (số đo cũ giữ `kpi_version`)."""

    __tablename__ = "asm_kpi_definitions"
    __table_args__ = (
        UniqueConstraint("kpi_code", "version", name="uq_asm_kpi_definitions_code_version"),
        CheckConstraint("direction IN ('higher_better', 'lower_better')", name="direction_valid"),
        CheckConstraint("status IN ('active', 'retired')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kpi_code: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    formula_key: Mapped[str] = mapped_column(String(40), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_levels: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    modes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source_events: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))


class AsmKpiTarget(Base):
    """Mục tiêu KPI theo hợp đồng từ EV-201 (05 §6 `asm_kpi_targets`). Mỗi lần duyệt kế hoạch thêm bộ mới; mục tiêu
    hiện hành = bản có `valid_from` mới nhất của (hợp đồng, KPI)."""

    __tablename__ = "asm_kpi_targets"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        UniqueConstraint("source_event_id", "kpi_code", name="uq_asm_kpi_targets_event_kpi"),
        Index("ix_asm_kpi_targets_subject", "subject_type", "subject_id", "kpi_code", "valid_from"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kpi_code: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'contract'"))
    subject_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    warning_threshold: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    unit: Mapped[str | None] = mapped_column(String(20))
    source_plan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'production'"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_event_id: Mapped[Any] = mapped_column(Uuid, nullable=False)


class AsmFactDocCheck(_FactMixin, Base):
    """EV-122 – lần kiểm tra bộ chứng từ (KPI-01). `checked_by` = người phát sự kiện."""

    __tablename__ = "asm_fact_document_checks"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        Index("ix_asm_fact_document_checks_at", "checked_at"),
    )

    check_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    shipment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_no: Mapped[int] = mapped_column(Integer, nullable=False)
    blocking_count: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False)
    checked_by: Mapped[int | None] = mapped_column(BigInteger)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AsmFactCheckpoint(_FactMixin, Base):
    """EV-204 – một lần đánh giá điểm kiểm soát (KPI-05)."""

    __tablename__ = "asm_fact_checkpoints"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        CheckConstraint("result IN ('passed', 'failed')", name="result_valid"),
        Index("ix_asm_fact_checkpoints_at", "evaluated_at"),
    )

    checkpoint_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    attempt_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    shipment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    step_code: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[str] = mapped_column(String(8), nullable=False)
    performer_id: Mapped[int | None] = mapped_column(BigInteger)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AsmFactCustoms(_FactMixin, Base):
    """EV-141 – thông quan (KPI-06, tách luồng xanh / vàng / đỏ)."""

    __tablename__ = "asm_fact_customs"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        CheckConstraint("channel IN ('green', 'yellow', 'red')", name="channel_valid"),
        Index("ix_asm_fact_customs_cleared_at", "cleared_at"),
    )

    shipment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    declaration_no: Mapped[str] = mapped_column(String(40), primary_key=True)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cleared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AsmFactIncident(_FactMixin, Base):
    """EV-205 / EV-206 – sự cố (KPI-09: loại `DOCUMENT_ERROR`, KPI-10)."""

    __tablename__ = "asm_fact_incidents"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        Index("ix_asm_fact_incidents_shipment", "shipment_id"),
    )

    incident_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    contract_id: Mapped[int | None] = mapped_column(BigInteger)
    shipment_id: Mapped[int | None] = mapped_column(BigInteger)
    incident_type: Mapped[str | None] = mapped_column(String(40))
    severity: Mapped[str | None] = mapped_column(String(16))
    attributed_to: Mapped[int | None] = mapped_column(BigInteger)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    root_cause_code: Mapped[str | None] = mapped_column(String(40))
    liability: Mapped[str | None] = mapped_column(String(20))


class AsmFactServiceRequest(_FactMixin, Base):
    """EV-113 / EV-114 – yêu cầu dịch vụ (KPI-14)."""

    __tablename__ = "asm_fact_service_requests"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        Index("ix_asm_fact_service_requests_decided_at", "decided_at"),
    )

    request_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    contract_id: Mapped[int | None] = mapped_column(BigInteger)
    channel: Mapped[str | None] = mapped_column(String(16))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[str | None] = mapped_column(String(16))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    shipment_id: Mapped[int | None] = mapped_column(BigInteger)


class AsmCalcRun(Base):
    """Một lần tính KPI (05 §6 `asm_calc_runs`): `schedule` (worker hằng ngày) / `manual` (API)."""

    __tablename__ = "asm_calc_runs"
    __table_args__ = (
        CheckConstraint("trigger IN ('schedule', 'manual')", name="trigger_valid"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(1000))


class AsmFactKpiMeasurement(Base):
    """Số đo KPI (05 §6 `asm_measurements`) – **chỉ thêm, không sửa / xóa** (trigger); tính lại → dòng mới với
    `calc_run_id` mới. Đối tượng `all` (toàn công ty, `subject_id = 0`), `contract`, `staff`."""

    __tablename__ = "asm_fact_kpi_measurements"
    __table_args__ = (
        CheckConstraint(MODE_CHECK, name="mode_valid"),
        CheckConstraint("subject_type IN ('all', 'contract', 'staff')", name="subject_type_valid"),
        CheckConstraint("period_type IN ('week', 'month', 'quarter')", name="period_type_valid"),
        CheckConstraint("denominator >= 0 AND numerator >= 0", name="counts_non_negative"),
        Index(
            "ix_asm_fact_kpi_measurements_lookup",
            "kpi_code",
            "subject_type",
            "subject_id",
            "period_type",
            "period_start",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    calc_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("asm_calc_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kpi_code: Mapped[str] = mapped_column(String(16), nullable=False)
    kpi_version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'production'"))
    period_type: Mapped[str] = mapped_column(String(8), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    numerator: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    denominator: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AsmKpiAlert(Base):
    """Cảnh báo KPI đã phát (EV-403) – một lần cho mỗi (KPI, đối tượng, kỳ); không sửa / xóa."""

    __tablename__ = "asm_kpi_alerts"
    __table_args__ = (
        CheckConstraint("severity IN ('warning', 'critical')", name="severity_valid"),
        UniqueConstraint(
            "kpi_code", "subject_type", "subject_id", "period_type", "period_start", name="uq_asm_kpi_alerts_period"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kpi_code: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_type: Mapped[str] = mapped_column(String(8), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    target: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    warning_threshold: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    calc_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("asm_calc_runs.id", ondelete="RESTRICT"), nullable=False
    )
    measurement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("asm_fact_kpi_measurements.id", ondelete="RESTRICT"), nullable=False
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("asm_kpi_targets.id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[Any] = mapped_column(Uuid, nullable=False)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
