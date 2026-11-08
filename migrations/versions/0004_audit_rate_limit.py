"""Giai đoạn 2 – nhật ký thao tác sys_audit_logs (06 §5), bộ đếm giới hạn tần suất sys_rate_limit_counters (06 §3).

- sys_audit_logs: chỉ thêm. Trigger `sys_audit_logs_immutable` chặn UPDATE, DELETE (kể cả khi lỗi mã nguồn hoặc
  ai đó sửa tay qua ứng dụng). TRUNCATE không bị chặn bởi trigger dòng – môi trường thật nên cấp cho tài khoản CSDL
  của ứng dụng quyền INSERT, SELECT trên bảng này (không UPDATE, DELETE, TRUNCATE). Trigger viết tay: autogenerate
  không sinh trigger.
- sys_rate_limit_counters: (scope, key, window_start) → count; dòng cũ hơn 1 giờ bị xóa khi có lượt gọi mới.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sys_audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_audit_logs")),
    )
    op.create_index("ix_sys_audit_logs_action_at", "sys_audit_logs", ["action", "at"], unique=False)
    op.create_index("ix_sys_audit_logs_actor_at", "sys_audit_logs", ["actor_id", "at"], unique=False)
    op.create_index("ix_sys_audit_logs_entity", "sys_audit_logs", ["entity_type", "entity_id"], unique=False)
    op.execute(
        """
        CREATE FUNCTION sys_audit_logs_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'sys_audit_logs chỉ được thêm, không được sửa hoặc xóa (%)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER sys_audit_logs_immutable
        BEFORE UPDATE OR DELETE ON sys_audit_logs
        FOR EACH ROW EXECUTE FUNCTION sys_audit_logs_immutable()
        """
    )
    op.create_table(
        "sys_rate_limit_counters",
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "key", "window_start", name=op.f("pk_sys_rate_limit_counters")),
    )
    op.create_index(
        "ix_sys_rate_limit_counters_window_start", "sys_rate_limit_counters", ["window_start"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_sys_rate_limit_counters_window_start", table_name="sys_rate_limit_counters")
    op.drop_table("sys_rate_limit_counters")
    op.execute("DROP TRIGGER sys_audit_logs_immutable ON sys_audit_logs")
    op.execute("DROP FUNCTION sys_audit_logs_immutable()")
    op.drop_index("ix_sys_audit_logs_entity", table_name="sys_audit_logs")
    op.drop_index("ix_sys_audit_logs_actor_at", table_name="sys_audit_logs")
    op.drop_index("ix_sys_audit_logs_action_at", table_name="sys_audit_logs")
    op.drop_table("sys_audit_logs")
