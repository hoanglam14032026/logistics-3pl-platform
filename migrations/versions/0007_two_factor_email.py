"""Giai đoạn 5 – xác thực 2 lớp TOTP, thư mời đặt mật khẩu, quên/đặt lại mật khẩu (06 §3; 02 §9.2).

- sys_user_tokens: token dùng một lần gửi qua email (invite, password_reset) – chỉ lưu SHA-256, expires_at, used_at,
  revoked_at.
- sys_users: totp_enabled_at, totp_last_step (chặn dùng lại mã). totp_secret, is_2fa_enabled có từ 0002.
- sys_refresh_tokens.mfa: phiên đã qua 2 lớp.

Khi nâng cấp hệ thống đang chạy: refresh token hiện có mang mfa = false → tài khoản bắt buộc 2FA (mặc định customer)
phải đăng nhập lại và thiết lập 2FA; tài khoản khác không ảnh hưởng.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sys_user_tokens",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("purpose IN ('invite', 'password_reset')", name=op.f("ck_sys_user_tokens_purpose_valid")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["sys_users.id"], name=op.f("fk_sys_user_tokens_user_id_sys_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_user_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sys_user_tokens_token_hash")),
    )
    op.create_index("ix_sys_user_tokens_user_purpose", "sys_user_tokens", ["user_id", "purpose"], unique=False)
    op.add_column("sys_refresh_tokens", sa.Column("mfa", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("sys_users", sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sys_users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("sys_users", "totp_last_step")
    op.drop_column("sys_users", "totp_enabled_at")
    op.drop_column("sys_refresh_tokens", "mfa")
    op.drop_index("ix_sys_user_tokens_user_purpose", table_name="sys_user_tokens")
    op.drop_table("sys_user_tokens")
