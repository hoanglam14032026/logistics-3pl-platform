"""Giai đoạn 14 – Luồng 4: bảng dữ kiện KPI vận hành dựng từ sự kiện.

- asm_fact_contracts (EV-101 + SLA), asm_fact_shipments (EV-111/112/142), asm_fact_tasks (ctl.task.*, EV-131/132/133 –
  khóa theo id bước Luồng 2 + revision), asm_fact_task_blocks (mỗi lần bị chặn), asm_fact_alerts (ctl.alert.*).
- Dữ kiện dựng từ sự kiện, không chép từ bảng luồng khác (N2): sau nâng cấp chạy
  `python -m app.shared.events.cli replay assessment` để dựng từ outbox. `sys_events.consumers` không sửa được
  (trigger 0011) – EV-133, ops.task.unblocked, ctl.task.synced phát **trước** 0015 không có luồng nhận `assessment`;
  trạng thái bước cũ lấy từ ctl.task.* và EV-131/132 (đã có luồng nhận 4).

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asm_fact_alerts",
        sa.Column("alert_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("recipient_id", sa.BigInteger(), nullable=True),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_to_role", sa.String(length=32), nullable=True),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_fact_alerts_mode_valid")),
        sa.PrimaryKeyConstraint("alert_id", name=op.f("pk_asm_fact_alerts")),
    )
    op.create_index("ix_asm_fact_alerts_raised_at", "asm_fact_alerts", ["raised_at"], unique=False)
    op.create_table(
        "asm_fact_contracts",
        sa.Column("contract_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("contract_no", sa.String(length=40), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "sla", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_fact_contracts_mode_valid")),
        sa.PrimaryKeyConstraint("contract_id", name=op.f("pk_asm_fact_contracts")),
    )
    op.create_table(
        "asm_fact_shipments",
        sa.Column("shipment_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("shipment_no", sa.String(length=40), nullable=True),
        sa.Column("contract_id", sa.BigInteger(), nullable=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("required_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("in_full", sa.Boolean(), nullable=True),
        sa.Column("damage_free", sa.Boolean(), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_fact_shipments_mode_valid")),
        sa.PrimaryKeyConstraint("shipment_id", name=op.f("pk_asm_fact_shipments")),
    )
    op.create_index("ix_asm_fact_shipments_contract_id", "asm_fact_shipments", ["contract_id"], unique=False)
    op.create_index("ix_asm_fact_shipments_delivered_at", "asm_fact_shipments", ["delivered_at"], unique=False)
    op.create_table(
        "asm_fact_task_blocks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("assignee_id", sa.BigInteger(), nullable=True),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_fact_task_blocks_mode_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asm_fact_task_blocks")),
        sa.UniqueConstraint("task_id", "revision", name="uq_asm_fact_task_blocks_task_revision"),
    )
    op.create_index("ix_asm_fact_task_blocks_blocked_at", "asm_fact_task_blocks", ["blocked_at"], unique=False)
    op.create_table(
        "asm_fact_tasks",
        sa.Column("task_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("ops_task_id", sa.BigInteger(), nullable=True),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("step_code", sa.String(length=20), nullable=False),
        sa.Column("assignee_id", sa.BigInteger(), nullable=True),
        sa.Column("performer_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("planned_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sequence_ok", sa.Boolean(), nullable=True),
        sa.Column("evidence_ok", sa.Boolean(), nullable=True),
        sa.Column("block_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_overdue_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_fact_tasks_mode_valid")),
        sa.CheckConstraint("block_count >= 0", name=op.f("ck_asm_fact_tasks_block_count_non_negative")),
        sa.CheckConstraint("revision >= 0", name=op.f("ck_asm_fact_tasks_revision_non_negative")),
        sa.PrimaryKeyConstraint("task_id", name=op.f("pk_asm_fact_tasks")),
    )
    op.create_index("ix_asm_fact_tasks_actual_end", "asm_fact_tasks", ["actual_end"], unique=False)
    op.create_index("ix_asm_fact_tasks_shipment_id", "asm_fact_tasks", ["shipment_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_asm_fact_tasks_shipment_id", table_name="asm_fact_tasks")
    op.drop_index("ix_asm_fact_tasks_actual_end", table_name="asm_fact_tasks")
    op.drop_table("asm_fact_tasks")
    op.drop_index("ix_asm_fact_task_blocks_blocked_at", table_name="asm_fact_task_blocks")
    op.drop_table("asm_fact_task_blocks")
    op.drop_index("ix_asm_fact_shipments_delivered_at", table_name="asm_fact_shipments")
    op.drop_index("ix_asm_fact_shipments_contract_id", table_name="asm_fact_shipments")
    op.drop_table("asm_fact_shipments")
    op.drop_table("asm_fact_contracts")
    op.drop_index("ix_asm_fact_alerts_raised_at", table_name="asm_fact_alerts")
    op.drop_table("asm_fact_alerts")
