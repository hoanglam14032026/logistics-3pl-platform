"""Giai đoạn 13 – đồng bộ bước Luồng 2 ↔ Luồng 1, leo thang BR-205, gửi cảnh báo qua email / Zalo.

- ops_tasks: bước của lô ở Luồng 1 (bản đồng bộ của ctl_shipment_tasks theo revision). Bước đã có ở Luồng 2 được chép
  sang (revision 1) – hai bên khớp ngay sau khi nâng cấp.
- ctl_shipment_tasks.revision; ctl_alerts.escalated_at, escalated_to_role; loại cảnh báo task_sync_conflict.
- ctl_alert_notifications: thông báo email / Zalo (một dòng mỗi cảnh báo + mục đích + kênh + người nhận).
- ctl_staff_profiles.zalo_user_id.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("ctl_task_id", sa.BigInteger(), nullable=False),
        sa.Column("step_code", sa.String(length=20), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("responsible_role", sa.String(length=32), nullable=False),
        sa.Column("depends_on", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_checkpoint", sa.Boolean(), nullable=False),
        sa.Column("planned_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assignee_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_reason", sa.String(length=500), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("pending_local", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'blocked', 'done', 'closed')", name=op.f("ck_ops_tasks_status_valid")
        ),
        sa.CheckConstraint("progress_pct BETWEEN 0 AND 100", name=op.f("ck_ops_tasks_progress_range")),
        sa.CheckConstraint("revision >= 0", name=op.f("ck_ops_tasks_revision_non_negative")),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["ops_shipments.id"],
            name=op.f("fk_ops_tasks_shipment_id_ops_shipments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_tasks")),
        sa.UniqueConstraint("ctl_task_id", name=op.f("uq_ops_tasks_ctl_task_id")),
        sa.UniqueConstraint("shipment_id", "step_code", name="uq_ops_tasks_shipment_step"),
    )
    op.create_index("ix_ops_tasks_assignee_status", "ops_tasks", ["assignee_id", "status"], unique=False)
    op.create_table(
        "ctl_alert_notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("alert_id", sa.BigInteger(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("address", sa.String(length=254), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(status = 'sent') = (sent_at IS NOT NULL)", name=op.f("ck_ctl_alert_notifications_sent_has_time")
        ),
        sa.CheckConstraint("channel IN ('email', 'zalo')", name=op.f("ck_ctl_alert_notifications_channel_valid")),
        sa.CheckConstraint("purpose IN ('raised', 'escalated')", name=op.f("ck_ctl_alert_notifications_purpose_valid")),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')", name=op.f("ck_ctl_alert_notifications_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["ctl_alerts.id"],
            name=op.f("fk_ctl_alert_notifications_alert_id_ctl_alerts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["sys_users.id"],
            name=op.f("fk_ctl_alert_notifications_user_id_sys_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ctl_alert_notifications")),
        sa.UniqueConstraint("alert_id", "purpose", "channel", "user_id", name="uq_ctl_alert_notifications_target"),
    )
    op.add_column("ctl_alerts", sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ctl_alerts", sa.Column("escalated_to_role", sa.String(length=32), nullable=True))
    op.add_column("ctl_shipment_tasks", sa.Column("revision", sa.Integer(), server_default="0", nullable=False))
    op.add_column("ctl_staff_profiles", sa.Column("zalo_user_id", sa.String(length=64), nullable=True))
    op.drop_constraint("ck_ctl_alerts_alert_type_valid", "ctl_alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_ctl_alerts_alert_type_valid"),
        "ctl_alerts",
        "alert_type IN ('task_blocked', 'task_overdue', 'shipment_late_risk', 'task_sync_conflict')",
    )
    op.execute("UPDATE ctl_shipment_tasks SET revision = 1")
    op.execute(
        """
        INSERT INTO ops_tasks (shipment_id, ctl_task_id, step_code, seq, name, responsible_role, depends_on,
                               is_checkpoint, planned_start, planned_end, assignee_id, status, progress_pct,
                               actual_start, actual_end, blocked_reason, revision, pending_local, last_synced_at)
        SELECT p.shipment_id, t.id, t.step_code, t.seq, t.name, t.responsible_role, t.depends_on, t.is_checkpoint,
               t.planned_start, t.planned_end, t.assignee_id, t.status, t.progress_pct, t.actual_start, t.actual_end,
               t.blocked_reason, t.revision, false, now()
        FROM ctl_shipment_tasks t
        JOIN ctl_shipment_plans p ON p.id = t.shipment_plan_id
        JOIN ops_shipments s ON s.id = p.shipment_id
        """
    )
    op.execute(
        "CREATE TRIGGER ctl_alert_notifications_no_delete BEFORE DELETE ON ctl_alert_notifications "
        "FOR EACH ROW EXECUTE FUNCTION ctl_no_delete()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER ctl_alert_notifications_no_delete ON ctl_alert_notifications")
    op.drop_column("ctl_staff_profiles", "zalo_user_id")
    op.drop_column("ctl_shipment_tasks", "revision")
    op.drop_column("ctl_alerts", "escalated_to_role")
    op.drop_column("ctl_alerts", "escalated_at")
    op.drop_table("ctl_alert_notifications")
    op.drop_index("ix_ops_tasks_assignee_status", table_name="ops_tasks")
    op.drop_table("ops_tasks")
    # Cảnh báo task_sync_conflict không có ở 0013: xóa (tạm tắt trigger chặn xóa) rồi đặt lại CHECK cũ
    op.execute(
        """
        ALTER TABLE ctl_alerts DISABLE TRIGGER ctl_alerts_no_delete;
        DELETE FROM ctl_alerts WHERE alert_type = 'task_sync_conflict';
        ALTER TABLE ctl_alerts ENABLE TRIGGER ctl_alerts_no_delete;
        """
    )
    op.drop_constraint("ck_ctl_alerts_alert_type_valid", "ctl_alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_ctl_alerts_alert_type_valid"),
        "ctl_alerts",
        "alert_type IN ('task_blocked', 'task_overdue', 'shipment_late_risk')",
    )
