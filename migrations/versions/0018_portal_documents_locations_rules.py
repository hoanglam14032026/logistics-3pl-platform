"""Giai đoạn 17 – khách hàng gửi chứng từ qua cổng, lô tự quay về docs_collecting, điểm đi / đến theo md_locations,
quy tắc chứng từ bắt buộc.

- ops_documents.channel (staff | portal).
- ops_shipments.origin_location_id, destination_location_id (FK md_locations) – bắt buộc khi INSERT (trigger); lô cũ
  giữ NULL + văn bản cũ (cột văn bản giờ là bản chụp "mã – tên").
- ops_document_requirements (seed: B/L cho đường biển, AWB cho hàng không, hóa đơn + phiếu đóng gói cho nhập / xuất
  khẩu, lệnh giao hàng cho nội địa – CẦN XÁC NHẬN); ops_document_check_items.source (manual | auto), rule_id.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DOC_TYPES = ", ".join(
    f"'{t}'"
    for t in (
        "bill_of_lading",
        "air_waybill",
        "commercial_invoice",
        "packing_list",
        "certificate_of_origin",
        "customs_declaration",
        "delivery_order",
        "sales_contract",
        "other",
    )
)


def upgrade() -> None:
    op.create_table(
        "ops_document_requirements",
        sa.Column("transport_mode", sa.String(length=16), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=True),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('import', 'export', 'domestic')",
            name=op.f("ck_ops_document_requirements_direction_valid"),
        ),
        sa.CheckConstraint(
            f"doc_type IN ({_DOC_TYPES})",
            name=op.f("ck_ops_document_requirements_doc_type_valid"),
        ),
        sa.CheckConstraint(
            "transport_mode IS NULL OR transport_mode IN ('sea_fcl', 'sea_lcl', 'air', 'road', 'rail', 'multimodal')",
            name=op.f("ck_ops_document_requirements_transport_mode_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_document_requirements")),
        sa.UniqueConstraint(
            "transport_mode",
            "direction",
            "doc_type",
            name="uq_ops_document_requirements_rule",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.add_column(
        "ops_document_check_items", sa.Column("source", sa.String(length=8), server_default="manual", nullable=False)
    )
    op.add_column("ops_document_check_items", sa.Column("rule_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_ops_document_check_items_rule_id_ops_document_requirements"),
        "ops_document_check_items",
        "ops_document_requirements",
        ["rule_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("ops_documents", sa.Column("channel", sa.String(length=8), server_default="staff", nullable=False))
    op.add_column("ops_shipments", sa.Column("origin_location_id", sa.BigInteger(), nullable=True))
    op.add_column("ops_shipments", sa.Column("destination_location_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_ops_shipments_origin_location_id_md_locations"),
        "ops_shipments",
        "md_locations",
        ["origin_location_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_ops_shipments_destination_location_id_md_locations"),
        "ops_shipments",
        "md_locations",
        ["destination_location_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        op.f("ck_ops_documents_channel_valid"), "ops_documents", "channel IN ('staff', 'portal')"
    )
    op.create_check_constraint(
        op.f("ck_ops_document_check_items_source_valid"), "ops_document_check_items", "source IN ('manual', 'auto')"
    )
    op.create_check_constraint(
        op.f("ck_ops_document_check_items_auto_from_rule"),
        "ops_document_check_items",
        "source = 'manual' OR (rule_id IS NOT NULL AND outcome = 'missing')",
    )
    op.execute(
        """
        CREATE FUNCTION ops_shipments_require_locations() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.origin_location_id IS NULL OR NEW.destination_location_id IS NULL THEN
                RAISE EXCEPTION 'Lô mới phải có origin_location_id, destination_location_id (md_locations)';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER ops_shipments_require_locations BEFORE INSERT ON ops_shipments
            FOR EACH ROW EXECUTE FUNCTION ops_shipments_require_locations();
        """
    )
    rules = sa.table(
        "ops_document_requirements",
        sa.column("transport_mode", sa.String),
        sa.column("direction", sa.String),
        sa.column("doc_type", sa.String),
        sa.column("note", sa.String),
    )
    op.bulk_insert(
        rules,
        [
            {
                "transport_mode": "sea_fcl",
                "direction": None,
                "doc_type": "bill_of_lading",
                "note": "Vận đơn đường biển",
            },
            {
                "transport_mode": "sea_lcl",
                "direction": None,
                "doc_type": "bill_of_lading",
                "note": "Vận đơn đường biển",
            },
            {"transport_mode": "air", "direction": None, "doc_type": "air_waybill", "note": "Vận đơn hàng không"},
            {
                "transport_mode": None,
                "direction": "import",
                "doc_type": "commercial_invoice",
                "note": "Hóa đơn thương mại",
            },
            {"transport_mode": None, "direction": "import", "doc_type": "packing_list", "note": "Phiếu đóng gói"},
            {
                "transport_mode": None,
                "direction": "export",
                "doc_type": "commercial_invoice",
                "note": "Hóa đơn thương mại",
            },
            {"transport_mode": None, "direction": "export", "doc_type": "packing_list", "note": "Phiếu đóng gói"},
            {"transport_mode": None, "direction": "domestic", "doc_type": "delivery_order", "note": "Lệnh giao hàng"},
        ],
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER ops_shipments_require_locations ON ops_shipments;
        DROP FUNCTION ops_shipments_require_locations();
        """
    )
    op.drop_constraint("ck_ops_document_check_items_auto_from_rule", "ops_document_check_items", type_="check")
    op.drop_constraint("ck_ops_document_check_items_source_valid", "ops_document_check_items", type_="check")
    op.drop_constraint("ck_ops_documents_channel_valid", "ops_documents", type_="check")
    op.drop_constraint(
        op.f("fk_ops_shipments_destination_location_id_md_locations"), "ops_shipments", type_="foreignkey"
    )
    op.drop_constraint(op.f("fk_ops_shipments_origin_location_id_md_locations"), "ops_shipments", type_="foreignkey")
    op.drop_column("ops_shipments", "destination_location_id")
    op.drop_column("ops_shipments", "origin_location_id")
    op.drop_column("ops_documents", "channel")
    op.drop_constraint(
        op.f("fk_ops_document_check_items_rule_id_ops_document_requirements"),
        "ops_document_check_items",
        type_="foreignkey",
    )
    op.drop_column("ops_document_check_items", "rule_id")
    op.drop_column("ops_document_check_items", "source")
    op.drop_table("ops_document_requirements")
