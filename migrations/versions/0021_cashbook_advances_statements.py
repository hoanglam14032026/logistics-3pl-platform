"""Giai đoạn 20 – sổ thu – chi theo hợp đồng (02 §3.10), tạm ứng nhân viên, bảng kê đối soát định kỳ (SOA).

- md_cashbook_categories: khoản mục thu – chi; seed T01–T05, C01–C08 (T05 "Thu hoàn ứng của nhân viên" – thêm so với
  02 §3.10.1 để ghi phiếu thu tiền tạm ứng thừa nhân viên nộp lại).
- ops_staff_advances (02 §3.10.3), ops_cash_vouchers (02 §3.10.2). Không xóa (trigger); phiếu `recorded` chỉ được
  chuyển `void` (BR-112 – trigger).
- ops_contract_settlements mở rộng `settlement_type = periodic_statement` (theo khách hàng + tháng, `contract_id`
  NULL): số dư đầu kỳ, bản chụp dòng, phát hành lại (`previous_id`), phản hồi của khách hàng. Bản đã gửi không đổi
  số liệu; bản `disputed` / `closed` không đổi gì (trigger).
- Khóa hóa đơn thương mại: kỳ có bảng kê `confirmed` / `closed` của khách hàng → không thêm / sửa hóa đơn, dòng hàng
  có ngày hóa đơn trong kỳ (trigger).
- ctl_alerts: loại `staff_advance_overdue` (M-10, không gắn lô).

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALERT_TYPES = "'task_blocked', 'task_overdue', 'shipment_late_risk', 'task_sync_conflict', 'kpi_below_target'"

CATEGORIES = [
    ("T01", "Thu tiền dịch vụ", "receipt", "revenue"),
    ("T02", "Thu hoàn chi hộ", "receipt", "none"),
    ("T03", "Thu tạm ứng của khách hàng", "receipt", "none"),
    ("T04", "Thu khác (bồi thường từ bên thứ ba, thanh lý vật tư…)", "receipt", "other_income"),
    ("T05", "Thu hoàn ứng của nhân viên (tạm ứng thừa nộp lại)", "receipt", "none"),
    ("C01", "Chi hộ khách hàng (phí cảng, hãng tàu, thuế và lệ phí nộp hộ…)", "payment", "none"),
    ("C02", "Chi thuê vận tải ngoài", "payment", "direct_cost"),
    ("C03", "Chi nâng hạ, lưu kho bãi, lưu container", "payment", "direct_cost"),
    ("C04", "Chi nhân công trực tiếp, bồi dưỡng theo lô", "payment", "direct_cost"),
    ("C05", "Chi phí trực tiếp khác (chuyển phát, in ấn, phí ngân hàng…)", "payment", "direct_cost"),
    ("C06", "Chi bồi thường cho khách hàng", "payment", "direct_cost"),
    ("C07", "Chi hoàn trả khách hàng", "payment", "none"),
    ("C08", "Chi tạm ứng cho nhân viên", "payment", "none"),
]

_AUDIT_COLUMNS = (
    lambda: sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
    lambda: sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    lambda: sa.Column("created_by", sa.BigInteger(), nullable=True),
    lambda: sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    lambda: sa.Column("updated_by", sa.BigInteger(), nullable=True),
    lambda: sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
)


def _audit_columns() -> list[sa.Column]:
    return [make() for make in _AUDIT_COLUMNS]


def upgrade() -> None:
    categories = op.create_table(
        "md_cashbook_categories",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("result_class", sa.String(length=16), nullable=False),
        sa.Column("parent_code", sa.String(length=10), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "direction IN ('receipt', 'payment')", name=op.f("ck_md_cashbook_categories_direction_valid")
        ),
        sa.CheckConstraint(
            "result_class IN ('revenue', 'other_income', 'direct_cost', 'none')",
            name=op.f("ck_md_cashbook_categories_result_class_valid"),
        ),
        sa.CheckConstraint(
            "code ~ '^[TC][0-9]{2}(\\.[0-9]{1,2})?$'", name=op.f("ck_md_cashbook_categories_code_format")
        ),
        sa.CheckConstraint("(parent_code IS NULL) = is_system", name=op.f("ck_md_cashbook_categories_system_is_root")),
        sa.ForeignKeyConstraint(
            ["parent_code"],
            ["md_cashbook_categories.code"],
            name=op.f("fk_md_cashbook_categories_parent_code_md_cashbook_categories"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_md_cashbook_categories")),
        sa.UniqueConstraint("code", name=op.f("uq_md_cashbook_categories_code")),
    )
    op.bulk_insert(
        categories,
        [{"code": c, "name": n, "direction": d, "result_class": r, "is_system": True} for c, n, d, r in CATEGORIES],
    )

    op.create_table(
        "ops_staff_advances",
        sa.Column("advance_no", sa.String(length=30), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=True),
        sa.Column("purpose", sa.String(length=1000), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="VND", nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="requested", nullable=False),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("disbursed_by", sa.BigInteger(), nullable=True),
        sa.Column("disbursed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settlement_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("spent_total", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("settled_by", sa.BigInteger(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(
            "status IN ('requested', 'approved', 'disbursed', 'settling', 'settled', 'rejected')",
            name=op.f("ck_ops_staff_advances_status_valid"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_ops_staff_advances_amount_positive")),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_ops_staff_advances_currency_iso")),
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by <> staff_id", name=op.f("ck_ops_staff_advances_approver_not_staff")
        ),
        sa.CheckConstraint(
            "settled_by IS NULL OR settled_by <> staff_id", name=op.f("ck_ops_staff_advances_settler_not_staff")
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR decision_note IS NOT NULL", name=op.f("ck_ops_staff_advances_reject_has_note")
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_staff_advances_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["ops_shipments.id"],
            name=op.f("fk_ops_staff_advances_shipment_id_ops_shipments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"], ["sys_users.id"], name=op.f("fk_ops_staff_advances_staff_id_sys_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_staff_advances")),
        sa.UniqueConstraint("advance_no", name=op.f("uq_ops_staff_advances_advance_no")),
    )
    op.create_index("ix_ops_staff_advances_staff_status", "ops_staff_advances", ["staff_id", "status"], unique=False)
    op.create_index("ix_ops_staff_advances_status_due", "ops_staff_advances", ["status", "due_date"], unique=False)

    op.create_table(
        "ops_cash_vouchers",
        sa.Column("voucher_no", sa.String(length=30), nullable=False),
        sa.Column("voucher_type", sa.String(length=10), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=True),
        sa.Column("category_code", sa.String(length=10), nullable=False),
        sa.Column("counterparty_type", sa.String(length=10), nullable=False),
        sa.Column("counterparty_id", sa.BigInteger(), nullable=True),
        sa.Column("counterparty_name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("voucher_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(precision=18, scale=6), server_default="1", nullable=False),
        sa.Column("amount_base", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("payment_method", sa.String(length=16), nullable=False),
        sa.Column("supporting_file_ids", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("external_invoice_no", sa.String(length=100), nullable=True),
        sa.Column("advance_id", sa.BigInteger(), nullable=True),
        sa.Column("settlement_id", sa.BigInteger(), nullable=True),
        sa.Column("replaces_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("prepared_by", sa.BigInteger(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_by", sa.BigInteger(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("void_reason", sa.String(length=500), nullable=True),
        sa.Column("voided_by", sa.BigInteger(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint("voucher_type IN ('receipt', 'payment')", name=op.f("ck_ops_cash_vouchers_type_valid")),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'recorded', 'void')",
            name=op.f("ck_ops_cash_vouchers_status_valid"),
        ),
        sa.CheckConstraint(
            "payment_method IN ('cash', 'bank_transfer', 'offset')", name=op.f("ck_ops_cash_vouchers_method_valid")
        ),
        sa.CheckConstraint(
            "counterparty_type IN ('customer', 'supplier', 'staff')",
            name=op.f("ck_ops_cash_vouchers_counterparty_type_valid"),
        ),
        sa.CheckConstraint("amount > 0 AND exchange_rate > 0", name=op.f("ck_ops_cash_vouchers_amounts_positive")),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_ops_cash_vouchers_currency_iso")),
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by <> prepared_by", name=op.f("ck_ops_cash_vouchers_approver_not_preparer")
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'recorded') OR voucher_type = 'receipt' OR approved_by IS NOT NULL",
            name=op.f("ck_ops_cash_vouchers_payment_approved"),
        ),
        sa.CheckConstraint(
            "status <> 'recorded' OR (recorded_by IS NOT NULL AND recorded_at IS NOT NULL)",
            name=op.f("ck_ops_cash_vouchers_recorded_has_actor"),
        ),
        sa.CheckConstraint(
            "status <> 'void' OR void_reason IS NOT NULL", name=op.f("ck_ops_cash_vouchers_void_has_reason")
        ),
        sa.CheckConstraint(
            "payment_method <> 'offset' OR advance_id IS NOT NULL", name=op.f("ck_ops_cash_vouchers_offset_has_advance")
        ),
        sa.ForeignKeyConstraint(
            ["advance_id"],
            ["ops_staff_advances.id"],
            name=op.f("fk_ops_cash_vouchers_advance_id_ops_staff_advances"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["category_code"],
            ["md_cashbook_categories.code"],
            name=op.f("fk_ops_cash_vouchers_category_code_md_cashbook_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_cash_vouchers_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replaces_id"],
            ["ops_cash_vouchers.id"],
            name=op.f("fk_ops_cash_vouchers_replaces_id_ops_cash_vouchers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["ops_contract_settlements.id"],
            name=op.f("fk_ops_cash_vouchers_settlement_id_ops_contract_settlements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["ops_shipments.id"],
            name=op.f("fk_ops_cash_vouchers_shipment_id_ops_shipments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_cash_vouchers")),
        sa.UniqueConstraint("replaces_id", name=op.f("uq_ops_cash_vouchers_replaces_id")),
        sa.UniqueConstraint("voucher_no", name=op.f("uq_ops_cash_vouchers_voucher_no")),
    )
    op.create_index("ix_ops_cash_vouchers_advance", "ops_cash_vouchers", ["advance_id"], unique=False)
    op.create_index("ix_ops_cash_vouchers_contract_date", "ops_cash_vouchers", ["contract_id", "voucher_date"])
    op.create_index("ix_ops_cash_vouchers_status", "ops_cash_vouchers", ["status"], unique=False)

    # --- ops_contract_settlements: periodic_statement (SOA) ---
    t = "ops_contract_settlements"
    op.alter_column(t, "contract_id", existing_type=sa.BigInteger(), nullable=True)
    # "periodic_statement" dài 18 ký tự – cột Giai đoạn 19 là VARCHAR(16)
    op.alter_column(t, "settlement_type", existing_type=sa.String(length=16), type_=sa.String(length=20))
    op.add_column(t, sa.Column("customer_id", sa.BigInteger(), nullable=True))
    op.add_column(
        t, sa.Column("opening_balance", sa.Numeric(precision=18, scale=2), server_default="0", nullable=False)
    )
    op.add_column(t, sa.Column("statement_lines", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column(t, sa.Column("previous_id", sa.BigInteger(), nullable=True))
    op.add_column(t, sa.Column("sent_by", sa.BigInteger(), nullable=True))
    op.add_column(t, sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(t, sa.Column("customer_confirmed_by", sa.BigInteger(), nullable=True))
    op.add_column(t, sa.Column("customer_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(t, sa.Column("responded_by", sa.BigInteger(), nullable=True))
    op.add_column(t, sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(t, sa.Column("response_via", sa.String(length=16), nullable=True))
    op.add_column(t, sa.Column("response_note", sa.String(length=1000), nullable=True))
    op.add_column(t, sa.Column("dispute_reason", sa.String(length=1000), nullable=True))
    op.add_column(t, sa.Column("closed_by", sa.BigInteger(), nullable=True))
    op.add_column(t, sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_ops_contract_settlements_customer_id_md_parties"),
        t,
        "md_parties",
        ["customer_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_ops_contract_settlements_previous_id_ops_contract_settlements"),
        t,
        t,
        ["previous_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(op.f("uq_ops_contract_settlements_previous_id"), t, ["previous_id"])
    for name in ("status_valid", "amounts_valid", "balance_formula"):
        op.drop_constraint(f"ck_{t}_{name}", t, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{t}_status_valid"),
        t,
        "(settlement_type = 'final' AND status IN ('draft', 'pending_approval', 'approved', 'rejected')) OR "
        "(settlement_type = 'periodic_statement' AND status IN ('draft', 'sent', 'confirmed', 'disputed', 'closed'))",
    )
    op.create_check_constraint(op.f(f"ck_{t}_type_valid"), t, "settlement_type IN ('final', 'periodic_statement')")
    op.create_check_constraint(
        op.f(f"ck_{t}_type_shape"),
        t,
        "(settlement_type = 'final' AND contract_id IS NOT NULL AND customer_id IS NULL AND opening_balance = 0)"
        " OR (settlement_type = 'periodic_statement' AND contract_id IS NULL AND customer_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f(f"ck_{t}_amounts_valid"),
        t,
        "invoice_total >= 0 AND sla_deduction >= 0 AND compensation >= 0 "
        "AND (paid_amount >= 0 OR settlement_type = 'periodic_statement')",
    )
    op.create_check_constraint(
        op.f(f"ck_{t}_balance_formula"),
        t,
        "balance_due = opening_balance + invoice_total - sla_deduction - compensation - paid_amount",
    )
    op.create_check_constraint(
        op.f(f"ck_{t}_dispute_has_reason"),
        t,
        "status <> 'disputed' OR (dispute_reason IS NOT NULL AND responded_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f(f"ck_{t}_confirmed_has_response"),
        t,
        "status NOT IN ('confirmed', 'closed') OR settlement_type = 'final' OR responded_at IS NOT NULL",
    )
    op.create_check_constraint(
        op.f(f"ck_{t}_response_via_valid"), t, "response_via IS NULL OR response_via IN ('customer', 'staff')"
    )
    op.create_index(
        "uq_ops_contract_settlements_statement_period",
        t,
        ["customer_id", "period_from"],
        unique=True,
        postgresql_where=sa.text("settlement_type = 'periodic_statement' AND status <> 'disputed'"),
    )

    # --- ctl_alerts: M-10 ---
    op.drop_constraint("ck_ctl_alerts_alert_type_valid", "ctl_alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_ctl_alerts_alert_type_valid"), "ctl_alerts", f"alert_type IN ({_ALERT_TYPES}, 'staff_advance_overdue')"
    )
    op.drop_constraint("ck_ctl_alerts_shipment_required", "ctl_alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_ctl_alerts_shipment_required"),
        "ctl_alerts",
        "shipment_id IS NOT NULL OR alert_type IN ('kpi_below_target', 'staff_advance_overdue')",
    )

    op.execute(
        """
        CREATE FUNCTION g20_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% không được xóa', TG_TABLE_NAME;
        END $$;
        CREATE TRIGGER ops_cash_vouchers_no_delete BEFORE DELETE ON ops_cash_vouchers
            FOR EACH ROW EXECUTE FUNCTION g20_no_delete();
        CREATE TRIGGER ops_staff_advances_no_delete BEFORE DELETE ON ops_staff_advances
            FOR EACH ROW EXECUTE FUNCTION g20_no_delete();

        -- BR-112: phiếu đã ghi sổ không sửa; chỉ được chuyển void (kèm lý do). Phiếu void không đổi nữa.
        CREATE FUNCTION ops_cash_voucher_recorded_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status = 'void' THEN
                RAISE EXCEPTION 'Phiếu % đã hủy (void) – không sửa được', OLD.voucher_no;
            END IF;
            IF OLD.status = 'recorded' AND (
                NEW.status <> 'void'
                OR (NEW.voucher_no, NEW.voucher_type, NEW.contract_id, NEW.shipment_id, NEW.category_code,
                    NEW.counterparty_type, NEW.counterparty_id, NEW.counterparty_name, NEW.description,
                    NEW.voucher_date, NEW.amount, NEW.currency, NEW.exchange_rate, NEW.amount_base,
                    NEW.payment_method, NEW.supporting_file_ids, NEW.advance_id, NEW.settlement_id,
                    NEW.prepared_by, NEW.approved_by, NEW.recorded_by, NEW.recorded_at)
                   IS DISTINCT FROM
                   (OLD.voucher_no, OLD.voucher_type, OLD.contract_id, OLD.shipment_id, OLD.category_code,
                    OLD.counterparty_type, OLD.counterparty_id, OLD.counterparty_name, OLD.description,
                    OLD.voucher_date, OLD.amount, OLD.currency, OLD.exchange_rate, OLD.amount_base,
                    OLD.payment_method, OLD.supporting_file_ids, OLD.advance_id, OLD.settlement_id,
                    OLD.prepared_by, OLD.approved_by, OLD.recorded_by, OLD.recorded_at)
            ) THEN
                RAISE EXCEPTION 'BR-112: phiếu % đã ghi sổ – không sửa, chỉ hủy (void) kèm lý do', OLD.voucher_no;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER ops_cash_vouchers_recorded_guard BEFORE UPDATE ON ops_cash_vouchers
            FOR EACH ROW EXECUTE FUNCTION ops_cash_voucher_recorded_guard();

        -- Bảng kê đối soát: đã gửi → không đổi số liệu; disputed / closed → không đổi gì (phát hành lại = bản mới)
        CREATE FUNCTION ops_statement_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.settlement_type <> 'periodic_statement' THEN
                RETURN NEW;
            END IF;
            IF OLD.status IN ('disputed', 'closed') THEN
                RAISE EXCEPTION 'Bảng kê % đang % – không sửa được', OLD.settlement_no, OLD.status;
            END IF;
            IF OLD.status <> 'draft' AND (
                (NEW.customer_id, NEW.period_from, NEW.period_to, NEW.currency, NEW.opening_balance,
                 NEW.invoice_count, NEW.invoice_total, NEW.paid_amount, NEW.balance_due, NEW.other_currency_totals,
                 NEW.statement_lines, NEW.previous_id)
                IS DISTINCT FROM
                (OLD.customer_id, OLD.period_from, OLD.period_to, OLD.currency, OLD.opening_balance,
                 OLD.invoice_count, OLD.invoice_total, OLD.paid_amount, OLD.balance_due, OLD.other_currency_totals,
                 OLD.statement_lines, OLD.previous_id)
            ) THEN
                RAISE EXCEPTION 'Bảng kê % đã gửi – không đổi số liệu', OLD.settlement_no;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER ops_contract_settlements_statement_guard BEFORE UPDATE ON ops_contract_settlements
            FOR EACH ROW EXECUTE FUNCTION ops_statement_guard();

        -- Khóa hóa đơn thương mại thuộc kỳ đã có bảng kê confirmed / closed của khách hàng (disputed: không khóa)
        CREATE FUNCTION ops_invoice_period_locked(p_contract_id BIGINT, p_date DATE) RETURNS TEXT
            LANGUAGE sql STABLE AS $$
            SELECT s.settlement_no
            FROM ops_contract_settlements s
            JOIN ops_service_contracts c ON c.customer_id = s.customer_id
            WHERE c.id = p_contract_id AND c.mode = 'production'
              AND s.settlement_type = 'periodic_statement' AND s.status IN ('confirmed', 'closed')
              AND p_date BETWEEN s.period_from AND s.period_to
            LIMIT 1
        $$;
        CREATE FUNCTION ops_invoice_lock_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            locked TEXT;
        BEGIN
            locked := ops_invoice_period_locked(NEW.contract_id, NEW.invoice_date);
            IF locked IS NULL AND TG_OP = 'UPDATE' THEN
                locked := ops_invoice_period_locked(OLD.contract_id, OLD.invoice_date);
            END IF;
            IF locked IS NOT NULL THEN
                RAISE EXCEPTION 'Kỳ hóa đơn đã khóa bởi bảng kê đối soát % (confirmed)', locked;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER ops_commercial_invoices_lock_guard BEFORE INSERT OR UPDATE ON ops_commercial_invoices
            FOR EACH ROW EXECUTE FUNCTION ops_invoice_lock_guard();
        CREATE FUNCTION ops_invoice_item_lock_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            locked TEXT;
        BEGIN
            SELECT ops_invoice_period_locked(i.contract_id, i.invoice_date) INTO locked
            FROM ops_commercial_invoices i WHERE i.id = NEW.invoice_id;
            IF locked IS NOT NULL THEN
                RAISE EXCEPTION 'Kỳ hóa đơn đã khóa bởi bảng kê đối soát % (confirmed)', locked;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER ops_commercial_invoice_items_lock_guard BEFORE INSERT ON ops_commercial_invoice_items
            FOR EACH ROW EXECUTE FUNCTION ops_invoice_item_lock_guard();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER ops_commercial_invoice_items_lock_guard ON ops_commercial_invoice_items;
        DROP FUNCTION ops_invoice_item_lock_guard();
        DROP TRIGGER ops_commercial_invoices_lock_guard ON ops_commercial_invoices;
        DROP FUNCTION ops_invoice_lock_guard();
        DROP FUNCTION ops_invoice_period_locked(BIGINT, DATE);
        DROP TRIGGER ops_contract_settlements_statement_guard ON ops_contract_settlements;
        DROP FUNCTION ops_statement_guard();
        DROP TRIGGER ops_cash_vouchers_recorded_guard ON ops_cash_vouchers;
        DROP FUNCTION ops_cash_voucher_recorded_guard();
        DROP TRIGGER ops_staff_advances_no_delete ON ops_staff_advances;
        DROP TRIGGER ops_cash_vouchers_no_delete ON ops_cash_vouchers;
        DROP FUNCTION g20_no_delete();
        """
    )
    op.execute("DELETE FROM ctl_alerts WHERE alert_type = 'staff_advance_overdue'")
    op.drop_constraint("ck_ctl_alerts_shipment_required", "ctl_alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_ctl_alerts_shipment_required"),
        "ctl_alerts",
        "shipment_id IS NOT NULL OR alert_type = 'kpi_below_target'",
    )
    op.drop_constraint("ck_ctl_alerts_alert_type_valid", "ctl_alerts", type_="check")
    op.create_check_constraint(op.f("ck_ctl_alerts_alert_type_valid"), "ctl_alerts", f"alert_type IN ({_ALERT_TYPES})")

    t = "ops_contract_settlements"
    op.drop_table("ops_cash_vouchers")  # tham chiếu settlement → bỏ trước khi xóa bảng kê
    op.execute(f"DELETE FROM {t} WHERE settlement_type = 'periodic_statement'")
    op.drop_index(
        "uq_ops_contract_settlements_statement_period",
        table_name=t,
        postgresql_where=sa.text("settlement_type = 'periodic_statement' AND status <> 'disputed'"),
    )
    for name in (
        "response_via_valid",
        "confirmed_has_response",
        "dispute_has_reason",
        "balance_formula",
        "amounts_valid",
        "type_shape",
        "type_valid",
        "status_valid",
    ):
        op.drop_constraint(f"ck_{t}_{name}", t, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{t}_status_valid"), t, "status IN ('draft', 'pending_approval', 'approved', 'rejected')"
    )
    op.create_check_constraint(
        op.f(f"ck_{t}_amounts_valid"),
        t,
        "invoice_total >= 0 AND sla_deduction >= 0 AND compensation >= 0 AND paid_amount >= 0",
    )
    op.create_check_constraint(
        op.f(f"ck_{t}_balance_formula"), t, "balance_due = invoice_total - sla_deduction - compensation - paid_amount"
    )
    op.drop_constraint(op.f("uq_ops_contract_settlements_previous_id"), t, type_="unique")
    op.drop_constraint(op.f("fk_ops_contract_settlements_previous_id_ops_contract_settlements"), t, type_="foreignkey")
    op.drop_constraint(op.f("fk_ops_contract_settlements_customer_id_md_parties"), t, type_="foreignkey")
    for col in (
        "closed_at",
        "closed_by",
        "dispute_reason",
        "response_note",
        "response_via",
        "responded_at",
        "responded_by",
        "customer_confirmed_at",
        "customer_confirmed_by",
        "sent_at",
        "sent_by",
        "previous_id",
        "statement_lines",
        "opening_balance",
        "customer_id",
    ):
        op.drop_column(t, col)
    op.alter_column(t, "contract_id", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column(t, "settlement_type", existing_type=sa.String(length=20), type_=sa.String(length=16))

    op.drop_index("ix_ops_staff_advances_status_due", table_name="ops_staff_advances")
    op.drop_index("ix_ops_staff_advances_staff_status", table_name="ops_staff_advances")
    op.drop_table("ops_staff_advances")
    op.drop_table("md_cashbook_categories")
