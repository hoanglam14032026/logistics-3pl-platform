"""Giai đoạn 4 – chi tiết hợp đồng (phạm vi, SLA, biểu giá theo phiên bản; bản chụp phiên bản), file đính kèm,
thanh lý/đóng (02 §3.1, §3.8, §3.9.2, §6.1; 06 §2.6).

- sys_files: metadata file (storage_key tương đối, MIME theo nội dung, SHA-256, chủ sở hữu owner_type/owner_id).
- ops_contract_scopes / _sla / _rates: theo (contract_id, contract_version); tiền NUMERIC(18,2), số lượng NUMERIC(18,3).
- ops_contract_versions: bản chụp JSON từng phiên bản; ops_contract_attachments: file của hợp đồng.
- ops_service_contracts: contract_version, liquidation_started_at, closed_at, closed_by.
- Trigger viết tay (autogenerate không sinh trigger): hợp đồng `closed` không sửa/xóa được; bảng con, file đính kèm,
  phiên bản của hợp đồng `closed` không thêm/sửa/xóa được – kể cả khi lỗi mã nguồn hoặc sửa tay trong CSDL.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHILD_TABLES = (
    "ops_contract_scopes",
    "ops_contract_sla",
    "ops_contract_rates",
    "ops_contract_attachments",
    "ops_contract_versions",
)


def upgrade() -> None:
    op.create_table(
        "sys_files",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("storage_key", sa.String(length=200), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("owner_type", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_files")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_sys_files_storage_key")),
    )
    op.create_index("ix_sys_files_owner", "sys_files", ["owner_type", "owner_id"], unique=False)
    op.create_table(
        "ops_contract_attachments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("customer_visible", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('signed_contract', 'amendment', 'liquidation_minutes', 'other')",
            name=op.f("ck_ops_contract_attachments_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_contract_attachments_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["sys_files.id"],
            name=op.f("fk_ops_contract_attachments_file_id_sys_files"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_contract_attachments")),
        sa.UniqueConstraint("file_id", name=op.f("uq_ops_contract_attachments_file_id")),
    )
    op.create_table(
        "ops_contract_rates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("rate_code", sa.String(length=40), nullable=False),
        sa.Column("service_code", sa.String(length=40), nullable=False),
        sa.Column("charge_name", sa.String(length=200), nullable=False),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("min_charge", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("tax_rate", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "basis IN ('per_shipment', 'per_container_20', 'per_container_40', 'per_kg', "
            "'per_cbm', 'per_declaration', 'per_pallet_day', 'per_trip', 'lump_sum')",
            name=op.f("ck_ops_contract_rates_basis_valid"),
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_ops_contract_rates_currency_iso4217")),
        sa.CheckConstraint(
            "min_charge IS NULL OR min_charge >= 0", name=op.f("ck_ops_contract_rates_min_charge_non_negative")
        ),
        sa.CheckConstraint("tax_rate >= 0 AND tax_rate <= 100", name=op.f("ck_ops_contract_rates_tax_rate_percent")),
        sa.CheckConstraint("unit_price >= 0", name=op.f("ck_ops_contract_rates_unit_price_non_negative")),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
            name=op.f("ck_ops_contract_rates_valid_period"),
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_contract_rates_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_contract_rates")),
        sa.UniqueConstraint("contract_id", "contract_version", "rate_code", name="rate_per_version"),
    )
    op.create_table(
        "ops_contract_scopes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("service_code", sa.String(length=40), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_contract_scopes_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_contract_scopes")),
        sa.UniqueConstraint("contract_id", "contract_version", "service_code", name="service_per_version"),
    )
    op.create_table(
        "ops_contract_sla",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("sla_code", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("target_value", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("kpi_code", sa.String(length=40), nullable=True),
        sa.Column("measure_level", sa.String(length=16), nullable=False),
        sa.Column("penalty_rule", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "measure_level IN ('per_shipment', 'per_period')", name=op.f("ck_ops_contract_sla_measure_level_valid")
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_contract_sla_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_contract_sla")),
        sa.UniqueConstraint("contract_id", "contract_version", "sla_code", name="sla_per_version"),
    )
    op.create_table(
        "ops_contract_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("amendment_ref", sa.String(length=30), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("attachment_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["ops_contract_attachments.id"],
            name=op.f("fk_ops_contract_versions_attachment_id_ops_contract_attachments"),
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_contract_versions_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_contract_versions")),
        sa.UniqueConstraint("contract_id", "contract_version", name="one_snapshot_per_version"),
    )
    op.add_column(
        "ops_service_contracts", sa.Column("contract_version", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "ops_service_contracts", sa.Column("liquidation_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("ops_service_contracts", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ops_service_contracts", sa.Column("closed_by", sa.BigInteger(), nullable=True))

    op.execute(
        """
        CREATE FUNCTION ops_contract_closed_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status = 'closed' THEN
                RAISE EXCEPTION 'Hợp đồng % đã đóng; không thay đổi được', OLD.contract_no
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER ops_service_contracts_closed_guard
        BEFORE UPDATE OR DELETE ON ops_service_contracts
        FOR EACH ROW EXECUTE FUNCTION ops_contract_closed_guard()
        """
    )
    op.execute(
        """
        CREATE FUNCTION ops_contract_child_closed_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            parent_status text;
        BEGIN
            SELECT status INTO parent_status FROM ops_service_contracts
            WHERE id = CASE TG_OP WHEN 'DELETE' THEN OLD.contract_id ELSE NEW.contract_id END;
            IF parent_status = 'closed' THEN
                RAISE EXCEPTION 'Hợp đồng đã đóng; không thay đổi được % ', TG_TABLE_NAME
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$
        """
    )
    for table in _CHILD_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_closed_guard BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION ops_contract_child_closed_guard()"
        )


def downgrade() -> None:
    for table in _CHILD_TABLES:
        op.execute(f"DROP TRIGGER {table}_closed_guard ON {table}")
    op.execute("DROP FUNCTION ops_contract_child_closed_guard()")
    op.execute("DROP TRIGGER ops_service_contracts_closed_guard ON ops_service_contracts")
    op.execute("DROP FUNCTION ops_contract_closed_guard()")
    op.drop_column("ops_service_contracts", "closed_by")
    op.drop_column("ops_service_contracts", "closed_at")
    op.drop_column("ops_service_contracts", "liquidation_started_at")
    op.drop_column("ops_service_contracts", "contract_version")
    op.drop_table("ops_contract_versions")
    op.drop_table("ops_contract_sla")
    op.drop_table("ops_contract_scopes")
    op.drop_table("ops_contract_rates")
    op.drop_table("ops_contract_attachments")
    op.drop_index("ix_sys_files_owner", table_name="sys_files")
    op.drop_table("sys_files")
