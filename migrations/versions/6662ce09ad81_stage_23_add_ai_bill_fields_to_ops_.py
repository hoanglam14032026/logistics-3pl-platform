"""stage 23: add ai bill fields to ops_documents (đã bỏ ở 0022 – giữ để chuỗi migration liền mạch)

Revision ID: 6662ce09ad81
Revises: 0021
Create Date: 2026-11-11 14:55:28.837793
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6662ce09ad81"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ops_documents", sa.Column("bill_image_path", sa.String(), nullable=True))
    op.add_column("ops_documents", sa.Column("ai_parsed", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("ops_documents", sa.Column("ai_confidence", sa.Float(), nullable=True))
    op.add_column("ops_documents", sa.Column("ai_raw", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ops_documents", "ai_raw")
    op.drop_column("ops_documents", "ai_confidence")
    op.drop_column("ops_documents", "ai_parsed")
    op.drop_column("ops_documents", "bill_image_path")
