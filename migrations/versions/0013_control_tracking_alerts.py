"""Giai đoạn 12 – Luồng 2: theo dõi thực thi, sự kiện ctl.*, cảnh báo chậm / lỗi.

- ctl_shipment_tasks: thêm thực tế (actual_start/end, progress_pct, blocked_reason/at, note, updated_at/by); trạng thái
  open | in_progress | blocked | done | closed; CHECK: blocked ⇔ có lý do, done ⇒ có actual_end và 100%.
- ctl_task_updates: nhật ký cập nhật bước (chỉ ghi thêm – trigger).
- ctl_alerts: cảnh báo cho manager (task_blocked, task_overdue, shipment_late_risk); một cảnh báo chưa xử lý cho mỗi
  dedup_key (UNIQUE một phần); không xóa (trigger).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ctl_alerts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("dedup_key", sa.String(length=100), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("plan_id", sa.BigInteger(), nullable=True),
        sa.Column("recipient_id", sa.BigInteger(), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.BigInteger(), nullable=True),
        sa.Column("resolution_note", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "(status = 'resolved') = (resolved_at IS NOT NULL)", name=op.f("ck_ctl_alerts_resolved_has_time")
        ),
        sa.CheckConstraint(
            "alert_type IN ('task_blocked', 'task_overdue', 'shipment_late_risk')",
            name=op.f("ck_ctl_alerts_alert_type_valid"),
        ),
        sa.CheckConstraint("severity IN ('warning', 'critical')", name=op.f("ck_ctl_alerts_severity_valid")),
        sa.CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name=op.f("ck_ctl_alerts_status_valid")),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["ctl_contract_plans.id"],
            name=op.f("fk_ctl_alerts_plan_id_ctl_contract_plans"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_id"], ["sys_users.id"], name=op.f("fk_ctl_alerts_recipient_id_sys_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["ctl_shipment_tasks.id"],
            name=op.f("fk_ctl_alerts_task_id_ctl_shipment_tasks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ctl_alerts")),
    )
    op.create_index("ix_ctl_alerts_recipient_status", "ctl_alerts", ["recipient_id", "status"], unique=False)
    op.create_index("ix_ctl_alerts_shipment", "ctl_alerts", ["shipment_id"], unique=False)
    op.create_index(
        "uq_ctl_alerts_dedup_unresolved",
        "ctl_alerts",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'resolved'"),
    )
    op.create_table(
        "ctl_task_updates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=False),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["ctl_shipment_tasks.id"],
            name=op.f("fk_ctl_task_updates_task_id_ctl_shipment_tasks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ctl_task_updates")),
    )
    op.create_index("ix_ctl_task_updates_task", "ctl_task_updates", ["task_id", "id"], unique=False)
    op.add_column("ctl_shipment_tasks", sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ctl_shipment_tasks", sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ctl_shipment_tasks", sa.Column("progress_pct", sa.Integer(), server_default="0", nullable=False))
    op.add_column("ctl_shipment_tasks", sa.Column("blocked_reason", sa.String(length=500), nullable=True))
    op.add_column("ctl_shipment_tasks", sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ctl_shipment_tasks", sa.Column("note", sa.String(length=1000), nullable=True))
    op.add_column("ctl_shipment_tasks", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ctl_shipment_tasks", sa.Column("updated_by", sa.BigInteger(), nullable=True))
    op.drop_constraint("ck_ctl_shipment_tasks_status_valid", "ctl_shipment_tasks", type_="check")
    op.create_check_constraint(
        op.f("ck_ctl_shipment_tasks_status_valid"),
        "ctl_shipment_tasks",
        "status IN ('open', 'in_progress', 'blocked', 'done', 'closed')",
    )
    op.create_check_constraint(
        op.f("ck_ctl_shipment_tasks_progress_range"), "ctl_shipment_tasks", "progress_pct BETWEEN 0 AND 100"
    )
    op.create_check_constraint(
        op.f("ck_ctl_shipment_tasks_blocked_has_reason"),
        "ctl_shipment_tasks",
        "(status = 'blocked') = (blocked_reason IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_ctl_shipment_tasks_done_has_end"),
        "ctl_shipment_tasks",
        "status <> 'done' OR (actual_end IS NOT NULL AND progress_pct = 100)",
    )
    op.create_check_constraint(
        op.f("ck_ctl_shipment_tasks_actual_order"),
        "ctl_shipment_tasks",
        "actual_end IS NULL OR actual_start IS NULL OR actual_end >= actual_start",
    )
    op.execute(
        """
        CREATE FUNCTION ctl_task_updates_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Bảng % chỉ ghi thêm; không sửa, không xóa', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END $$;
        CREATE TRIGGER ctl_task_updates_append_only BEFORE UPDATE OR DELETE ON ctl_task_updates
            FOR EACH ROW EXECUTE FUNCTION ctl_task_updates_append_only();
        CREATE TRIGGER ctl_alerts_no_delete BEFORE DELETE ON ctl_alerts
            FOR EACH ROW EXECUTE FUNCTION ctl_no_delete();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER ctl_alerts_no_delete ON ctl_alerts;
        DROP TRIGGER ctl_task_updates_append_only ON ctl_task_updates;
        DROP FUNCTION ctl_task_updates_append_only();
        """
    )
    # Bước đang làm / bị chặn / xong → về open (0012 chỉ có open, closed)
    op.execute("UPDATE ctl_shipment_tasks SET status = 'open' WHERE status IN ('in_progress', 'blocked', 'done')")
    for name in ("actual_order", "done_has_end", "blocked_has_reason", "progress_range", "status_valid"):
        op.drop_constraint(f"ck_ctl_shipment_tasks_{name}", "ctl_shipment_tasks", type_="check")
    op.create_check_constraint(
        op.f("ck_ctl_shipment_tasks_status_valid"), "ctl_shipment_tasks", "status IN ('open', 'closed')"
    )
    op.drop_column("ctl_shipment_tasks", "updated_by")
    op.drop_column("ctl_shipment_tasks", "updated_at")
    op.drop_column("ctl_shipment_tasks", "note")
    op.drop_column("ctl_shipment_tasks", "blocked_at")
    op.drop_column("ctl_shipment_tasks", "blocked_reason")
    op.drop_column("ctl_shipment_tasks", "progress_pct")
    op.drop_column("ctl_shipment_tasks", "actual_end")
    op.drop_column("ctl_shipment_tasks", "actual_start")
    op.drop_index("ix_ctl_task_updates_task", table_name="ctl_task_updates")
    op.drop_table("ctl_task_updates")
    op.drop_index(
        "uq_ctl_alerts_dedup_unresolved", table_name="ctl_alerts", postgresql_where=sa.text("status <> 'resolved'")
    )
    op.drop_index("ix_ctl_alerts_shipment", table_name="ctl_alerts")
    op.drop_index("ix_ctl_alerts_recipient_status", table_name="ctl_alerts")
    op.drop_table("ctl_alerts")
