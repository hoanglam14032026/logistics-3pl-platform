"""Giai đoạn 19 – phụ lục hợp đồng (duyệt 2 người), chấm dứt trước hạn, quyết toán BR-110.

- ops_contract_amendments (CHECK: người duyệt 2 khác người lập và người duyệt 1), ops_contract_terminations (một đề
  xuất đang chờ mỗi hợp đồng; người duyệt khác người đề xuất), ops_contract_settlements (một quyết toán `final` mỗi hợp
  đồng; CHECK công thức số còn phải thanh toán; người duyệt khác người lập). Không xóa (trigger).
- asm_fact_settlements (Luồng 4).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asm_fact_settlements",
        sa.Column("settlement_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("settlement_no", sa.String(length=30), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("invoice_count", sa.Integer(), nullable=False),
        sa.Column("invoice_total", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("sla_deduction", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("compensation", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("balance_due", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("settlement_id", name=op.f("pk_asm_fact_settlements")),
    )
    op.create_index(op.f("ix_asm_fact_settlements_contract_id"), "asm_fact_settlements", ["contract_id"], unique=False)
    op.create_table(
        "ops_contract_settlements",
        sa.Column("settlement_no", sa.String(length=30), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_type", sa.String(length=16), server_default="final", nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("invoice_count", sa.Integer(), nullable=False),
        sa.Column("invoice_total", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("other_currency_totals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sla_deduction", sa.Numeric(precision=18, scale=2), server_default="0", nullable=False),
        sa.Column("deduction_note", sa.String(length=1000), nullable=True),
        sa.Column("compensation", sa.Numeric(precision=18, scale=2), server_default="0", nullable=False),
        sa.Column("paid_amount", sa.Numeric(precision=18, scale=2), server_default="0", nullable=False),
        sa.Column("balance_due", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("kpi_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("prepared_by", sa.BigInteger(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_ops_contract_settlements_currency_iso")),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'rejected')",
            name=op.f("ck_ops_contract_settlements_status_valid"),
        ),
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by <> prepared_by",
            name=op.f("ck_ops_contract_settlements_approver_not_preparer"),
        ),
        sa.CheckConstraint(
            "balance_due = invoice_total - sla_deduction - compensation - paid_amount",
            name=op.f("ck_ops_contract_settlements_balance_formula"),
        ),
        sa.CheckConstraint(
            "invoice_total >= 0 AND sla_deduction >= 0 AND compensation >= 0 AND paid_amount >= 0",
            name=op.f("ck_ops_contract_settlements_amounts_valid"),
        ),
        sa.CheckConstraint(
            "sla_deduction = 0 OR deduction_note IS NOT NULL",
            name=op.f("ck_ops_contract_settlements_deduction_has_note"),
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_contract_settlements_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_contract_settlements")),
        sa.UniqueConstraint("contract_id", "settlement_type", name="uq_ops_contract_settlements_type"),
        sa.UniqueConstraint("settlement_no", name=op.f("uq_ops_contract_settlements_settlement_no")),
    )
    op.create_table(
        "ops_contract_terminations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("requested_by", sa.BigInteger(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("cancelled_shipment_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("open_shipment_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "status = 'pending' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name=op.f("ck_ops_contract_terminations_decided"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name=op.f("ck_ops_contract_terminations_status_valid")
        ),
        sa.CheckConstraint(
            "decided_by IS NULL OR decided_by <> requested_by",
            name=op.f("ck_ops_contract_terminations_decider_not_requester"),
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_contract_terminations_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_contract_terminations")),
    )
    op.create_index(
        "uq_ops_contract_terminations_pending",
        "ops_contract_terminations",
        ["contract_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "ops_contract_amendments",
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("amendment_no", sa.String(length=10), nullable=False),
        sa.Column("base_contract_version", sa.Integer(), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("attachment_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("first_approved_by", sa.BigInteger(), nullable=True),
        sa.Column("first_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("second_approved_by", sa.BigInteger(), nullable=True),
        sa.Column("second_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("applied_version", sa.Integer(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status <> 'effective' OR (second_approved_by IS NOT NULL AND applied_version IS NOT NULL)",
            name=op.f("ck_ops_contract_amendments_effective_has_two_approvals"),
        ),
        sa.CheckConstraint(
            "status IN ('pending_approval', 'pending_second_approval', 'effective', 'rejected', 'withdrawn')",
            name=op.f("ck_ops_contract_amendments_status_valid"),
        ),
        sa.CheckConstraint(
            "second_approved_by IS NULL "
            "OR (second_approved_by <> first_approved_by AND second_approved_by <> created_by)",
            name=op.f("ck_ops_contract_amendments_four_eyes"),
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["ops_contract_attachments.id"],
            name=op.f("fk_ops_contract_amendments_attachment_id_ops_contract_attachments"),
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_contract_amendments_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_contract_amendments")),
        sa.UniqueConstraint("contract_id", "amendment_no", name="uq_ops_contract_amendments_no"),
    )
    op.create_index(
        "ix_ops_contract_amendments_contract_status", "ops_contract_amendments", ["contract_id", "status"], unique=False
    )

    op.execute(
        """
        CREATE FUNCTION g19_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% không được xóa', TG_TABLE_NAME;
        END $$;
        CREATE TRIGGER ops_contract_amendments_no_delete BEFORE DELETE ON ops_contract_amendments
            FOR EACH ROW EXECUTE FUNCTION g19_no_delete();
        CREATE TRIGGER ops_contract_terminations_no_delete BEFORE DELETE ON ops_contract_terminations
            FOR EACH ROW EXECUTE FUNCTION g19_no_delete();
        CREATE TRIGGER ops_contract_settlements_no_delete BEFORE DELETE ON ops_contract_settlements
            FOR EACH ROW EXECUTE FUNCTION g19_no_delete();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER ops_contract_settlements_no_delete ON ops_contract_settlements;
        DROP TRIGGER ops_contract_terminations_no_delete ON ops_contract_terminations;
        DROP TRIGGER ops_contract_amendments_no_delete ON ops_contract_amendments;
        DROP FUNCTION g19_no_delete();
        """
    )
    op.drop_index("ix_ops_contract_amendments_contract_status", table_name="ops_contract_amendments")
    op.drop_table("ops_contract_amendments")
    op.drop_index(
        "uq_ops_contract_terminations_pending",
        table_name="ops_contract_terminations",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("ops_contract_terminations")
    op.drop_table("ops_contract_settlements")
    op.drop_index(op.f("ix_asm_fact_settlements_contract_id"), table_name="asm_fact_settlements")
    op.drop_table("asm_fact_settlements")
