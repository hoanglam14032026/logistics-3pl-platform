"""Giai đoạn 8 – lô hàng / vận đơn (02 §3.2, §6.2): ops_shipments, ops_shipment_status_history, ops_delivery_records.

- ops_shipments: thuộc hợp đồng (RESTRICT), số `LH-yyyy-nnnnnn` (sys_doc_sequences), trạng thái 02 §6.2, CHECK miền giá
  trị, Incoterms 2020 bắt buộc khi nhập/xuất khẩu, etd ≤ eta, hủy phải có lý do.
- Trigger viết tay (autogenerate không sinh trigger):
  - ops_shipments: không xóa (06 §2.5); lô `closed`/`cancelled` không sửa được.
  - ops_shipment_status_history, ops_delivery_records: chỉ ghi thêm (không sửa, không xóa) – dấu vết tiến độ, giao hàng.
- Sự kiện EV-101/111/112/142 ghi vào sys_audit_logs (đã có từ 0004) – không thêm bảng.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_shipments",
        sa.Column("shipment_no", sa.String(length=30), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("transport_mode", sa.String(length=16), nullable=False),
        sa.Column("incoterm", sa.String(length=3), nullable=True),
        sa.Column("incoterm_place", sa.String(length=200), nullable=True),
        sa.Column("shipper_id", sa.BigInteger(), nullable=False),
        sa.Column("shipper_name", sa.String(length=300), nullable=False),
        sa.Column("consignee_id", sa.BigInteger(), nullable=False),
        sa.Column("consignee_name", sa.String(length=300), nullable=False),
        sa.Column("notify_party_id", sa.BigInteger(), nullable=True),
        sa.Column("notify_party_name", sa.String(length=300), nullable=True),
        sa.Column("origin_location", sa.String(length=200), nullable=False),
        sa.Column("destination_location", sa.String(length=200), nullable=False),
        sa.Column("cargo_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("etd", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("required_delivery_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claims_preferential_tariff", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("priority", sa.String(length=16), server_default="normal", nullable=False),
        sa.Column("assignee_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("status_reason", sa.String(length=500), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "direction = 'domestic' OR incoterm IS NOT NULL",
            name=op.f("ck_ops_shipments_incoterm_required_cross_border"),
        ),
        sa.CheckConstraint(
            "direction IN ('import', 'export', 'domestic')", name=op.f("ck_ops_shipments_direction_valid")
        ),
        sa.CheckConstraint(
            "incoterm IS NULL OR incoterm IN "
            "('EXW', 'FCA', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP', 'FAS', 'FOB', 'CFR', 'CIF')",
            name=op.f("ck_ops_shipments_incoterm_valid"),
        ),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_ops_shipments_mode_valid")),
        sa.CheckConstraint("priority IN ('normal', 'high', 'urgent')", name=op.f("ck_ops_shipments_priority_valid")),
        sa.CheckConstraint(
            "status <> 'cancelled' OR status_reason IS NOT NULL", name=op.f("ck_ops_shipments_cancel_has_reason")
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'docs_collecting', 'docs_verified', 'in_execution', 'delivered', 'closed', "
            "'cancelled')",
            name=op.f("ck_ops_shipments_status_valid"),
        ),
        sa.CheckConstraint(
            "transport_mode IN ('sea_fcl', 'sea_lcl', 'air', 'road', 'rail', 'multimodal')",
            name=op.f("ck_ops_shipments_transport_mode_valid"),
        ),
        sa.CheckConstraint("etd IS NULL OR eta IS NULL OR etd <= eta", name=op.f("ck_ops_shipments_etd_before_eta")),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["sys_users.id"], name=op.f("fk_ops_shipments_assignee_id_sys_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["consignee_id"],
            ["md_parties.id"],
            name=op.f("fk_ops_shipments_consignee_id_md_parties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_shipments_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["md_parties.id"],
            name=op.f("fk_ops_shipments_customer_id_md_parties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["notify_party_id"],
            ["md_parties.id"],
            name=op.f("fk_ops_shipments_notify_party_id_md_parties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipper_id"], ["md_parties.id"], name=op.f("fk_ops_shipments_shipper_id_md_parties"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_shipments")),
        sa.UniqueConstraint("shipment_no", name=op.f("uq_ops_shipments_shipment_no")),
    )
    op.create_index("ix_ops_shipments_assignee_status", "ops_shipments", ["assignee_id", "status"], unique=False)
    op.create_index("ix_ops_shipments_contract_status", "ops_shipments", ["contract_id", "status"], unique=False)
    op.create_index("ix_ops_shipments_customer_status", "ops_shipments", ["customer_id", "status"], unique=False)
    op.create_table(
        "ops_delivery_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receiver_name", sa.String(length=200), nullable=False),
        sa.Column("in_full", sa.Boolean(), nullable=False),
        sa.Column("damage_free", sa.Boolean(), nullable=False),
        sa.Column("remarks", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["ops_shipments.id"],
            name=op.f("fk_ops_delivery_records_shipment_id_ops_shipments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_delivery_records")),
        sa.UniqueConstraint("shipment_id", name=op.f("uq_ops_delivery_records_shipment_id")),
    )
    op.create_table(
        "ops_shipment_status_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("changed_by", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["ops_shipments.id"],
            name=op.f("fk_ops_shipment_status_history_shipment_id_ops_shipments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_shipment_status_history")),
    )
    op.create_index(
        "ix_ops_shipment_status_history_shipment", "ops_shipment_status_history", ["shipment_id", "id"], unique=False
    )
    op.execute(
        """
        CREATE FUNCTION ops_shipments_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Không xóa lô hàng % (dùng thao tác hủy lô)', OLD.shipment_no
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status IN ('closed', 'cancelled') THEN
                RAISE EXCEPTION 'Lô hàng % đã %; không thay đổi được', OLD.shipment_no, OLD.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER ops_shipments_guard BEFORE UPDATE OR DELETE ON ops_shipments
            FOR EACH ROW EXECUTE FUNCTION ops_shipments_guard();

        CREATE FUNCTION ops_shipment_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Bảng % chỉ ghi thêm; không sửa, không xóa', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END $$;
        CREATE TRIGGER ops_shipment_status_history_append_only BEFORE UPDATE OR DELETE ON ops_shipment_status_history
            FOR EACH ROW EXECUTE FUNCTION ops_shipment_append_only();
        CREATE TRIGGER ops_delivery_records_append_only BEFORE UPDATE OR DELETE ON ops_delivery_records
            FOR EACH ROW EXECUTE FUNCTION ops_shipment_append_only();
        """
    )


def downgrade() -> None:
    op.drop_index("ix_ops_shipment_status_history_shipment", table_name="ops_shipment_status_history")
    op.drop_table("ops_shipment_status_history")
    op.drop_table("ops_delivery_records")
    op.drop_index("ix_ops_shipments_customer_status", table_name="ops_shipments")
    op.drop_index("ix_ops_shipments_contract_status", table_name="ops_shipments")
    op.drop_index("ix_ops_shipments_assignee_status", table_name="ops_shipments")
    op.drop_table("ops_shipments")  # trigger bị xóa theo bảng
    op.execute("DROP FUNCTION ops_shipments_guard(); DROP FUNCTION ops_shipment_append_only();")
