"""P2 - Luong 3: lrn_topics, lrn_resources, lrn_courses, lrn_course_items, lrn_progress (04 §2/§3).

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table(
        "lrn_topics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("topic_code", sa.String(8), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(1000)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_code", name="uq_lrn_topics_topic_code"),
    )
    op.create_table(
        "lrn_resources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.String(2000)),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("topic_code", sa.String(8)),
        sa.Column("level", sa.String(32)),
        sa.Column("status", sa.String(16), server_default="draft", nullable=False),
        sa.Column("detail", JSONB),
        sa.Column("created_by", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "resource_type IN ('GUIDE', 'ACADEMIC_DOC', 'VIDEO', 'INTERVIEW', 'CASE_STUDY', 'TEMPLATE', 'REGULATION')",
            name="ck_lrn_resources_resource_type_valid",
        ),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_lrn_resources_resource_status_valid"),
    )
    op.create_table(
        "lrn_courses",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("course_code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("owner_instructor_id", sa.BigInteger()),
        sa.Column("status", sa.String(16), server_default="draft", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_code", name="uq_lrn_courses_course_code"),
    )
    op.create_table(
        "lrn_course_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("course_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("is_required", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("min_completion", sa.Integer(), server_default="100", nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["lrn_courses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resource_id"], ["lrn_resources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "lrn_progress",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("learner_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("course_id", sa.BigInteger()),
        sa.Column("percent_complete", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_position_sec", sa.Integer()),
        sa.Column("first_viewed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["resource_id"], ["lrn_resources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["course_id"], ["lrn_courses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Seed 7 chu de T01..T07 (04 §3)
    op.execute(
        "INSERT INTO lrn_topics (topic_code, name) VALUES "
        "('T01', 'Hop dong va dieu kien thuong mai'), "
        "('T02', 'Chung tu thuong mai va van tai'), "
        "('T03', 'Thu tuc hai quan'), "
        "('T04', 'Van tai va giao nhan'), "
        "('T05', 'Kho bai va phan phoi'), "
        "('T06', 'Quan ly van hanh va kiem soat'), "
        "('T07', 'Do luong hieu qua') "
        "ON CONFLICT (topic_code) DO NOTHING"
    )

def downgrade() -> None:
    op.drop_table("lrn_progress")
    op.drop_table("lrn_course_items")
    op.drop_table("lrn_courses")
    op.drop_table("lrn_resources")
    op.drop_table("lrn_topics")