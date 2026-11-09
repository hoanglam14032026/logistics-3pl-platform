"""Giai đoạn 15 – Luồng 4: danh mục KPI, mục tiêu từ EV-201, dữ kiện cho KPI-01…14, tính định kỳ, cảnh báo EV-403.

- asm_kpi_definitions (seed KPI-01…14 theo 05 §3 – không có mục tiêu: mục tiêu đến từ kế hoạch duyệt), asm_kpi_targets.
- asm_fact_document_checks, asm_fact_checkpoints, asm_fact_customs, asm_fact_incidents, asm_fact_service_requests;
  asm_fact_shipments thêm first_doc_received_at, docs_verified_at, docs_verified_by (KPI-02).
- asm_calc_runs, asm_fact_kpi_measurements (chỉ thêm – trigger chặn sửa / xóa), asm_kpi_alerts (chặn xóa; chỉ được
  ghi event_id một lần).
- ctl_alerts: loại kpi_below_target, shipment_id cho phép NULL (cảnh báo KPI), contract_id.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_TYPES = "'task_blocked', 'task_overdue', 'shipment_late_risk', 'task_sync_conflict'"

_KPIS = [
    # (mã, tên, mô tả công thức, formula_key, đơn vị, chiều, cấp đo, chế độ, sự kiện nguồn)
    (
        "KPI-01",
        "Tỷ lệ bộ chứng từ đạt ngay lần kiểm tra đầu",
        "Số lô có lần kiểm tra run_no = 1 với blocking_count = 0 ÷ số lô đã kiểm tra",
        "doc_first_time_right",
        "%",
        "higher_better",
        ["staff", "contract"],
        ["production", "training"],
        ["EV-122"],
    ),
    (
        "KPI-02",
        "Thời gian xử lý bộ chứng từ",
        "Thời điểm lô chuyển docs_verified − thời điểm nhận chứng từ đầu tiên (trung vị)",
        "doc_processing_hours",
        "hour",
        "lower_better",
        ["staff", "contract", "learner"],
        ["production", "training"],
        ["EV-121", "EV-112"],
    ),
    (
        "KPI-03",
        "Tỷ lệ bước hoàn thành đúng hạn",
        "Số bước actual_end ≤ planned_end ÷ số bước hoàn thành",
        "task_on_time",
        "%",
        "higher_better",
        ["staff", "learner"],
        ["production", "training"],
        ["EV-132"],
    ),
    (
        "KPI-04",
        "Tỷ lệ tuân thủ quy trình",
        "Số bước có sequence_ok ∧ evidence_ok ÷ số bước hoàn thành",
        "process_compliance",
        "%",
        "higher_better",
        ["staff", "learner"],
        ["production", "training"],
        ["EV-132"],
    ),
    (
        "KPI-05",
        "Tỷ lệ đạt điểm kiểm soát lần đầu",
        "Số điểm kiểm soát passed ở attempt_no = 1 ÷ số điểm kiểm soát đã đánh giá (lần đầu)",
        "checkpoint_first_pass",
        "%",
        "higher_better",
        ["staff", "learner"],
        ["production", "training"],
        ["EV-204"],
    ),
    (
        "KPI-06",
        "Thời gian thông quan",
        "cleared_at − submitted_at (trung vị; tách theo luồng xanh/vàng/đỏ)",
        "customs_clearance_hours",
        "hour",
        "lower_better",
        ["contract"],
        ["production", "training"],
        ["EV-141"],
    ),
    (
        "KPI-07",
        "Tỷ lệ giao hàng đúng hạn (On-Time Delivery)",
        "Số lô delivered_at ≤ sla_due_at ÷ số lô đã giao",
        "on_time_delivery",
        "%",
        "higher_better",
        ["contract", "staff"],
        ["production", "training"],
        ["EV-142"],
    ),
    (
        "KPI-08",
        "Tỷ lệ giao đủ, nguyên vẹn",
        "Số lô in_full ∧ damage_free ÷ số lô đã giao",
        "in_full_damage_free",
        "%",
        "higher_better",
        ["contract"],
        ["production", "training"],
        ["EV-142"],
    ),
    (
        "KPI-09",
        "Tỷ lệ đơn hàng hoàn hảo (Perfect Order)",
        "Số lô đúng hạn ∧ đủ ∧ nguyên vẹn ∧ không có sự cố DOCUMENT_ERROR ÷ số lô đã giao",
        "perfect_order",
        "%",
        "higher_better",
        ["contract"],
        ["production", "training"],
        ["EV-142", "EV-205"],
    ),
    (
        "KPI-10",
        "Tỷ lệ lô có sự cố",
        "Số lô có ≥ 1 sự cố ÷ số lô",
        "incident_rate",
        "%",
        "lower_better",
        ["contract", "staff"],
        ["production", "training"],
        ["EV-205"],
    ),
    (
        "KPI-11",
        "Tỷ lệ phát hiện sai lệch cài sẵn",
        "Số sai lệch trong đáp án được người học ghi nhận đúng ÷ số sai lệch trong đáp án",
        "discrepancy_detection",
        "%",
        "higher_better",
        ["learner"],
        ["training"],
        ["EV-122"],
    ),
    (
        "KPI-12",
        "Tỷ lệ báo sai",
        "Số sai lệch người học ghi nhận không có trong đáp án ÷ số sai lệch người học ghi nhận",
        "false_report",
        "%",
        "lower_better",
        ["learner"],
        ["training"],
        ["EV-122"],
    ),
    (
        "KPI-13",
        "Mức hoàn thành học liệu bắt buộc",
        "Số học liệu bắt buộc đã hoàn thành ÷ số học liệu bắt buộc của khóa",
        "learning_completion",
        "%",
        "higher_better",
        ["learner"],
        ["training"],
        ["EV-302", "EV-303"],
    ),
    (
        "KPI-14",
        "Tỷ lệ phản hồi yêu cầu dịch vụ đúng hạn",
        "Số yêu cầu có decided_at ≤ response_due_at ÷ số yêu cầu đã quyết định (kèm trung vị thời gian phản hồi)",
        "service_request_response",
        "%",
        "higher_better",
        ["contract", "staff", "learner"],
        ["production", "training"],
        ["EV-113", "EV-114"],
    ),
]


def upgrade() -> None:
    op.create_table(
        "asm_calc_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name=op.f("ck_asm_calc_runs_status_valid")),
        sa.CheckConstraint("trigger IN ('schedule', 'manual')", name=op.f("ck_asm_calc_runs_trigger_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asm_calc_runs")),
    )
    op.create_table(
        "asm_fact_checkpoints",
        sa.Column("checkpoint_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("attempt_no", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("step_code", sa.String(length=20), nullable=False),
        sa.Column("result", sa.String(length=8), nullable=False),
        sa.Column("performer_id", sa.BigInteger(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_fact_checkpoints_mode_valid")),
        sa.CheckConstraint("result IN ('passed', 'failed')", name=op.f("ck_asm_fact_checkpoints_result_valid")),
        sa.PrimaryKeyConstraint("checkpoint_id", "attempt_no", name=op.f("pk_asm_fact_checkpoints")),
    )
    op.create_index("ix_asm_fact_checkpoints_at", "asm_fact_checkpoints", ["evaluated_at"], unique=False)
    op.create_table(
        "asm_fact_customs",
        sa.Column("shipment_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("declaration_no", sa.String(length=40), nullable=False),
        sa.Column("channel", sa.String(length=8), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("channel IN ('green', 'yellow', 'red')", name=op.f("ck_asm_fact_customs_channel_valid")),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_fact_customs_mode_valid")),
        sa.PrimaryKeyConstraint("shipment_id", "declaration_no", name=op.f("pk_asm_fact_customs")),
    )
    op.create_index("ix_asm_fact_customs_cleared_at", "asm_fact_customs", ["cleared_at"], unique=False)
    op.create_table(
        "asm_fact_document_checks",
        sa.Column("check_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("run_no", sa.Integer(), nullable=False),
        sa.Column("blocking_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("checked_by", sa.BigInteger(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_fact_document_checks_mode_valid")),
        sa.PrimaryKeyConstraint("check_id", name=op.f("pk_asm_fact_document_checks")),
    )
    op.create_index("ix_asm_fact_document_checks_at", "asm_fact_document_checks", ["checked_at"], unique=False)
    op.create_table(
        "asm_fact_incidents",
        sa.Column("incident_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=True),
        sa.Column("shipment_id", sa.BigInteger(), nullable=True),
        sa.Column("incident_type", sa.String(length=40), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=True),
        sa.Column("attributed_to", sa.BigInteger(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("root_cause_code", sa.String(length=40), nullable=True),
        sa.Column("liability", sa.String(length=20), nullable=True),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_fact_incidents_mode_valid")),
        sa.PrimaryKeyConstraint("incident_id", name=op.f("pk_asm_fact_incidents")),
    )
    op.create_index("ix_asm_fact_incidents_shipment", "asm_fact_incidents", ["shipment_id"], unique=False)
    op.create_table(
        "asm_fact_service_requests",
        sa.Column("request_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("shipment_id", sa.BigInteger(), nullable=True),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_fact_service_requests_mode_valid")),
        sa.PrimaryKeyConstraint("request_id", name=op.f("pk_asm_fact_service_requests")),
    )
    op.create_index(
        "ix_asm_fact_service_requests_decided_at", "asm_fact_service_requests", ["decided_at"], unique=False
    )
    op.create_table(
        "asm_kpi_definitions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kpi_code", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("formula_key", sa.String(length=40), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("subject_levels", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("modes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_events", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
        sa.CheckConstraint(
            "direction IN ('higher_better', 'lower_better')", name=op.f("ck_asm_kpi_definitions_direction_valid")
        ),
        sa.CheckConstraint("status IN ('active', 'retired')", name=op.f("ck_asm_kpi_definitions_status_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asm_kpi_definitions")),
        sa.UniqueConstraint("kpi_code", "version", name="uq_asm_kpi_definitions_code_version"),
    )
    op.create_table(
        "asm_kpi_targets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kpi_code", sa.String(length=40), nullable=False),
        sa.Column("subject_type", sa.String(length=16), server_default=sa.text("'contract'"), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("target_value", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("warning_threshold", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("source_plan_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_kpi_targets_mode_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asm_kpi_targets")),
        sa.UniqueConstraint("source_event_id", "kpi_code", name="uq_asm_kpi_targets_event_kpi"),
    )
    op.create_index(
        "ix_asm_kpi_targets_subject",
        "asm_kpi_targets",
        ["subject_type", "subject_id", "kpi_code", "valid_from"],
        unique=False,
    )
    op.create_table(
        "asm_fact_kpi_measurements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("calc_run_id", sa.BigInteger(), nullable=False),
        sa.Column("kpi_code", sa.String(length=16), nullable=False),
        sa.Column("kpi_version", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.String(length=16), server_default=sa.text("'production'"), nullable=False),
        sa.Column("period_type", sa.String(length=8), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("numerator", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("denominator", sa.Integer(), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('production', 'training')", name=op.f("ck_asm_fact_kpi_measurements_mode_valid")),
        sa.CheckConstraint(
            "period_type IN ('week', 'month', 'quarter')", name=op.f("ck_asm_fact_kpi_measurements_period_type_valid")
        ),
        sa.CheckConstraint(
            "subject_type IN ('all', 'contract', 'staff')", name=op.f("ck_asm_fact_kpi_measurements_subject_type_valid")
        ),
        sa.CheckConstraint(
            "denominator >= 0 AND numerator >= 0", name=op.f("ck_asm_fact_kpi_measurements_counts_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["calc_run_id"],
            ["asm_calc_runs.id"],
            name=op.f("fk_asm_fact_kpi_measurements_calc_run_id_asm_calc_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asm_fact_kpi_measurements")),
    )
    op.create_index(
        op.f("ix_asm_fact_kpi_measurements_calc_run_id"), "asm_fact_kpi_measurements", ["calc_run_id"], unique=False
    )
    op.create_index(
        "ix_asm_fact_kpi_measurements_lookup",
        "asm_fact_kpi_measurements",
        ["kpi_code", "subject_type", "subject_id", "period_type", "period_start"],
        unique=False,
    )
    op.create_table(
        "asm_kpi_alerts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kpi_code", sa.String(length=16), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("period_type", sa.String(length=8), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("target", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("warning_threshold", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("calc_run_id", sa.BigInteger(), nullable=False),
        sa.Column("measurement_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("severity IN ('warning', 'critical')", name=op.f("ck_asm_kpi_alerts_severity_valid")),
        sa.ForeignKeyConstraint(
            ["calc_run_id"],
            ["asm_calc_runs.id"],
            name=op.f("fk_asm_kpi_alerts_calc_run_id_asm_calc_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["measurement_id"],
            ["asm_fact_kpi_measurements.id"],
            name=op.f("fk_asm_kpi_alerts_measurement_id_asm_fact_kpi_measurements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["asm_kpi_targets.id"],
            name=op.f("fk_asm_kpi_alerts_target_id_asm_kpi_targets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asm_kpi_alerts")),
        sa.UniqueConstraint(
            "kpi_code", "subject_type", "subject_id", "period_type", "period_start", name="uq_asm_kpi_alerts_period"
        ),
    )
    op.add_column("asm_fact_shipments", sa.Column("first_doc_received_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("asm_fact_shipments", sa.Column("docs_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("asm_fact_shipments", sa.Column("docs_verified_by", sa.BigInteger(), nullable=True))
    op.add_column("ctl_alerts", sa.Column("contract_id", sa.BigInteger(), nullable=True))
    op.alter_column("ctl_alerts", "shipment_id", existing_type=sa.BIGINT(), nullable=True)
    op.drop_constraint("ck_ctl_alerts_alert_type_valid", "ctl_alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_ctl_alerts_alert_type_valid"), "ctl_alerts", f"alert_type IN ({_OLD_TYPES}, 'kpi_below_target')"
    )
    op.drop_constraint("ck_ctl_alert_notifications_purpose_valid", "ctl_alert_notifications", type_="check")
    op.create_check_constraint(
        op.f("ck_ctl_alert_notifications_purpose_valid"),
        "ctl_alert_notifications",
        "purpose IN ('raised', 'escalated', 'kpi')",
    )
    op.create_check_constraint(
        op.f("ck_ctl_alerts_shipment_required"),
        "ctl_alerts",
        "shipment_id IS NOT NULL OR alert_type = 'kpi_below_target'",
    )
    definitions = sa.table(
        "asm_kpi_definitions",
        sa.column("kpi_code", sa.String),
        sa.column("version", sa.Integer),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("formula_key", sa.String),
        sa.column("unit", sa.String),
        sa.column("direction", sa.String),
        sa.column("subject_levels", postgresql.JSONB),
        sa.column("modes", postgresql.JSONB),
        sa.column("source_events", postgresql.JSONB),
        sa.column("status", sa.String),
    )
    op.bulk_insert(
        definitions,
        [
            {
                "kpi_code": code,
                "version": 1,
                "name": name,
                "description": desc,
                "formula_key": key,
                "unit": unit,
                "direction": direction,
                "subject_levels": levels,
                "modes": modes,
                "source_events": events,
                "status": "active",
            }
            for code, name, desc, key, unit, direction, levels, modes, events in _KPIS
        ],
    )
    op.execute(
        """
        CREATE FUNCTION asm_fact_kpi_measurements_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'asm_fact_kpi_measurements chỉ được thêm (không sửa / xóa) – tính lại tạo dòng mới';
        END $$;
        CREATE TRIGGER asm_fact_kpi_measurements_append_only BEFORE UPDATE OR DELETE ON asm_fact_kpi_measurements
            FOR EACH ROW EXECUTE FUNCTION asm_fact_kpi_measurements_append_only();
        CREATE FUNCTION asm_kpi_alerts_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'asm_kpi_alerts không được xóa';
            END IF;
            IF OLD.event_id <> '00000000-0000-0000-0000-000000000000'::uuid
               OR (to_jsonb(NEW) - 'event_id') IS DISTINCT FROM (to_jsonb(OLD) - 'event_id') THEN
                RAISE EXCEPTION 'asm_kpi_alerts chỉ được ghi event_id một lần';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER asm_kpi_alerts_guard BEFORE UPDATE OR DELETE ON asm_kpi_alerts
            FOR EACH ROW EXECUTE FUNCTION asm_kpi_alerts_guard();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER asm_kpi_alerts_guard ON asm_kpi_alerts;
        DROP FUNCTION asm_kpi_alerts_guard();
        DROP TRIGGER asm_fact_kpi_measurements_append_only ON asm_fact_kpi_measurements;
        DROP FUNCTION asm_fact_kpi_measurements_append_only();
        """
    )
    # Cảnh báo KPI (và thông báo của nó) không có ở 0015: xóa (tạm tắt trigger chặn xóa) rồi đặt lại ràng buộc cũ
    op.execute(
        """
        ALTER TABLE ctl_alert_notifications DISABLE TRIGGER ctl_alert_notifications_no_delete;
        ALTER TABLE ctl_alerts DISABLE TRIGGER ctl_alerts_no_delete;
        DELETE FROM ctl_alert_notifications
            WHERE alert_id IN (SELECT id FROM ctl_alerts WHERE alert_type = 'kpi_below_target');
        DELETE FROM ctl_alerts WHERE alert_type = 'kpi_below_target';
        ALTER TABLE ctl_alerts ENABLE TRIGGER ctl_alerts_no_delete;
        ALTER TABLE ctl_alert_notifications ENABLE TRIGGER ctl_alert_notifications_no_delete;
        """
    )
    op.drop_constraint("ck_ctl_alert_notifications_purpose_valid", "ctl_alert_notifications", type_="check")
    op.create_check_constraint(
        op.f("ck_ctl_alert_notifications_purpose_valid"),
        "ctl_alert_notifications",
        "purpose IN ('raised', 'escalated')",
    )
    op.drop_constraint("ck_ctl_alerts_shipment_required", "ctl_alerts", type_="check")
    op.drop_constraint("ck_ctl_alerts_alert_type_valid", "ctl_alerts", type_="check")
    op.create_check_constraint(op.f("ck_ctl_alerts_alert_type_valid"), "ctl_alerts", f"alert_type IN ({_OLD_TYPES})")
    op.alter_column("ctl_alerts", "shipment_id", existing_type=sa.BIGINT(), nullable=False)
    op.drop_column("ctl_alerts", "contract_id")
    op.drop_column("asm_fact_shipments", "docs_verified_by")
    op.drop_column("asm_fact_shipments", "docs_verified_at")
    op.drop_column("asm_fact_shipments", "first_doc_received_at")
    op.drop_table("asm_kpi_alerts")
    op.drop_index("ix_asm_fact_kpi_measurements_lookup", table_name="asm_fact_kpi_measurements")
    op.drop_index(op.f("ix_asm_fact_kpi_measurements_calc_run_id"), table_name="asm_fact_kpi_measurements")
    op.drop_table("asm_fact_kpi_measurements")
    op.drop_index("ix_asm_kpi_targets_subject", table_name="asm_kpi_targets")
    op.drop_table("asm_kpi_targets")
    op.drop_table("asm_kpi_definitions")
    op.drop_index("ix_asm_fact_service_requests_decided_at", table_name="asm_fact_service_requests")
    op.drop_table("asm_fact_service_requests")
    op.drop_index("ix_asm_fact_incidents_shipment", table_name="asm_fact_incidents")
    op.drop_table("asm_fact_incidents")
    op.drop_index("ix_asm_fact_document_checks_at", table_name="asm_fact_document_checks")
    op.drop_table("asm_fact_document_checks")
    op.drop_index("ix_asm_fact_customs_cleared_at", table_name="asm_fact_customs")
    op.drop_table("asm_fact_customs")
    op.drop_index("ix_asm_fact_checkpoints_at", table_name="asm_fact_checkpoints")
    op.drop_table("asm_fact_checkpoints")
    op.drop_table("asm_calc_runs")
