"""
Chạy migration Alembic từ ứng dụng (lifespan của create_app()) – thay cho Base.metadata.create_all().

- Khóa advisory của PostgreSQL: nhiều tiến trình (nhiều worker uvicorn, nhiều container) khởi động cùng lúc
  thì chỉ một tiến trình chạy migration, các tiến trình khác chờ rồi thấy đã ở head.
- CSDL tạo bằng create_all trước bước 0.3 (có đủ 4 bảng v1, chưa có bảng alembic_version) được đánh dấu
  (stamp) ở revision gốc 0001 rồi mới upgrade, không tạo lại bảng, không mất dữ liệu.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, text

logger = logging.getLogger("app.migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

# Revision gốc: schema v1.1.0 đúng như create_all tạo ra.
BASELINE_REVISION = "0001"
BASELINE_TABLES = frozenset({"business_guides", "training_documents", "business_data_records", "video_resources"})

# Khóa advisory dùng riêng cho migration (số bất kỳ, cố định trong toàn hệ thống).
_ADVISORY_LOCK_KEY = 3_100_202_603


class SchemaStateError(RuntimeError):
    """CSDL ở trạng thái không xác định được (có một phần bảng v1 nhưng không có alembic_version)."""


def alembic_config(connection: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def head_revision() -> str:
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    if len(heads) != 1:
        raise SchemaStateError(f"Alembic phải có đúng 1 head, đang có {len(heads)}: {heads}")
    return heads[0]


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def _stamp_legacy_schema_if_needed(connection: Connection, cfg: Config) -> None:
    tables = set(inspect(connection).get_table_names())
    if "alembic_version" in tables:
        return
    present = BASELINE_TABLES & tables
    if not present:
        return
    if present != BASELINE_TABLES:
        missing = ", ".join(sorted(BASELINE_TABLES - present))
        raise SchemaStateError(
            f"CSDL có một phần bảng v1 nhưng chưa có alembic_version (thiếu: {missing}); cần kiểm tra thủ công"
        )
    logger.warning("CSDL tạo bằng create_all (chưa có alembic_version): đánh dấu revision %s", BASELINE_REVISION)
    command.stamp(cfg, BASELINE_REVISION)


def upgrade_to_head(engine: Engine) -> str:
    """Đưa schema lên head; trả về revision hiện tại sau khi chạy."""
    with engine.connect() as connection:
        is_pg = connection.dialect.name == "postgresql"
        if is_pg:
            connection.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})
            connection.commit()
        try:
            cfg = alembic_config(connection)
            _stamp_legacy_schema_if_needed(connection, cfg)
            command.upgrade(cfg, "head")
            connection.commit()
            revision = current_revision(connection)
            connection.commit()
            return revision
        except BaseException:
            connection.rollback()
            raise
        finally:
            if is_pg:
                connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY})
                connection.commit()
