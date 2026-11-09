"""Giai đoạn 10 – hóa đơn thương mại, outbox sự kiện sys_events, duyệt 2 người cho job trích xuất chứng từ.

- ops_commercial_invoices (+ ops_commercial_invoice_items): gắn hợp đồng (bắt buộc) / lô hàng, tổng tiền NUMERIC(18,2),
  source_doc_job_id (FK, UNIQUE). Không xóa hóa đơn; dòng hàng chỉ ghi thêm (trigger).
- sys_events: outbox sự kiện (01 §4, N5) – nội dung không sửa/xóa (trigger), chỉ trạng thái gửi thay đổi. Chuyển các
  sự kiện EV-101/111/112/142 đã ghi trong sys_audit_logs (Giai đoạn 8–9) sang outbox ở trạng thái pending (gửi khi
  API/worker khởi động).
- sys_doc_jobs: requires_dual_approval, first_approved_by/at; trạng thái pending_second_approval (cột status → 32 ký
  tự); CHECK người duyệt thứ 2 khác người duyệt đầu và khác người tải lên.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sys_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_code", sa.String(length=32), nullable=False),
        sa.Column("event_name", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("consumers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deliveries", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(status = 'sent') = (dispatched_at IS NOT NULL)", name=op.f("ck_sys_events_sent_has_dispatched_at")
        ),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name=op.f("ck_sys_events_status_valid")),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_sys_events_attempts_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_sys_events_event_id")),
    )
    op.create_index("ix_sys_events_code_occurred_at", "sys_events", ["event_code", "occurred_at"], unique=False)
    op.create_index("ix_sys_events_entity", "sys_events", ["entity_type", "entity_id"], unique=False)
    op.create_index("ix_sys_events_status_id", "sys_events", ["status", "id"], unique=False)
    op.create_table(
        "ops_commercial_invoices",
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=True),
        sa.Column("invoice_no", sa.String(length=100), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("seller_name", sa.String(length=300), nullable=False),
        sa.Column("buyer_name", sa.String(length=300), nullable=False),
        sa.Column("incoterm", sa.String(length=3), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("source_doc_job_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_ops_commercial_invoices_currency_iso")),
        sa.CheckConstraint(
            "incoterm IS NULL OR incoterm IN "
            "('EXW', 'FCA', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP', 'FAS', 'FOB', 'CFR', 'CIF')",
            name=op.f("ck_ops_commercial_invoices_incoterm_valid"),
        ),
        sa.CheckConstraint("total_amount >= 0", name=op.f("ck_ops_commercial_invoices_total_amount_non_negative")),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_commercial_invoices_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["ops_shipments.id"],
            name=op.f("fk_ops_commercial_invoices_shipment_id_ops_shipments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_doc_job_id"],
            ["sys_doc_jobs.id"],
            name=op.f("fk_ops_commercial_invoices_source_doc_job_id_sys_doc_jobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_commercial_invoices")),
        sa.UniqueConstraint(
            "contract_id", "seller_name", "invoice_no", name="uq_ops_commercial_invoices_contract_seller_no"
        ),
        sa.UniqueConstraint("source_doc_job_id", name=op.f("uq_ops_commercial_invoices_source_doc_job_id")),
    )
    op.create_index(
        "ix_ops_commercial_invoices_contract_date",
        "ops_commercial_invoices",
        ["contract_id", "invoice_date"],
        unique=False,
    )
    op.create_index("ix_ops_commercial_invoices_shipment", "ops_commercial_invoices", ["shipment_id"], unique=False)
    op.create_table(
        "ops_commercial_invoice_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("invoice_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("hs_code", sa.String(length=12), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.CheckConstraint(
            "amount IS NULL OR amount >= 0", name=op.f("ck_ops_commercial_invoice_items_amount_non_negative")
        ),
        sa.CheckConstraint("line_no >= 1", name=op.f("ck_ops_commercial_invoice_items_line_no_positive")),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity >= 0", name=op.f("ck_ops_commercial_invoice_items_quantity_non_negative")
        ),
        sa.CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0",
            name=op.f("ck_ops_commercial_invoice_items_unit_price_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["ops_commercial_invoices.id"],
            name=op.f("fk_ops_commercial_invoice_items_invoice_id_ops_commercial_invoices"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_commercial_invoice_items")),
        sa.UniqueConstraint("invoice_id", "line_no", name="uq_ops_commercial_invoice_items_line"),
    )
    op.add_column(
        "sys_doc_jobs",
        sa.Column("requires_dual_approval", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("sys_doc_jobs", sa.Column("first_approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sys_doc_jobs", sa.Column("first_approved_by", sa.BigInteger(), nullable=True))
    op.alter_column("sys_doc_jobs", "status", type_=sa.String(length=32), existing_type=sa.String(length=20))
    op.drop_constraint("ck_sys_doc_jobs_status_valid", "sys_doc_jobs", type_="check")
    op.create_check_constraint(
        op.f("ck_sys_doc_jobs_status_valid"), "sys_doc_jobs", f"status IN ({_statuses(NEW_STATUSES)})"
    )
    op.create_check_constraint(
        op.f("ck_sys_doc_jobs_pending_second_has_first_approval"),
        "sys_doc_jobs",
        "status <> 'pending_second_approval' OR (requires_dual_approval AND first_approved_by IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_sys_doc_jobs_dual_approval_distinct_people"),
        "sys_doc_jobs",
        "NOT (status = 'approved' AND requires_dual_approval) OR (first_approved_by IS NOT NULL "
        "AND approved_by <> first_approved_by AND approved_by IS DISTINCT FROM created_by)",
    )
    _triggers()
    _backfill_events()


OLD_STATUSES = ("queued", "processing", "ready_for_review", "needs_review", "approved", "rejected", "failed")
NEW_STATUSES = (
    "queued",
    "processing",
    "ready_for_review",
    "needs_review",
    "pending_second_approval",
    "approved",
    "rejected",
    "failed",
)


def _statuses(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION sys_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Không xóa sự kiện outbox %', OLD.event_id USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.event_id IS DISTINCT FROM NEW.event_id OR OLD.event_code IS DISTINCT FROM NEW.event_code
               OR OLD.payload IS DISTINCT FROM NEW.payload OR OLD.entity_type IS DISTINCT FROM NEW.entity_type
               OR OLD.entity_id IS DISTINCT FROM NEW.entity_id OR OLD.occurred_at IS DISTINCT FROM NEW.occurred_at
               OR OLD.consumers IS DISTINCT FROM NEW.consumers THEN
                RAISE EXCEPTION 'Nội dung sự kiện outbox % không sửa được (chỉ trạng thái gửi)', OLD.event_id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER sys_events_guard BEFORE UPDATE OR DELETE ON sys_events
            FOR EACH ROW EXECUTE FUNCTION sys_events_guard();

        CREATE FUNCTION ops_commercial_invoices_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Không xóa hóa đơn thương mại %', OLD.invoice_no USING ERRCODE = 'restrict_violation';
        END $$;
        CREATE TRIGGER ops_commercial_invoices_no_delete BEFORE DELETE ON ops_commercial_invoices
            FOR EACH ROW EXECUTE FUNCTION ops_commercial_invoices_no_delete();

        CREATE FUNCTION ops_commercial_invoice_items_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Bảng % chỉ ghi thêm; không sửa, không xóa', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END $$;
        CREATE TRIGGER ops_commercial_invoice_items_append_only BEFORE UPDATE OR DELETE ON ops_commercial_invoice_items
            FOR EACH ROW EXECUTE FUNCTION ops_commercial_invoice_items_append_only();
        """
    )


def _backfill_events() -> None:
    """Sự kiện đã phát trước outbox (dòng `event.<mã>` trong sys_audit_logs) → sys_events, chờ gửi (giữ event_id)."""
    op.execute(
        """
        INSERT INTO sys_events (event_id, event_code, event_name, schema_version, mode, entity_type, entity_id,
                                actor_id, occurred_at, payload, consumers, deliveries, status, attempts, created_at)
        SELECT (after->>'event_id')::uuid, after->>'event_code', after->>'event_name',
               (after->>'schema_version')::int, after->>'mode', entity_type, entity_id, actor_id,
               (after->>'occurred_at')::timestamptz, after, '["control", "assessment"]'::jsonb, '{}'::jsonb,
               'pending', 0, at
        FROM sys_audit_logs
        WHERE action LIKE 'event.%' AND after ? 'event_id' AND entity_id IS NOT NULL
        ORDER BY id
        ON CONFLICT (event_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER ops_commercial_invoice_items_append_only ON ops_commercial_invoice_items;
        DROP TRIGGER ops_commercial_invoices_no_delete ON ops_commercial_invoices;
        DROP TRIGGER sys_events_guard ON sys_events;
        DROP FUNCTION ops_commercial_invoice_items_append_only();
        DROP FUNCTION ops_commercial_invoices_no_delete();
        DROP FUNCTION sys_events_guard();
        """
    )
    # Job đang chờ người duyệt thứ 2 → về needs_review (0010 không có trạng thái này)
    op.execute("UPDATE sys_doc_jobs SET status = 'needs_review' WHERE status = 'pending_second_approval'")
    op.drop_constraint("ck_sys_doc_jobs_dual_approval_distinct_people", "sys_doc_jobs", type_="check")
    op.drop_constraint("ck_sys_doc_jobs_pending_second_has_first_approval", "sys_doc_jobs", type_="check")
    op.drop_constraint("ck_sys_doc_jobs_status_valid", "sys_doc_jobs", type_="check")
    op.create_check_constraint(
        op.f("ck_sys_doc_jobs_status_valid"), "sys_doc_jobs", f"status IN ({_statuses(OLD_STATUSES)})"
    )
    op.alter_column("sys_doc_jobs", "status", type_=sa.String(length=20), existing_type=sa.String(length=32))
    op.drop_column("sys_doc_jobs", "first_approved_by")
    op.drop_column("sys_doc_jobs", "first_approved_at")
    op.drop_column("sys_doc_jobs", "requires_dual_approval")
    op.drop_table("ops_commercial_invoice_items")
    op.drop_index("ix_ops_commercial_invoices_shipment", table_name="ops_commercial_invoices")
    op.drop_index("ix_ops_commercial_invoices_contract_date", table_name="ops_commercial_invoices")
    op.drop_table("ops_commercial_invoices")
    op.drop_index("ix_sys_events_status_id", table_name="sys_events")
    op.drop_index("ix_sys_events_entity", table_name="sys_events")
    op.drop_index("ix_sys_events_code_occurred_at", table_name="sys_events")
    op.drop_table("sys_events")
