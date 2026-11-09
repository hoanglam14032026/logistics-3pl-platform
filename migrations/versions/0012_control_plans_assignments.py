"""Giai đoạn 11 – Luồng 2: lập kế hoạch, phân công tự động; bộ nhận sự kiện sys_event_consumptions; quy trình mẫu.

- sys_event_consumptions: vết xử lý sự kiện của từng luồng nhận (UNIQUE consumer + event_id) – không xử lý trùng.
- md_process_templates, md_process_steps (02 §4, thêm required_skill): seed QT-NK-SEA-FCL v1 (10 bước NK-01…NK-10,
  điểm kiểm soát NK-02/03/05/09) và QT-VC-NOIDIA v1 (vận chuyển nội địa đường bộ). Thời gian chuẩn: giá trị minh họa.
- ctl_contract_plans (+ ctl_plan_members, ctl_plan_kpi_targets): kế hoạch triển khai hợp đồng (từ EV-101).
- ctl_shipment_plans, ctl_shipment_tasks: kế hoạch lô + bước (từ EV-111); ctl_assignments: lịch sử phân công (chỉ ghi
  thêm – chỉ được bỏ cờ is_current); ctl_staff_profiles: năng lực, sức chứa.
- Trigger: không xóa sys_event_consumptions, ctl_assignments; ctl_assignments chỉ đổi is_current (true → false).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "md_process_templates",
        sa.Column("process_code", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("service_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("transport_modes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "direction IN ('import', 'export', 'domestic')", name=op.f("ck_md_process_templates_direction_valid")
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'retired')", name=op.f("ck_md_process_templates_status_valid")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_md_process_templates_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_md_process_templates")),
        sa.UniqueConstraint("process_code", "version", name="uq_md_process_templates_code_version"),
    )
    op.create_index(
        "uq_md_process_templates_one_approved",
        "md_process_templates",
        ["process_code"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )
    op.create_table(
        "sys_event_consumptions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("consumer", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_code", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('processed', 'skipped', 'failed', 'dead')", name=op.f("ck_sys_event_consumptions_status_valid")
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_sys_event_consumptions_attempts_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_event_consumptions")),
        sa.UniqueConstraint("consumer", "event_id", name="uq_sys_event_consumptions_consumer_event"),
    )
    op.create_index("ix_sys_event_consumptions_status", "sys_event_consumptions", ["consumer", "status"], unique=False)
    op.create_table(
        "md_process_steps",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("template_id", sa.BigInteger(), nullable=False),
        sa.Column("step_code", sa.String(length=20), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("responsible_role", sa.String(length=32), nullable=False),
        sa.Column("required_skill", sa.String(length=40), nullable=True),
        sa.Column("standard_duration_hours", sa.Numeric(precision=7, scale=2), nullable=False),
        sa.Column("depends_on", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("is_checkpoint", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("checkpoint_type", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "is_checkpoint = (checkpoint_type IS NOT NULL)", name=op.f("ck_md_process_steps_checkpoint_type_matches")
        ),
        sa.CheckConstraint("standard_duration_hours >= 0", name=op.f("ck_md_process_steps_duration_non_negative")),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["md_process_templates.id"],
            name=op.f("fk_md_process_steps_template_id_md_process_templates"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_md_process_steps")),
        sa.UniqueConstraint("template_id", "seq", name="uq_md_process_steps_template_seq"),
        sa.UniqueConstraint("template_id", "step_code", name="uq_md_process_steps_template_step"),
    )
    op.create_table(
        "ctl_contract_plans",
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_no", sa.String(length=30), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("service_scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("account_manager_id", sa.BigInteger(), nullable=True),
        sa.Column("plan_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("notes", sa.String(length=4000), nullable=True),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'revised', 'closed')",
            name=op.f("ck_ctl_contract_plans_status_valid"),
        ),
        sa.CheckConstraint("valid_from <= valid_to", name=op.f("ck_ctl_contract_plans_valid_period")),
        sa.ForeignKeyConstraint(
            ["account_manager_id"],
            ["sys_users.id"],
            name=op.f("fk_ctl_contract_plans_account_manager_id_sys_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ctl_contract_plans")),
        sa.UniqueConstraint("contract_id", name=op.f("uq_ctl_contract_plans_contract_id")),
    )
    op.create_table(
        "ctl_staff_profiles",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("skills", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("max_load_hours", sa.Numeric(precision=7, scale=2), server_default="40", nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("max_load_hours > 0", name=op.f("ck_ctl_staff_profiles_max_load_positive")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["sys_users.id"], name=op.f("fk_ctl_staff_profiles_user_id_sys_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ctl_staff_profiles")),
        sa.UniqueConstraint("user_id", name=op.f("uq_ctl_staff_profiles_user_id")),
    )
    op.create_table(
        "ctl_plan_kpi_targets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("kpi_code", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("target_value", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("warning_threshold", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["ctl_contract_plans.id"],
            name=op.f("fk_ctl_plan_kpi_targets_plan_id_ctl_contract_plans"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ctl_plan_kpi_targets")),
        sa.UniqueConstraint("plan_id", "kpi_code", name="uq_ctl_plan_kpi_targets_plan_kpi"),
    )
    op.create_table(
        "ctl_plan_members",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_in_contract", sa.String(length=32), nullable=False),
        sa.Column("is_role_play", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["ctl_contract_plans.id"],
            name=op.f("fk_ctl_plan_members_plan_id_ctl_contract_plans"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["sys_users.id"], name=op.f("fk_ctl_plan_members_user_id_sys_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ctl_plan_members")),
        sa.UniqueConstraint("plan_id", "user_id", "role_in_contract", name="uq_ctl_plan_members_plan_user_role"),
    )
    op.create_table(
        "ctl_shipment_plans",
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("shipment_no", sa.String(length=30), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=True),
        sa.Column("transport_mode", sa.String(length=16), nullable=True),
        sa.Column("required_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("template_id", sa.BigInteger(), nullable=True),
        sa.Column("process_code", sa.String(length=40), nullable=True),
        sa.Column("process_version", sa.Integer(), nullable=True),
        sa.Column("assignee_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("shipment_status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("status IN ('open', 'closed')", name=op.f("ck_ctl_shipment_plans_status_valid")),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["sys_users.id"],
            name=op.f("fk_ctl_shipment_plans_assignee_id_sys_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["ctl_contract_plans.id"],
            name=op.f("fk_ctl_shipment_plans_plan_id_ctl_contract_plans"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["md_process_templates.id"],
            name=op.f("fk_ctl_shipment_plans_template_id_md_process_templates"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ctl_shipment_plans")),
        sa.UniqueConstraint("shipment_id", name=op.f("uq_ctl_shipment_plans_shipment_id")),
    )
    op.create_index(
        "ix_ctl_shipment_plans_assignee_status", "ctl_shipment_plans", ["assignee_id", "status"], unique=False
    )
    op.create_index("ix_ctl_shipment_plans_contract", "ctl_shipment_plans", ["contract_id"], unique=False)
    op.create_table(
        "ctl_shipment_tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shipment_plan_id", sa.BigInteger(), nullable=False),
        sa.Column("step_code", sa.String(length=20), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("responsible_role", sa.String(length=32), nullable=False),
        sa.Column("required_skill", sa.String(length=40), nullable=True),
        sa.Column("standard_duration_hours", sa.Numeric(precision=7, scale=2), nullable=False),
        sa.Column("depends_on", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_checkpoint", sa.Boolean(), nullable=False),
        sa.Column("checkpoint_type", sa.String(length=40), nullable=True),
        sa.Column("planned_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assignee_id", sa.BigInteger(), nullable=True),
        sa.Column("controller_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.CheckConstraint("status IN ('open', 'closed')", name=op.f("ck_ctl_shipment_tasks_status_valid")),
        sa.CheckConstraint(
            "controller_id IS NULL OR assignee_id IS NULL OR controller_id <> assignee_id",
            name=op.f("ck_ctl_shipment_tasks_four_eyes"),
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["sys_users.id"],
            name=op.f("fk_ctl_shipment_tasks_assignee_id_sys_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["controller_id"],
            ["sys_users.id"],
            name=op.f("fk_ctl_shipment_tasks_controller_id_sys_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_plan_id"],
            ["ctl_shipment_plans.id"],
            name=op.f("fk_ctl_shipment_tasks_shipment_plan_id_ctl_shipment_plans"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ctl_shipment_tasks")),
        sa.UniqueConstraint("shipment_plan_id", "step_code", name="uq_ctl_shipment_tasks_plan_step"),
    )
    op.create_index(
        "ix_ctl_shipment_tasks_assignee_status", "ctl_shipment_tasks", ["assignee_id", "status"], unique=False
    )
    op.create_table(
        "ctl_assignments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("assignee_id", sa.BigInteger(), nullable=True),
        sa.Column("previous_assignee_id", sa.BigInteger(), nullable=True),
        sa.Column("assigned_by", sa.BigInteger(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("score", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("method IN ('auto', 'manual')", name=op.f("ck_ctl_assignments_method_valid")),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["sys_users.id"],
            name=op.f("fk_ctl_assignments_assignee_id_sys_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["ctl_shipment_tasks.id"],
            name=op.f("fk_ctl_assignments_task_id_ctl_shipment_tasks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ctl_assignments")),
    )
    op.create_index("ix_ctl_assignments_assignee", "ctl_assignments", ["assignee_id"], unique=False)
    op.create_index("ix_ctl_assignments_shipment", "ctl_assignments", ["shipment_id", "id"], unique=False)
    op.create_index(
        "uq_ctl_assignments_current",
        "ctl_assignments",
        ["shipment_id"],
        unique=True,
        postgresql_where=sa.text("is_current AND task_id IS NULL"),
    )
    _seed()
    _triggers()


NK = [
    ("NK-01", "Tiếp nhận thông báo hàng đến và bộ chứng từ", "DOCS", 4, None),
    ("NK-02", "Kiểm tra chéo bộ chứng từ (B5)", "DOCS", 4, "DOC_REVIEW"),
    ("NK-03", "Xác định mã HS, thuế suất, chính sách mặt hàng", "CUSTOMS_CLEARANCE", 8, "HS_REVIEW"),
    ("NK-04", "Lấy lệnh giao hàng, thanh toán phí tại cảng đến", "DOCS", 4, None),
    ("NK-05", "Lập và truyền tờ khai hải quan điện tử", "CUSTOMS_CLEARANCE", 4, "DECLARATION_APPROVAL"),
    ("NK-06", "Xử lý phân luồng: xuất trình hồ sơ, kiểm tra thực tế", "CUSTOMS_CLEARANCE", 8, None),
    ("NK-07", "Nộp thuế, thông quan, thủ tục giám sát tại cổng cảng", "CUSTOMS_CLEARANCE", 4, None),
    ("NK-08", "Nhận container, vận chuyển nội địa", "ROAD_TRANSPORT", 24, None),
    ("NK-09", "Giao hàng cho người nhận, ký biên bản", "ROAD_TRANSPORT", 8, "DELIVERY_CHECK"),
    ("NK-10", "Trả vỏ container, tập hợp hồ sơ, đóng lô", "DOCS", 24, None),
]
VC = [
    ("VC-01", "Nhận lệnh vận chuyển, điều xe", "ROAD_TRANSPORT", 2, None),
    ("VC-02", "Giao hàng, ký phiếu giao hàng", "ROAD_TRANSPORT", 6, "DELIVERY_CHECK"),
    ("VC-03", "Tập hợp hồ sơ, đóng lô", "DOCS", 4, None),
]


def _seed() -> None:
    templates = sa.table(
        "md_process_templates",
        sa.column("id", sa.BigInteger),
        sa.column("process_code", sa.String),
        sa.column("version", sa.Integer),
        sa.column("name", sa.String),
        sa.column("service_codes", postgresql.JSONB),
        sa.column("direction", sa.String),
        sa.column("transport_modes", postgresql.JSONB),
        sa.column("status", sa.String),
        sa.column("approved_at", sa.DateTime(timezone=True)),
    )
    steps = sa.table(
        "md_process_steps",
        sa.column("template_id", sa.BigInteger),
        sa.column("step_code", sa.String),
        sa.column("seq", sa.Integer),
        sa.column("name", sa.String),
        sa.column("responsible_role", sa.String),
        sa.column("required_skill", sa.String),
        sa.column("standard_duration_hours", sa.Numeric),
        sa.column("depends_on", postgresql.JSONB),
        sa.column("is_checkpoint", sa.Boolean),
        sa.column("checkpoint_type", sa.String),
    )
    conn = op.get_bind()
    for code, name, services, direction, modes, rows in (
        (
            "QT-NK-SEA-FCL",
            "Nhập khẩu đường biển hàng nguyên container – thông quan + vận chuyển nội địa",
            ["CUSTOMS_CLEARANCE", "ROAD_TRANSPORT"],
            "import",
            ["sea_fcl"],
            NK,
        ),
        ("QT-VC-NOIDIA", "Vận chuyển nội địa đường bộ", ["ROAD_TRANSPORT"], "domestic", ["road"], VC),
    ):
        template_id = conn.execute(
            templates.insert()
            .values(
                process_code=code,
                version=1,
                name=name,
                service_codes=services,
                direction=direction,
                transport_modes=modes,
                status="approved",
                approved_at=sa.func.now(),
            )
            .returning(templates.c.id)
        ).scalar_one()
        previous = None
        for seq, (step_code, step_name, skill, hours, checkpoint) in enumerate(rows, 1):
            conn.execute(
                steps.insert().values(
                    template_id=template_id,
                    step_code=step_code,
                    seq=seq,
                    name=step_name,
                    responsible_role="ops_staff",
                    required_skill=skill,
                    standard_duration_hours=hours,
                    depends_on=[previous] if previous else [],
                    is_checkpoint=checkpoint is not None,
                    checkpoint_type=checkpoint,
                )
            )
            previous = step_code


def _triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION ctl_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Không xóa bản ghi bảng % (dấu vết xử lý / phân công)', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END $$;
        CREATE TRIGGER sys_event_consumptions_no_delete BEFORE DELETE ON sys_event_consumptions
            FOR EACH ROW EXECUTE FUNCTION ctl_no_delete();

        CREATE FUNCTION ctl_assignments_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Không xóa lịch sử phân công' USING ERRCODE = 'restrict_violation';
            END IF;
            IF (to_jsonb(NEW) - 'is_current') IS DISTINCT FROM (to_jsonb(OLD) - 'is_current')
               OR (NEW.is_current AND NOT OLD.is_current) THEN
                RAISE EXCEPTION 'Lịch sử phân công chỉ ghi thêm (chỉ được bỏ cờ is_current)'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER ctl_assignments_guard BEFORE UPDATE OR DELETE ON ctl_assignments
            FOR EACH ROW EXECUTE FUNCTION ctl_assignments_guard();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER ctl_assignments_guard ON ctl_assignments;
        DROP TRIGGER sys_event_consumptions_no_delete ON sys_event_consumptions;
        DROP FUNCTION ctl_assignments_guard();
        DROP FUNCTION ctl_no_delete();
        """
    )
    op.drop_index(
        "uq_ctl_assignments_current",
        table_name="ctl_assignments",
        postgresql_where=sa.text("is_current AND task_id IS NULL"),
    )
    op.drop_index("ix_ctl_assignments_shipment", table_name="ctl_assignments")
    op.drop_index("ix_ctl_assignments_assignee", table_name="ctl_assignments")
    op.drop_table("ctl_assignments")
    op.drop_index("ix_ctl_shipment_tasks_assignee_status", table_name="ctl_shipment_tasks")
    op.drop_table("ctl_shipment_tasks")
    op.drop_index("ix_ctl_shipment_plans_contract", table_name="ctl_shipment_plans")
    op.drop_index("ix_ctl_shipment_plans_assignee_status", table_name="ctl_shipment_plans")
    op.drop_table("ctl_shipment_plans")
    op.drop_table("ctl_plan_members")
    op.drop_table("ctl_plan_kpi_targets")
    op.drop_table("ctl_staff_profiles")
    op.drop_table("ctl_contract_plans")
    op.drop_table("md_process_steps")
    op.drop_index("ix_sys_event_consumptions_status", table_name="sys_event_consumptions")
    op.drop_table("sys_event_consumptions")
    op.drop_index(
        "uq_md_process_templates_one_approved",
        table_name="md_process_templates",
        postgresql_where=sa.text("status = 'approved'"),
    )
    op.drop_table("md_process_templates")
