"""
Luồng 2 – Quản lý, kiểm soát (03 §3.1, §3.3) – Giai đoạn 11: kế hoạch triển khai hợp đồng, kế hoạch lô + bước thực hiện
từ quy trình mẫu, phân công tự động.

Dữ liệu của Luồng 1 chỉ đến qua sự kiện (EV-101, EV-111, EV-112, EV-142) – lưu bản chụp, **không** FK sang bảng
`ops_*` (N2: luồng không đọc/ghi bảng của luồng khác; Luồng 1 tắt thì Luồng 2 vẫn chạy với dữ liệu đã nhận).
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
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
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditColumnsMixin, Base
from app.core.timeutil import now_utc


class PlanStatus(StrEnum):
    """03 §3.1. Giai đoạn 11 chỉ tạo `draft` (tự tạo khi nhận EV-101); gửi duyệt, duyệt (EV-201): làm sau."""

    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REVISED = "revised"
    CLOSED = "closed"


class WorkStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"  # lô đã giao / đóng / hủy (EV-112, EV-142) – không còn tính vào tải công việc


class TaskStatus(StrEnum):
    """Trạng thái bước (Giai đoạn 12): open → in_progress → done; in_progress ⇄ blocked (lỗi, chờ…); closed: lô đã
    đóng/hủy (EV-112)."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CLOSED = "closed"


ACTIVE_TASK_STATUSES = (TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value, TaskStatus.BLOCKED.value)


class AlertType(StrEnum):
    TASK_BLOCKED = "task_blocked"  # bước gặp lỗi / bị chặn
    TASK_OVERDUE = "task_overdue"  # quá thời hạn kế hoạch mà chưa xong
    SHIPMENT_LATE_RISK = "shipment_late_risk"  # dự kiến xong sau hạn giao yêu cầu (SLA)
    TASK_SYNC_CONFLICT = "task_sync_conflict"  # Giai đoạn 13: thay đổi bước ở Luồng 1 xung đột với Luồng 2
    KPI_BELOW_TARGET = "kpi_below_target"  # Giai đoạn 15: EV-403 – KPI kém hơn mục tiêu (không gắn lô)
    STAFF_ADVANCE_OVERDUE = "staff_advance_overdue"  # Giai đoạn 20: M-10 – tạm ứng quá hạn hoàn ứng (không gắn lô)


class AlertSeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class AssignMethod(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


def _in(values) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class CtlContractPlan(AuditColumnsMixin, Base):
    """Kế hoạch triển khai hợp đồng (03 §3.1 `ctl_contract_plans`) – tự tạo nháp khi nhận EV-101 (CTL-F01)."""

    __tablename__ = "ctl_contract_plans"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(PlanStatus)})", name="status_valid"),
        CheckConstraint("valid_from <= valid_to", name="valid_period"),
    )

    contract_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    contract_no: Mapped[str] = mapped_column(String(30), nullable=False)
    customer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    service_scopes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    account_manager_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"))
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    notes: Mapped[str | None] = mapped_column(String(4000))
    source_event_id: Mapped[Any] = mapped_column(Uuid, nullable=False)


class CtlPlanMember(Base):
    """Thành viên kế hoạch (03 §3.1 `ctl_plan_members`). Phân công tự động thêm người được giao (BR-204)."""

    __tablename__ = "ctl_plan_members"
    __table_args__ = (
        UniqueConstraint("plan_id", "user_id", "role_in_contract", name="uq_ctl_plan_members_plan_user_role"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ctl_contract_plans.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"), nullable=False)
    role_in_contract: Mapped[str] = mapped_column(String(32), nullable=False)  # account_manager, doc_staff, …
    is_role_play: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    added_by: Mapped[int | None] = mapped_column(BigInteger)  # NULL: hệ thống (phân công tự động)


class CtlPlanKpiTarget(Base):
    """Mục tiêu KPI (03 §3.1 `ctl_plan_kpi_targets`) – mặc định từ SLA hợp đồng trong EV-101."""

    __tablename__ = "ctl_plan_kpi_targets"
    __table_args__ = (UniqueConstraint("plan_id", "kpi_code", name="uq_ctl_plan_kpi_targets_plan_kpi"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ctl_contract_plans.id", ondelete="RESTRICT"), nullable=False
    )
    kpi_code: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    unit: Mapped[str | None] = mapped_column(String(20))
    warning_threshold: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="contract_sla")


class CtlShipmentPlan(AuditColumnsMixin, Base):
    """Kế hoạch thực hiện một lô (nhận EV-111): quy trình mẫu đã chọn, người phụ trách hiện tại."""

    __tablename__ = "ctl_shipment_plans"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(WorkStatus)})", name="status_valid"),
        Index("ix_ctl_shipment_plans_assignee_status", "assignee_id", "status"),
        Index("ix_ctl_shipment_plans_contract", "contract_id"),
    )

    shipment_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    shipment_no: Mapped[str] = mapped_column(String(30), nullable=False)
    contract_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plan_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ctl_contract_plans.id", ondelete="RESTRICT"))
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(16))
    transport_mode: Mapped[str | None] = mapped_column(String(16))
    required_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    template_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("md_process_templates.id", ondelete="RESTRICT")
    )
    process_code: Mapped[str | None] = mapped_column(String(40))
    process_version: Mapped[int | None] = mapped_column(Integer)
    assignee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    shipment_status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_event_id: Mapped[Any] = mapped_column(Uuid, nullable=False)


class CtlShipmentTask(Base):
    """Bước thực hiện của lô, sinh từ `md_process_steps` (02 §4) – người thực hiện, người kiểm soát (BR-201)."""

    __tablename__ = "ctl_shipment_tasks"
    __table_args__ = (
        UniqueConstraint("shipment_plan_id", "step_code", name="uq_ctl_shipment_tasks_plan_step"),
        CheckConstraint(f"status IN ({_in(TaskStatus)})", name="status_valid"),
        CheckConstraint("progress_pct BETWEEN 0 AND 100", name="progress_range"),
        CheckConstraint("(status = 'blocked') = (blocked_reason IS NOT NULL)", name="blocked_has_reason"),
        CheckConstraint("status <> 'done' OR (actual_end IS NOT NULL AND progress_pct = 100)", name="done_has_end"),
        CheckConstraint(
            "actual_end IS NULL OR actual_start IS NULL OR actual_end >= actual_start", name="actual_order"
        ),
        CheckConstraint(
            "controller_id IS NULL OR assignee_id IS NULL OR controller_id <> assignee_id", name="four_eyes"
        ),
        Index("ix_ctl_shipment_tasks_assignee_status", "assignee_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shipment_plan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ctl_shipment_plans.id", ondelete="RESTRICT"), nullable=False
    )
    step_code: Mapped[str] = mapped_column(String(20), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    responsible_role: Mapped[str] = mapped_column(String(32), nullable=False)
    required_skill: Mapped[str | None] = mapped_column(String(40))
    standard_duration_hours: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    depends_on: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_checkpoint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checkpoint_type: Mapped[str | None] = mapped_column(String(40))
    planned_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"))
    controller_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    # Thực tế (Giai đoạn 12)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    blocked_reason: Mapped[str | None] = mapped_column(String(500))
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(1000))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    # Giai đoạn 13: tăng mỗi lần đổi trạng thái/người – Luồng 1 (`ops_tasks`) đồng bộ theo số này (nguồn chuẩn)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class CtlAssignment(Base):
    """Lịch sử phân công (03 §3.3 `ctl_assignments`) – chỉ ghi thêm; `is_current` đánh dấu phân công hiện tại của lô."""

    __tablename__ = "ctl_assignments"
    __table_args__ = (
        CheckConstraint(f"method IN ({_in(AssignMethod)})", name="method_valid"),
        Index("ix_ctl_assignments_shipment", "shipment_id", "id"),
        Index("ix_ctl_assignments_assignee", "assignee_id"),
        Index(
            "uq_ctl_assignments_current",
            "shipment_id",
            unique=True,
            postgresql_where=text("is_current AND task_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ctl_shipment_tasks.id", ondelete="RESTRICT"))
    assignee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"))
    previous_assignee_id: Mapped[int | None] = mapped_column(BigInteger)
    assigned_by: Mapped[int | None] = mapped_column(BigInteger)  # NULL: phân công tự động
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    # Giải thích lựa chọn: năng lực khớp, tải trước/sau, ứng viên khác
    score: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    event_id: Mapped[Any | None] = mapped_column(Uuid)  # EV-202 đã phát


class CtlStaffProfile(AuditColumnsMixin, Base):
    """Năng lực, sức chứa của nhân viên cho phân công tự động (Giai đoạn 11). Chưa có hồ sơ: không năng lực nào,
    `max_load_hours` = 40, sẵn sàng."""

    __tablename__ = "ctl_staff_profiles"
    __table_args__ = (CheckConstraint("max_load_hours > 0", name="max_load_positive"),)

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    # {mã năng lực: mức 1–5} – mã dịch vụ (CUSTOMS_CLEARANCE…) hoặc DOCS
    skills: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    max_load_hours: Mapped[Decimal] = mapped_column(
        Numeric(7, 2), nullable=False, default=Decimal("40"), server_default="40"
    )
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    note: Mapped[str | None] = mapped_column(String(500))
    # Giai đoạn 13: user_id người theo dõi Zalo OA – nhận cảnh báo qua Zalo khi đặt ZALO_TOKEN
    zalo_user_id: Mapped[str | None] = mapped_column(String(64))


class CtlTaskUpdate(Base):
    """Nhật ký cập nhật thực tế của bước (Giai đoạn 12) – chỉ ghi thêm: bắt đầu, % tiến độ, chặn/gỡ chặn, hoàn thành."""

    __tablename__ = "ctl_task_updates"
    __table_args__ = (Index("ix_ctl_task_updates_task", "task_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ctl_shipment_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # start | progress | block | unblock | complete
    from_status: Mapped[str] = mapped_column(String(16), nullable=False)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000))
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )


class CtlAlert(Base):
    """Cảnh báo chậm / lỗi cho manager (03 §3.5 – Giai đoạn 12). Người nhận: `account_manager` của kế hoạch; không có →
    NULL = mọi `manager`. Một cảnh báo chưa xử lý cho mỗi `dedup_key` (không cảnh báo lặp)."""

    __tablename__ = "ctl_alerts"
    __table_args__ = (
        CheckConstraint(f"alert_type IN ({_in(AlertType)})", name="alert_type_valid"),
        CheckConstraint(f"severity IN ({_in(AlertSeverity)})", name="severity_valid"),
        CheckConstraint(f"status IN ({_in(AlertStatus)})", name="status_valid"),
        CheckConstraint("(status = 'resolved') = (resolved_at IS NOT NULL)", name="resolved_has_time"),
        CheckConstraint(
            "shipment_id IS NOT NULL OR alert_type IN ('kpi_below_target', 'staff_advance_overdue')",
            name="shipment_required",
        ),
        Index(
            "uq_ctl_alerts_dedup_unresolved", "dedup_key", unique=True, postgresql_where=text("status <> 'resolved'")
        ),
        Index("ix_ctl_alerts_recipient_status", "recipient_id", "status"),
        Index("ix_ctl_alerts_shipment", "shipment_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(100), nullable=False)
    shipment_id: Mapped[int | None] = mapped_column(BigInteger)  # NULL: cảnh báo KPI (Giai đoạn 15)
    contract_id: Mapped[int | None] = mapped_column(BigInteger)  # Giai đoạn 15: cảnh báo KPI theo hợp đồng
    task_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ctl_shipment_tasks.id", ondelete="RESTRICT"))
    plan_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ctl_contract_plans.id", ondelete="RESTRICT"))
    recipient_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    raised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[int | None] = mapped_column(BigInteger)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(BigInteger)  # NULL: hệ thống tự đóng (bước xong / gỡ chặn)
    resolution_note: Mapped[str | None] = mapped_column(String(500))
    # BR-205 (Giai đoạn 13): chưa xử lý sau CONTROL_ESCALATION_HOURS → leo thang lên business_manager
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_to_role: Mapped[str | None] = mapped_column(String(32))


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"  # hết lượt thử lại


class CtlAlertNotification(Base):
    """Thông báo cảnh báo ra ngoài ứng dụng (Giai đoạn 13): mỗi (cảnh báo, mục đích, kênh, người nhận) một dòng – gửi
    qua hàng đợi rq, thử lại như Giai đoạn 7. Kênh `email` (email tài khoản), `zalo` (ZALO_TOKEN + `zalo_user_id`)."""

    __tablename__ = "ctl_alert_notifications"
    __table_args__ = (
        UniqueConstraint("alert_id", "purpose", "channel", "user_id", name="uq_ctl_alert_notifications_target"),
        CheckConstraint("channel IN ('email', 'zalo')", name="channel_valid"),
        CheckConstraint("purpose IN ('raised', 'escalated', 'kpi')", name="purpose_valid"),
        CheckConstraint(f"status IN ({_in(NotificationStatus)})", name="status_valid"),
        CheckConstraint("(status = 'sent') = (sent_at IS NOT NULL)", name="sent_has_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ctl_alerts.id", ondelete="RESTRICT"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"), nullable=False)
    address: Mapped[str] = mapped_column(String(254), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
