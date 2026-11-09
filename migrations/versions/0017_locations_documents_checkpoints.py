"""Giai đoạn 16 – danh mục địa điểm, chứng từ lô + kiểm tra chứng từ (BR-103), điểm kiểm soát của lô.

- md_locations (cảng, sân bay, ICD, kho, cửa khẩu, điểm giao hàng; tọa độ; party_id).
- ops_documents (phiên bản, bản hiện hành duy nhất mỗi loại – không xóa), ops_document_checks (một lần `pending` mỗi
  lô), ops_document_check_items (chỉ thêm).
- ops_checkpoints (mỗi loại một điểm / lô), ops_checkpoint_evaluations (chỉ thêm).
- asm_fact_checkpoints: `source` (control | ops) vào khóa chính, `checkpoint_type`; task_id / step_code cho phép null.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
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
        "md_locations",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("location_type", sa.String(length=16), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("party_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL)", name=op.f("ck_md_locations_archived_at_matches_status")
        ),
        sa.CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9-]{1,19}$'", name=op.f("ck_md_locations_code_format")),
        sa.CheckConstraint("country_code ~ '^[A-Z]{2}$'", name=op.f("ck_md_locations_country_code_iso2")),
        sa.CheckConstraint(
            "location_type IN ('seaport', 'airport', 'icd', 'warehouse', 'border_gate', 'delivery_point')",
            name=op.f("ck_md_locations_location_type_valid"),
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_md_locations_status_valid")),
        sa.CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name=op.f("ck_md_locations_coordinates_pair")),
        sa.CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90", name=op.f("ck_md_locations_latitude_range")
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180", name=op.f("ck_md_locations_longitude_range")
        ),
        sa.ForeignKeyConstraint(
            ["party_id"], ["md_parties.id"], name=op.f("fk_md_locations_party_id_md_parties"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_md_locations")),
        sa.UniqueConstraint("code", name=op.f("uq_md_locations_code")),
    )
    op.create_index("ix_md_locations_party_id", "md_locations", ["party_id"], unique=False)
    op.create_table(
        "ops_checkpoints",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("checkpoint_type", sa.String(length=24), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.BigInteger(), nullable=True),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("actual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "(status = 'pending') = (attempts = 0)", name=op.f("ck_ops_checkpoints_attempts_match_status")
        ),
        sa.CheckConstraint(
            "checkpoint_type IN ('cargo_receipt', 'customs_clearance', 'warehouse_release', 'delivery')",
            name=op.f("ck_ops_checkpoints_checkpoint_type_valid"),
        ),
        sa.CheckConstraint("status IN ('pending', 'passed', 'failed')", name=op.f("ck_ops_checkpoints_status_valid")),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["md_locations.id"],
            name=op.f("fk_ops_checkpoints_location_id_md_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["ops_shipments.id"],
            name=op.f("fk_ops_checkpoints_shipment_id_ops_shipments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_checkpoints")),
        sa.UniqueConstraint("shipment_id", "checkpoint_type", name="uq_ops_checkpoints_type"),
    )
    op.create_index(op.f("ix_ops_checkpoints_shipment_id"), "ops_checkpoints", ["shipment_id"], unique=False)
    op.create_table(
        "ops_documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="current", nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("uploaded_by", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            f"doc_type IN ({_DOC_TYPES})",
            name=op.f("ck_ops_documents_doc_type_valid"),
        ),
        sa.CheckConstraint("status IN ('current', 'superseded')", name=op.f("ck_ops_documents_status_valid")),
        sa.CheckConstraint("version_no >= 1", name=op.f("ck_ops_documents_version_positive")),
        sa.ForeignKeyConstraint(
            ["file_id"], ["sys_files.id"], name=op.f("fk_ops_documents_file_id_sys_files"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["ops_shipments.id"],
            name=op.f("fk_ops_documents_shipment_id_ops_shipments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_documents")),
        sa.UniqueConstraint("shipment_id", "doc_type", "version_no", name="uq_ops_documents_version"),
    )
    op.create_index(op.f("ix_ops_documents_shipment_id"), "ops_documents", ["shipment_id"], unique=False)
    op.create_index(
        "uq_ops_documents_current",
        "ops_documents",
        ["shipment_id", "doc_type"],
        unique=True,
        postgresql_where=sa.text("status = 'current'"),
    )
    op.create_table(
        "ops_checkpoint_evaluations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("checkpoint_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(length=8), nullable=False),
        sa.Column("actual_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("declaration_no", sa.String(length=40), nullable=True),
        sa.Column("customs_channel", sa.String(length=8), nullable=True),
        sa.Column("customs_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_by", sa.BigInteger(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "customs_channel IS NULL OR customs_channel IN ('green', 'yellow', 'red')",
            name=op.f("ck_ops_checkpoint_evaluations_channel_valid"),
        ),
        sa.CheckConstraint(
            "result = 'passed' OR note IS NOT NULL", name=op.f("ck_ops_checkpoint_evaluations_failed_has_note")
        ),
        sa.CheckConstraint("result IN ('passed', 'failed')", name=op.f("ck_ops_checkpoint_evaluations_result_valid")),
        sa.CheckConstraint(
            "customs_submitted_at IS NULL OR customs_submitted_at <= actual_at",
            name=op.f("ck_ops_checkpoint_evaluations_customs_submitted_before"),
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["ops_checkpoints.id"],
            name=op.f("fk_ops_checkpoint_evaluations_checkpoint_id_ops_checkpoints"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_checkpoint_evaluations")),
        sa.UniqueConstraint("checkpoint_id", "attempt_no", name="uq_ops_checkpoint_evaluations_attempt"),
    )
    op.create_table(
        "ops_document_checks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("run_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("result", sa.String(length=16), nullable=True),
        sa.Column("blocking_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("document_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("triggered_by_document_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("checked_by", sa.BigInteger(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'completed') = (result IS NOT NULL AND checked_at IS NOT NULL)",
            name=op.f("ck_ops_document_checks_completed"),
        ),
        sa.CheckConstraint(
            "result IS NULL OR result IN ('passed', 'failed')", name=op.f("ck_ops_document_checks_result_valid")
        ),
        sa.CheckConstraint("status IN ('pending', 'completed')", name=op.f("ck_ops_document_checks_status_valid")),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["ops_shipments.id"],
            name=op.f("fk_ops_document_checks_shipment_id_ops_shipments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by_document_id"],
            ["ops_documents.id"],
            name=op.f("fk_ops_document_checks_triggered_by_document_id_ops_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_document_checks")),
        sa.UniqueConstraint("shipment_id", "run_no", name="uq_ops_document_checks_run"),
    )
    op.create_index(
        "uq_ops_document_checks_pending",
        "ops_document_checks",
        ["shipment_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "ops_document_check_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("check_id", sa.BigInteger(), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "(outcome = 'missing') = (document_id IS NULL)",
            name=op.f("ck_ops_document_check_items_document_matches_outcome"),
        ),
        sa.CheckConstraint(
            f"doc_type IN ({_DOC_TYPES})",
            name=op.f("ck_ops_document_check_items_doc_type_valid"),
        ),
        sa.CheckConstraint(
            "outcome = 'ok' OR reason IS NOT NULL", name=op.f("ck_ops_document_check_items_reason_required")
        ),
        sa.CheckConstraint(
            "outcome IN ('ok', 'missing', 'invalid')", name=op.f("ck_ops_document_check_items_outcome_valid")
        ),
        sa.ForeignKeyConstraint(
            ["check_id"],
            ["ops_document_checks.id"],
            name=op.f("fk_ops_document_check_items_check_id_ops_document_checks"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["ops_documents.id"],
            name=op.f("fk_ops_document_check_items_document_id_ops_documents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_document_check_items")),
        sa.UniqueConstraint("check_id", "doc_type", name="uq_ops_document_check_items_type"),
    )
    op.add_column(
        "asm_fact_checkpoints",
        sa.Column("source", sa.String(length=8), server_default=sa.text("'control'"), nullable=False),
    )
    op.add_column("asm_fact_checkpoints", sa.Column("checkpoint_type", sa.String(length=24), nullable=True))
    op.alter_column("asm_fact_checkpoints", "task_id", existing_type=sa.BIGINT(), nullable=True)
    op.alter_column("asm_fact_checkpoints", "step_code", existing_type=sa.VARCHAR(length=20), nullable=True)
    op.create_check_constraint(
        op.f("ck_asm_fact_checkpoints_source_valid"), "asm_fact_checkpoints", "source IN ('control', 'ops')"
    )
    op.drop_constraint("pk_asm_fact_checkpoints", "asm_fact_checkpoints", type_="primary")
    op.create_primary_key(
        op.f("pk_asm_fact_checkpoints"), "asm_fact_checkpoints", ["source", "checkpoint_id", "attempt_no"]
    )
    op.execute(
        """
        CREATE FUNCTION ops_g16_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% chỉ được thêm (không sửa / xóa)', TG_TABLE_NAME;
        END $$;
        CREATE TRIGGER ops_checkpoint_evaluations_append_only BEFORE UPDATE OR DELETE ON ops_checkpoint_evaluations
            FOR EACH ROW EXECUTE FUNCTION ops_g16_append_only();
        CREATE TRIGGER ops_document_check_items_append_only BEFORE UPDATE OR DELETE ON ops_document_check_items
            FOR EACH ROW EXECUTE FUNCTION ops_g16_append_only();
        CREATE FUNCTION ops_g16_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% không được xóa (BR-03)', TG_TABLE_NAME;
        END $$;
        CREATE TRIGGER ops_documents_no_delete BEFORE DELETE ON ops_documents
            FOR EACH ROW EXECUTE FUNCTION ops_g16_no_delete();
        CREATE TRIGGER ops_document_checks_no_delete BEFORE DELETE ON ops_document_checks
            FOR EACH ROW EXECUTE FUNCTION ops_g16_no_delete();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER ops_document_checks_no_delete ON ops_document_checks;
        DROP TRIGGER ops_documents_no_delete ON ops_documents;
        DROP FUNCTION ops_g16_no_delete();
        DROP TRIGGER ops_document_check_items_append_only ON ops_document_check_items;
        DROP TRIGGER ops_checkpoint_evaluations_append_only ON ops_checkpoint_evaluations;
        DROP FUNCTION ops_g16_append_only();
        DELETE FROM asm_fact_checkpoints WHERE source = 'ops';
        """
    )
    op.drop_constraint("pk_asm_fact_checkpoints", "asm_fact_checkpoints", type_="primary")
    op.create_primary_key(op.f("pk_asm_fact_checkpoints"), "asm_fact_checkpoints", ["checkpoint_id", "attempt_no"])
    op.drop_constraint("ck_asm_fact_checkpoints_source_valid", "asm_fact_checkpoints", type_="check")
    op.alter_column("asm_fact_checkpoints", "step_code", existing_type=sa.VARCHAR(length=20), nullable=False)
    op.alter_column("asm_fact_checkpoints", "task_id", existing_type=sa.BIGINT(), nullable=False)
    op.drop_column("asm_fact_checkpoints", "checkpoint_type")
    op.drop_column("asm_fact_checkpoints", "source")
    op.drop_table("ops_document_check_items")
    op.drop_index(
        "uq_ops_document_checks_pending",
        table_name="ops_document_checks",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("ops_document_checks")
    op.drop_table("ops_checkpoint_evaluations")
    op.drop_index(
        "uq_ops_documents_current", table_name="ops_documents", postgresql_where=sa.text("status = 'current'")
    )
    op.drop_index(op.f("ix_ops_documents_shipment_id"), table_name="ops_documents")
    op.drop_table("ops_documents")
    op.drop_index(op.f("ix_ops_checkpoints_shipment_id"), table_name="ops_checkpoints")
    op.drop_table("ops_checkpoints")
    op.drop_index("ix_md_locations_party_id", table_name="md_locations")
    op.drop_table("md_locations")
