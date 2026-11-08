"""Giai đoạn 3 – hợp đồng dịch vụ ops_service_contracts (02 §3.1, §6.1); bộ đếm mã nghiệp vụ sys_doc_sequences.

- ops_service_contracts: cột chung 06 §2.4; contract_no duy nhất (HD-yyyy-nnnn); customer_id → md_parties
  (RESTRICT) + bản chụp customer_name, customer_tax_code; signed_date, valid_from ≤ valid_to (DATE);
  mode (CHECK production/training); status (CHECK 02 §6.1); portal_enabled; điều khoản thanh toán, trách nhiệm.
- sys_doc_sequences: (prefix, year) → last_value – sinh số tăng dần theo năm trong cùng giao dịch (06 §2.4).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sys_doc_sequences",
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("prefix", "year", name=op.f("pk_sys_doc_sequences")),
    )
    op.create_table(
        "ops_service_contracts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("contract_no", sa.String(length=30), nullable=False),
        sa.Column("customer_contract_ref", sa.String(length=100), nullable=True),
        sa.Column("mode", sa.String(length=16), server_default="production", nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_name", sa.String(length=300), nullable=False),
        sa.Column("customer_tax_code", sa.String(length=32), nullable=True),
        sa.Column("signed_date", sa.Date(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("liability_terms", sa.Text(), nullable=True),
        sa.Column("payment_terms", sa.Text(), nullable=True),
        sa.Column("advance_required", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("payment_due_days", sa.Integer(), nullable=True),
        sa.Column("portal_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("status_reason", sa.String(length=500), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_ops_service_contracts_mode_valid")),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'suspended', 'expired', 'liquidating', 'closed')",
            name=op.f("ck_ops_service_contracts_status_valid"),
        ),
        sa.CheckConstraint(
            "payment_due_days IS NULL OR payment_due_days >= 0",
            name=op.f("ck_ops_service_contracts_payment_due_days_non_negative"),
        ),
        sa.CheckConstraint("valid_from <= valid_to", name=op.f("ck_ops_service_contracts_valid_period")),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["md_parties.id"],
            name=op.f("fk_ops_service_contracts_customer_id_md_parties"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_service_contracts")),
        sa.UniqueConstraint("contract_no", name=op.f("uq_ops_service_contracts_contract_no")),
    )
    op.create_index(
        "ix_ops_service_contracts_customer_status", "ops_service_contracts", ["customer_id", "status"], unique=False
    )
    op.create_index(
        "ix_ops_service_contracts_status_valid_to", "ops_service_contracts", ["status", "valid_to"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ops_service_contracts_status_valid_to", table_name="ops_service_contracts")
    op.drop_index("ix_ops_service_contracts_customer_status", table_name="ops_service_contracts")
    op.drop_table("ops_service_contracts")
    op.drop_table("sys_doc_sequences")
