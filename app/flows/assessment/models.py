"""
Luồng 4 – bảng dữ kiện `asm_fact_*` (05 §6) – Giai đoạn 14.

Dựng **chỉ từ sự kiện** nhận qua Redis Stream `l3pl:events:assessment` (EV-101, EV-111/112/142, EV-131/132/133,
`ops.task.unblocked`, `ctl.task.*`, `ctl.alert.*`) – không FK, không đọc bảng của Luồng 1, 2 (N2, N3, N4). Mỗi dòng
ghi `mode` của sự kiện (BR-01) và `event_id` nguồn gần nhất; không xử lý trùng nhờ `sys_event_consumptions`.

KPI tính khi đọc từ các bảng này (`kpi.py`) – không có `asm_measurements` / `asm_calc_runs` ở giai đoạn này.
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
