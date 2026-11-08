"""Bước 0.6 – xác thực: sys_users, sys_user_roles, sys_refresh_tokens (06 §3, §4; 01 §4).

- sys_users: cột chung 06 §2.4 (id BIGINT, created/updated_at/by, row_version), thời gian có múi giờ;
  khóa tạm (failed_login_count, locked_until); dự phòng 2FA: totp_secret NULL, is_2fa_enabled = false.
- sys_user_roles: nhiều vai trò cho một người dùng; CHECK giới hạn 8 vai trò của 00 §4 (thêm vai trò = migration mới).
- sys_refresh_tokens: chỉ lưu SHA-256 của token; family_id cho xoay vòng và phát hiện dùng lại.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sys_users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("failed_login_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_secret", sa.String(length=64), nullable=True),
        sa.Column("is_2fa_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("email IS NULL OR email = lower(email)", name=op.f("ck_sys_users_email_lowercase")),
        sa.CheckConstraint("failed_login_count >= 0", name=op.f("ck_sys_users_failed_login_count_non_negative")),
        sa.CheckConstraint("username = lower(username)", name=op.f("ck_sys_users_username_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_users")),
        sa.UniqueConstraint("email", name=op.f("uq_sys_users_email")),
        sa.UniqueConstraint("username", name=op.f("uq_sys_users_username")),
    )
    op.create_table(
        "sys_refresh_tokens",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.Column("replaced_by_id", sa.BigInteger(), nullable=True),
        sa.Column("created_ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["sys_refresh_tokens.id"],
            name=op.f("fk_sys_refresh_tokens_replaced_by_id_sys_refresh_tokens"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["sys_users.id"], name=op.f("fk_sys_refresh_tokens_user_id_sys_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sys_refresh_tokens_token_hash")),
    )
    op.create_index(op.f("ix_sys_refresh_tokens_family_id"), "sys_refresh_tokens", ["family_id"], unique=False)
    op.create_index(op.f("ix_sys_refresh_tokens_user_id"), "sys_refresh_tokens", ["user_id"], unique=False)
    op.create_table(
        "sys_user_roles",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "role IN ('ops_staff', 'manager', 'business_manager', 'learner', "
            "'instructor', 'content_editor', 'customer', 'admin')",
            name=op.f("ck_sys_user_roles_role_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["sys_users.id"], name=op.f("fk_sys_user_roles_user_id_sys_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "role", name=op.f("pk_sys_user_roles")),
    )


def downgrade() -> None:
    op.drop_table("sys_user_roles")
    op.drop_index(op.f("ix_sys_refresh_tokens_user_id"), table_name="sys_refresh_tokens")
    op.drop_index(op.f("ix_sys_refresh_tokens_family_id"), table_name="sys_refresh_tokens")
    op.drop_table("sys_refresh_tokens")
    op.drop_table("sys_users")
