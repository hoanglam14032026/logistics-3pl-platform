"""Giai đoạn 18 – Luồng 3 đào tạo: hợp đồng đào tạo, người học, buổi học, điểm danh / hoàn thành; Luồng 4: dữ kiện
đào tạo + điểm buổi học (KPI-11/12/13).

- lrn_training_contracts, lrn_contract_learners, lrn_sessions, lrn_attendance.
- asm_fact_training_contracts, asm_fact_enrollments, asm_fact_training_sessions, asm_fact_learning_progress,
  asm_session_scores. Không xóa điểm, điểm danh (trigger).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asm_fact_enrollments",
        sa.Column("contract_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("learner_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("contract_id", "learner_id", name=op.f("pk_asm_fact_enrollments")),
    )
    op.create_index(op.f("ix_asm_fact_enrollments_learner_id"), "asm_fact_enrollments", ["learner_id"], unique=False)
    op.create_table(
        "asm_fact_learning_progress",
        sa.Column("session_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("learner_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("attendance", sa.String(length=16), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("session_id", "learner_id", name=op.f("pk_asm_fact_learning_progress")),
    )
    op.create_table(
        "asm_fact_training_contracts",
        sa.Column("contract_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("contract_no", sa.String(length=30), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("instructor_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("contract_id", name=op.f("pk_asm_fact_training_contracts")),
    )
    op.create_index(
        op.f("ix_asm_fact_training_contracts_instructor_id"),
        "asm_fact_training_contracts",
        ["instructor_id"],
        unique=False,
    )
    op.create_table(
        "asm_fact_training_sessions",
        sa.Column("session_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'scheduled'"), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_asm_fact_training_sessions")),
    )
    op.create_index(
        "ix_asm_fact_training_sessions_contract",
        "asm_fact_training_sessions",
        ["contract_id", "starts_at"],
        unique=False,
    )
    op.create_table(
        "asm_session_scores",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("learner_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.Column("seeded_discrepancies", sa.Integer(), nullable=True),
        sa.Column("detected_correct", sa.Integer(), nullable=True),
        sa.Column("false_reports", sa.Integer(), nullable=True),
        sa.Column("graded_by", sa.BigInteger(), nullable=False),
        sa.Column("graded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(seeded_discrepancies IS NULL) = (detected_correct IS NULL) "
            "AND (seeded_discrepancies IS NULL) = (false_reports IS NULL)",
            name=op.f("ck_asm_session_scores_discrepancy_fields_together"),
        ),
        sa.CheckConstraint("score BETWEEN 0 AND 10", name=op.f("ck_asm_session_scores_score_range")),
        sa.CheckConstraint(
            "seeded_discrepancies IS NULL OR (seeded_discrepancies > 0 AND detected_correct BETWEEN 0 AND "
            "seeded_discrepancies AND false_reports >= 0)",
            name=op.f("ck_asm_session_scores_discrepancy_counts_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["learner_id"],
            ["sys_users.id"],
            name=op.f("fk_asm_session_scores_learner_id_sys_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asm_session_scores")),
        sa.UniqueConstraint("session_id", "learner_id", name="uq_asm_session_scores_learner"),
    )
    op.create_index("ix_asm_session_scores_learner", "asm_session_scores", ["learner_id"], unique=False)
    op.create_table(
        "lrn_training_contracts",
        sa.Column("contract_no", sa.String(length=30), nullable=False),
        sa.Column("contract_type", sa.String(length=16), server_default="training", nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("instructor_id", sa.BigInteger(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("schedule", sa.String(length=500), nullable=False),
        sa.Column("tuition_fee", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="VND", nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("status_reason", sa.String(length=500), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_lrn_training_contracts_currency_iso4217")),
        sa.CheckConstraint(
            "status <> 'cancelled' OR status_reason IS NOT NULL",
            name=op.f("ck_lrn_training_contracts_cancel_has_reason"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'completed', 'cancelled')",
            name=op.f("ck_lrn_training_contracts_status_valid"),
        ),
        sa.CheckConstraint("start_date <= end_date", name=op.f("ck_lrn_training_contracts_period_valid")),
        sa.CheckConstraint("tuition_fee >= 0", name=op.f("ck_lrn_training_contracts_tuition_non_negative")),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["md_parties.id"],
            name=op.f("fk_lrn_training_contracts_customer_id_md_parties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instructor_id"],
            ["sys_users.id"],
            name=op.f("fk_lrn_training_contracts_instructor_id_sys_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lrn_training_contracts")),
        sa.UniqueConstraint("contract_no", name=op.f("uq_lrn_training_contracts_contract_no")),
    )
    op.create_index(
        "ix_lrn_training_contracts_customer", "lrn_training_contracts", ["customer_id", "status"], unique=False
    )
    op.create_index(
        "ix_lrn_training_contracts_instructor", "lrn_training_contracts", ["instructor_id", "status"], unique=False
    )
    op.create_table(
        "lrn_contract_learners",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("learner_id", sa.BigInteger(), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("enrolled_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["lrn_training_contracts.id"],
            name=op.f("fk_lrn_contract_learners_contract_id_lrn_training_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["learner_id"],
            ["sys_users.id"],
            name=op.f("fk_lrn_contract_learners_learner_id_sys_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lrn_contract_learners")),
        sa.UniqueConstraint("contract_id", "learner_id", name="uq_lrn_contract_learners_learner"),
    )
    op.create_index(op.f("ix_lrn_contract_learners_learner_id"), "lrn_contract_learners", ["learner_id"], unique=False)
    op.create_table(
        "lrn_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("topic", sa.String(length=300), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location_id", sa.BigInteger(), nullable=True),
        sa.Column("is_required", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="scheduled", nullable=False),
        sa.Column("cancel_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status <> 'cancelled' OR cancel_reason IS NOT NULL", name=op.f("ck_lrn_sessions_cancel_has_reason")
        ),
        sa.CheckConstraint("status IN ('scheduled', 'cancelled')", name=op.f("ck_lrn_sessions_status_valid")),
        sa.CheckConstraint("starts_at < ends_at", name=op.f("ck_lrn_sessions_time_valid")),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["lrn_training_contracts.id"],
            name=op.f("fk_lrn_sessions_contract_id_lrn_training_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["md_locations.id"],
            name=op.f("fk_lrn_sessions_location_id_md_locations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lrn_sessions")),
        sa.UniqueConstraint("contract_id", "seq", name="uq_lrn_sessions_seq"),
    )
    op.create_index(op.f("ix_lrn_sessions_contract_id"), "lrn_sessions", ["contract_id"], unique=False)
    op.create_table(
        "lrn_attendance",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("learner_id", sa.BigInteger(), nullable=False),
        sa.Column("attendance", sa.String(length=16), nullable=False),
        sa.Column("completion", sa.String(length=16), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("recorded_by", sa.BigInteger(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "attendance IN ('present', 'late', 'absent', 'excused')", name=op.f("ck_lrn_attendance_attendance_valid")
        ),
        sa.CheckConstraint(
            "completion IS DISTINCT FROM 'completed' OR attendance IN ('present', 'late')",
            name=op.f("ck_lrn_attendance_completed_present"),
        ),
        sa.CheckConstraint(
            "completion IS NULL OR completion IN ('completed', 'not_completed')",
            name=op.f("ck_lrn_attendance_completion_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["learner_id"], ["sys_users.id"], name=op.f("fk_lrn_attendance_learner_id_sys_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["lrn_sessions.id"],
            name=op.f("fk_lrn_attendance_session_id_lrn_sessions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lrn_attendance")),
        sa.UniqueConstraint("session_id", "learner_id", name="uq_lrn_attendance_learner"),
    )
    op.create_index(op.f("ix_lrn_attendance_learner_id"), "lrn_attendance", ["learner_id"], unique=False)

    op.execute(
        """
        CREATE FUNCTION g18_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% không được xóa', TG_TABLE_NAME;
        END $$;
        CREATE TRIGGER lrn_attendance_no_delete BEFORE DELETE ON lrn_attendance
            FOR EACH ROW EXECUTE FUNCTION g18_no_delete();
        CREATE TRIGGER lrn_training_contracts_no_delete BEFORE DELETE ON lrn_training_contracts
            FOR EACH ROW EXECUTE FUNCTION g18_no_delete();
        CREATE TRIGGER asm_session_scores_no_delete BEFORE DELETE ON asm_session_scores
            FOR EACH ROW EXECUTE FUNCTION g18_no_delete();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER asm_session_scores_no_delete ON asm_session_scores;
        DROP TRIGGER lrn_training_contracts_no_delete ON lrn_training_contracts;
        DROP TRIGGER lrn_attendance_no_delete ON lrn_attendance;
        DROP FUNCTION g18_no_delete();
        """
    )
    op.drop_index(op.f("ix_lrn_attendance_learner_id"), table_name="lrn_attendance")
    op.drop_table("lrn_attendance")
    op.drop_index(op.f("ix_lrn_sessions_contract_id"), table_name="lrn_sessions")
    op.drop_table("lrn_sessions")
    op.drop_index(op.f("ix_lrn_contract_learners_learner_id"), table_name="lrn_contract_learners")
    op.drop_table("lrn_contract_learners")
    op.drop_index("ix_lrn_training_contracts_instructor", table_name="lrn_training_contracts")
    op.drop_index("ix_lrn_training_contracts_customer", table_name="lrn_training_contracts")
    op.drop_table("lrn_training_contracts")
    op.drop_index("ix_asm_session_scores_learner", table_name="asm_session_scores")
    op.drop_table("asm_session_scores")
    op.drop_index("ix_asm_fact_training_sessions_contract", table_name="asm_fact_training_sessions")
    op.drop_table("asm_fact_training_sessions")
    op.drop_index(op.f("ix_asm_fact_training_contracts_instructor_id"), table_name="asm_fact_training_contracts")
    op.drop_table("asm_fact_training_contracts")
    op.drop_table("asm_fact_learning_progress")
    op.drop_index(op.f("ix_asm_fact_enrollments_learner_id"), table_name="asm_fact_enrollments")
    op.drop_table("asm_fact_enrollments")
