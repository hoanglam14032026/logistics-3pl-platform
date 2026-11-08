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
    assert head_revision() == "0004"


def test_upgrade_from_empty_creates_all_tables():
    reset_database()
    assert upgrade_to_head(engine) == head_revision()
    assert _tables() == BASELINE_TABLES | IDENTITY_TABLES | MASTERDATA_TABLES | AUDIT_TABLES | {"alembic_version"}


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
