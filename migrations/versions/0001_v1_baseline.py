"""Schema gốc v1.1.0: 4 bảng business_guides, training_documents, business_data_records, video_resources.

Giữ nguyên đúng như Base.metadata.create_all() đã tạo (kể cả các điểm chưa đạt 06 §2.4: DateTime không
múi giờ, cột cho phép NULL) – sửa ở migration sau, không sửa revision gốc. CSDL đã tạo bằng create_all
được app/core/migrations.py đánh dấu (stamp) ở revision này thay vì tạo lại.

Revision ID: 0001
Revises: –
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_data_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("record_type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("data_content", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_business_data_records_id"), "business_data_records", ["id"], unique=False)
    op.create_table(
        "business_guides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_business_guides_id"), "business_guides", ["id"], unique=False)
    op.create_table(
        "training_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("file_type", sa.String(length=50), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_training_documents_id"), "training_documents", ["id"], unique=False)
    op.create_table(
        "video_resources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("video_path", sa.String(length=500), nullable=False),
        sa.Column("thumbnail_path", sa.String(length=500), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_video_resources_id"), "video_resources", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_video_resources_id"), table_name="video_resources")
    op.drop_table("video_resources")
    op.drop_index(op.f("ix_training_documents_id"), table_name="training_documents")
    op.drop_table("training_documents")
    op.drop_index(op.f("ix_business_guides_id"), table_name="business_guides")
    op.drop_table("business_guides")
    op.drop_index(op.f("ix_business_data_records_id"), table_name="business_data_records")
    op.drop_table("business_data_records")
