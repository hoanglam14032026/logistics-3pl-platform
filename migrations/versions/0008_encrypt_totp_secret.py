"""Giai đoạn 6 – mã hóa sys_users.totp_secret bằng Fernet (khóa TOTP_ENCRYPTION_KEY, khác JWT_SECRET).

- totp_secret: VARCHAR(64) → VARCHAR(255) (bản mã Fernet của secret 32 ký tự dài 140 ký tự).
- Dữ liệu: secret dạng rõ hiện có (từ 0007) được mã hóa bằng khóa đầu tiên của TOTP_ENCRYPTION_KEY; giá trị đã là bản
  mã (giải mã được) giữ nguyên – chạy lại an toàn.
- CHECK ck_sys_users_totp_secret_encrypted: chỉ nhận bản mã Fernet (tiền tố `gAAAAA`).

Downgrade giải mã về dạng rõ (cần đúng khóa) rồi thu cột về VARCHAR(64).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_sys_users_totp_secret_encrypted"


def _box() -> MultiFernet:
    return MultiFernet([Fernet(k.encode("ascii")) for k in get_settings().totp_encryption_keys])


def _rows(conn) -> list[tuple[int, str]]:
    return list(conn.execute(sa.text("SELECT id, totp_secret FROM sys_users WHERE totp_secret IS NOT NULL")))


def upgrade() -> None:
    op.alter_column("sys_users", "totp_secret", type_=sa.String(length=255), existing_nullable=True)
    conn = op.get_bind()
    rows = _rows(conn)
    if rows:
        box = _box()
        for user_id, value in rows:
            try:
                box.decrypt(value.encode("ascii"))
                continue  # đã mã hóa
            except (InvalidToken, UnicodeEncodeError):
                pass
            conn.execute(
                sa.text("UPDATE sys_users SET totp_secret = :v WHERE id = :id"),
                {"v": box.encrypt(value.encode("utf-8")).decode("ascii"), "id": user_id},
            )
    op.create_check_constraint(op.f(_CHECK), "sys_users", "totp_secret IS NULL OR totp_secret LIKE 'gAAAAA%'")


def downgrade() -> None:
    op.drop_constraint(op.f(_CHECK), "sys_users", type_="check")
    conn = op.get_bind()
    rows = _rows(conn)
    if rows:
        box = _box()
        for user_id, value in rows:
            conn.execute(
                sa.text("UPDATE sys_users SET totp_secret = :v WHERE id = :id"),
                {"v": box.decrypt(value.encode("ascii")).decode("utf-8"), "id": user_id},
            )
    op.alter_column("sys_users", "totp_secret", type_=sa.String(length=64), existing_nullable=True)
