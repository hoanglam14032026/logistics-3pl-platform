"""Test migration Alembic (07 §1, §6 điểm 3): upgrade/downgrade, khớp model, đánh dấu CSDL tạo bằng create_all.

Mỗi test tự đưa CSDL test về trạng thái cần thiết; fixture `_restore_head` trả CSDL về head (trống dữ liệu)
cho các test khác.
"""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core.db import engine
from app.core.migrations import (
    BASELINE_TABLES,
    SchemaStateError,
    alembic_config,
    current_revision,
    head_revision,
    upgrade_to_head,
)
from app.legacy_v1.database import Base as LegacyBase
from app.main import create_app
from tests.conftest import ALL_METADATA, reset_database

IDENTITY_TABLES = {"sys_users", "sys_user_roles", "sys_refresh_tokens"}
MASTERDATA_TABLES = {"md_parties"}
AUDIT_TABLES = {"sys_audit_logs", "sys_rate_limit_counters"}
OPS_TABLES = {
    "ops_service_contracts",
    "sys_doc_sequences",
    "ops_contract_scopes",
    "ops_contract_sla",
    "ops_contract_rates",
    "ops_contract_versions",
    "ops_contract_attachments",
    "sys_files",
    "sys_user_tokens",
    "ops_shipments",
    "ops_shipment_status_history",
    "ops_delivery_records",
}
DOCAI_TABLES = {"ai_models", "sys_doc_jobs", "sys_doc_extractions"}
G10_TABLES = {"sys_events", "ops_commercial_invoices", "ops_commercial_invoice_items"}
G11_TABLES = {
    "sys_event_consumptions",
    "md_process_templates",
    "md_process_steps",
    "ctl_contract_plans",
    "ctl_plan_members",
    "ctl_plan_kpi_targets",
    "ctl_shipment_plans",
    "ctl_shipment_tasks",
    "ctl_assignments",
    "ctl_staff_profiles",
}
G12_TABLES = {"ctl_task_updates", "ctl_alerts"}
G13_TABLES = {"ops_tasks", "ctl_alert_notifications"}
G14_TABLES = {"asm_fact_contracts", "asm_fact_shipments", "asm_fact_tasks", "asm_fact_task_blocks", "asm_fact_alerts"}
G17_TABLES = {"ops_document_requirements"}
G19_TABLES = {
    "ops_contract_amendments",
    "ops_contract_terminations",
    "ops_contract_settlements",
    "asm_fact_settlements",
}
G20_TABLES = {"md_cashbook_categories", "ops_cash_vouchers", "ops_staff_advances"}
G18_TABLES = {
    "lrn_training_contracts",
    "lrn_contract_learners",
    "lrn_sessions",
    "lrn_attendance",
    "asm_fact_training_contracts",
    "asm_fact_enrollments",
    "asm_fact_training_sessions",
    "asm_fact_learning_progress",
    "asm_session_scores",
}
G23_TABLES = {"ops_document_catalog", "ops_contract_documents"}
G24_TABLES = {
    "lrn_topics",
    "lrn_resources",
    "lrn_courses",
    "lrn_course_items",
    "lrn_progress",
}  # P2 Learning: hoc lieu 7 loai
G16_TABLES = {
    "md_locations",
    "ops_documents",
    "ops_document_checks",
    "ops_document_check_items",
    "ops_checkpoints",
    "ops_checkpoint_evaluations",
}
G15_TABLES = {
    "asm_kpi_definitions",
    "asm_kpi_targets",
    "asm_fact_document_checks",
    "asm_fact_checkpoints",
    "asm_fact_customs",
    "asm_fact_incidents",
    "asm_fact_service_requests",
    "asm_calc_runs",
    "asm_fact_kpi_measurements",
    "asm_kpi_alerts",
}


@pytest.fixture(autouse=True)
def _restore_head():
    yield
    reset_database()
    upgrade_to_head(engine)


def _tables() -> set[str]:
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names())


def _revision() -> str | None:
    with engine.connect() as conn:
        return current_revision(conn)


def _alembic(fn, *args) -> None:
    with engine.begin() as conn:
        fn(alembic_config(conn), *args)


def test_single_head():
    assert head_revision() == "0024"


def test_upgrade_from_empty_creates_all_tables():
    reset_database()
    assert upgrade_to_head(engine) == head_revision()
    expected = (
        BASELINE_TABLES
        | IDENTITY_TABLES
        | MASTERDATA_TABLES
        | AUDIT_TABLES
        | OPS_TABLES
        | DOCAI_TABLES
        | G10_TABLES
        | G11_TABLES
        | G12_TABLES
        | G13_TABLES
        | G14_TABLES
        | G15_TABLES
        | G16_TABLES
        | G17_TABLES
        | G18_TABLES
        | G19_TABLES
        | G20_TABLES
        | G23_TABLES
        | G24_TABLES
    )
    assert _tables() == expected | {"alembic_version"}


def test_schema_after_upgrade_matches_models():
    """Không có khác biệt giữa schema do migration tạo và Base.metadata (tương đương `alembic check`)."""
    reset_database()
    upgrade_to_head(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), ALL_METADATA)
    assert diff == []


def test_downgrade_base_then_upgrade_again():
    upgrade_to_head(engine)
    _alembic(command.downgrade, "base")
    assert _tables() == {"alembic_version"}
    assert _revision() is None
    assert upgrade_to_head(engine) == head_revision()
    assert BASELINE_TABLES <= _tables()


def test_upgrade_is_idempotent():
    first = upgrade_to_head(engine)
    assert upgrade_to_head(engine) == first


def test_legacy_create_all_database_is_stamped_and_keeps_data():
    """CSDL v1 tạo bằng create_all (chưa có alembic_version): đánh dấu 0001, không tạo lại bảng, giữ dữ liệu."""
    reset_database()
    LegacyBase.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO business_guides (title, category, content) VALUES ('Cũ', 'kho', 'Giữ lại')"))

    assert upgrade_to_head(engine) == head_revision()

    with engine.connect() as conn:
        assert conn.execute(text("SELECT title FROM business_guides")).scalars().all() == ["Cũ"]
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == head_revision()


def test_partial_legacy_schema_is_rejected():
    reset_database()
    with engine.begin() as conn:
        LegacyBase.metadata.tables["business_guides"].create(conn)
    with pytest.raises(SchemaStateError, match="thiếu"):
        upgrade_to_head(engine)
    assert "alembic_version" not in _tables()


def test_lifespan_runs_migrations_on_empty_database():
    reset_database()
    with TestClient(create_app()) as c:
        assert c.get("/api/v1/guide").status_code == 200
    assert _revision() == head_revision()


def test_0008_encrypts_existing_totp_secrets_and_downgrade_decrypts():
    """Secret dạng rõ từ 0007 được mã hóa khi lên 0008 (giá trị đã mã hóa giữ nguyên); downgrade trả về dạng rõ."""
    from app.core import secretbox

    upgrade_to_head(engine)
    _alembic(command.downgrade, "0007")
    plain = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sys_users (username, full_name, password_hash, totp_secret, is_2fa_enabled) VALUES "
                "('a', 'A', 'x', :s, true), ('b', 'B', 'x', NULL, false)"
            ),
            {"s": plain},
        )
    _alembic(command.upgrade, "0008")
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT username, totp_secret FROM sys_users")).all())
    assert rows["b"] is None and rows["a"].startswith("gAAAAA") and secretbox.decrypt(rows["a"]) == plain
    _alembic(command.downgrade, "0007")
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT totp_secret FROM sys_users WHERE username = 'a'")) == plain


def test_0010_seeds_sample_model_and_downgrade_keeps_old_data():
    """0010 thêm model mẫu qwen3.8:27b (endpoint NULL = OLLAMA_URL); 0010 → 0009 → 0010 không mất dữ liệu cũ."""
    reset_database()
    upgrade_to_head(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT code, model_id, provider, endpoint, model_type, is_active, is_seeded FROM ai_models")
        ).all()
    assert rows == [("qwen3.8-27b", "qwen3.8:27b", "ollama", None, "vision", True, True)]
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO md_parties (party_type, name, tax_code) VALUES ('customer', 'Cty A', '0101234567')")
        )
    _alembic(command.downgrade, "0009")
    assert not (DOCAI_TABLES & _tables())
    with engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("md_parties")}
        assert "source_doc_job_id" not in cols
        assert conn.scalar(text("SELECT name FROM md_parties")) == "Cty A"
    _alembic(command.upgrade, "head")
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT source_doc_job_id FROM md_parties WHERE name = 'Cty A'")) is None
        assert conn.scalar(text("SELECT count(*) FROM ai_models")) == 1


def test_0011_moves_old_events_to_outbox_and_downgrade_reverts_status():
    """Sự kiện Giai đoạn 8–9 (dòng `event.<mã>` trong nhật ký) chuyển sang outbox `pending`, giữ event_id; job đang chờ
    người duyệt thứ 2 về needs_review khi hạ xuống 0010."""
    import json
    import uuid

    reset_database()
    upgrade_to_head(engine)
    _alembic(command.downgrade, "0010")
    event_id = str(uuid.uuid4())
    envelope = {
        "event_id": event_id,
        "event_code": "EV-112",
        "event_name": "ops.shipment.status_changed",
        "schema_version": 1,
        "occurred_at": "2026-11-09T01:00:00Z",
        "mode": "production",
        "actor_id": 7,
        "data": {"shipment_id": 3, "from": "draft", "to": "docs_collecting"},
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sys_audit_logs (actor_id, action, entity_type, entity_id, after) "
                "VALUES (7, 'event.EV-112', 'shipment', 3, CAST(:a AS jsonb)), "
                "(7, 'party.create', 'party', 1, '{}')"
            ),
            {"a": json.dumps(envelope)},
        )
    _alembic(command.upgrade, "head")
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT event_id::text, event_code, entity_id, status, consumers, payload FROM sys_events")
        ).all()
    assert rows == [(event_id, "EV-112", 3, "pending", ["control", "assessment"], envelope)]


def test_check_constraint_names_match_models():
    """CHECK do migration tạo đúng tên quy ước của model (compare_metadata không so CHECK)."""
    reset_database()
    upgrade_to_head(engine)
    from app.core.db import Base

    with engine.connect() as conn:
        inspector = inspect(conn)
        for table in (
            "sys_doc_jobs",
            "sys_events",
            "ops_commercial_invoices",
            "ops_commercial_invoice_items",
            *sorted(
                G11_TABLES
                | G12_TABLES
                | G13_TABLES
                | G14_TABLES
                | G15_TABLES
                | G16_TABLES
                | G17_TABLES
                | G18_TABLES
                | G19_TABLES
                | G20_TABLES
            ),
        ):
            in_db = {c["name"] for c in inspector.get_check_constraints(table)}
            in_model = {
                c.name for c in Base.metadata.tables[table].constraints if c.__class__.__name__ == "CheckConstraint"
            }
            assert in_db == in_model, table


def test_0012_seeds_process_templates_and_downgrades_cleanly():
    reset_database()
    upgrade_to_head(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT t.process_code, t.status, count(s.id), count(s.id) FILTER (WHERE s.is_checkpoint) "
                "FROM md_process_templates t JOIN md_process_steps s ON s.template_id = t.id "
                "GROUP BY t.process_code, t.status ORDER BY 1"
            )
        ).all()
    assert rows == [("QT-NK-SEA-FCL", "approved", 10, 4), ("QT-VC-NOIDIA", "approved", 3, 1)]
    _alembic(command.downgrade, "0011")
    assert not (
        (
            G11_TABLES
            | G12_TABLES
            | G13_TABLES
            | G14_TABLES
            | G15_TABLES
            | G16_TABLES
            | G17_TABLES
            | G18_TABLES
            | G19_TABLES
            | G20_TABLES
        )
        & _tables()
    )
    _alembic(command.upgrade, "head")
    assert G11_TABLES <= _tables()


def test_0015_assessment_facts_round_trip():
    reset_database()
    upgrade_to_head(engine)
    assert G14_TABLES <= _tables()
    _alembic(command.downgrade, "0014")
    assert not (G14_TABLES & _tables()) and G13_TABLES <= _tables()
    _alembic(command.upgrade, "head")
    assert G14_TABLES <= _tables()


def test_0016_kpi_catalog_seed_and_round_trip():
    reset_database()
    upgrade_to_head(engine)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT kpi_code, formula_key FROM asm_kpi_definitions ORDER BY kpi_code")).all()
    assert [r[0] for r in rows] == [f"KPI-{i:02d}" for i in range(1, 15)]
    _alembic(command.downgrade, "0015")
    assert not (G15_TABLES & _tables()) and G14_TABLES <= _tables()
    _alembic(command.upgrade, "head")
    assert G15_TABLES <= _tables()


def test_0017_locations_documents_checkpoints_round_trip():
    reset_database()
    upgrade_to_head(engine)
    _alembic(command.downgrade, "0016")
    assert not (G16_TABLES & _tables()) and G15_TABLES <= _tables()
    _alembic(command.upgrade, "head")
    assert G16_TABLES <= _tables()


def test_0018_requirements_seed_and_round_trip():
    reset_database()
    upgrade_to_head(engine)
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM ops_document_requirements WHERE is_active")).scalar()
    assert n == 8
    _alembic(command.downgrade, "0017")
    assert not (G17_TABLES & _tables()) and G16_TABLES <= _tables()
    _alembic(command.upgrade, "head")
    assert G17_TABLES <= _tables()


def test_0019_training_round_trip():
    reset_database()
    upgrade_to_head(engine)
    _alembic(command.downgrade, "0018")
    assert not (G18_TABLES & _tables()) and G17_TABLES <= _tables()
    _alembic(command.upgrade, "head")
    assert G18_TABLES <= _tables()


def test_0020_contract_lifecycle_round_trip():
    reset_database()
    upgrade_to_head(engine)
    _alembic(command.downgrade, "0019")
    assert not (G19_TABLES & _tables()) and G18_TABLES <= _tables()
    _alembic(command.upgrade, "head")
    assert G19_TABLES <= _tables()
