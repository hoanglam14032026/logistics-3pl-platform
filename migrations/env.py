"""
Môi trường Alembic.

- Chạy từ CLI (`alembic upgrade head`): tạo engine từ DATABASE_URL của Settings.
- Chạy từ ứng dụng (app/core/migrations.py): dùng kết nối truyền qua `config.attributes["connection"]`.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.legacy_v1 import models as _legacy_models  # noqa: F401  (đăng ký model v1 vào Base.metadata)
from app.legacy_v1.database import Base

config = context.config

# Chỉ cấu hình logging khi chạy CLI có alembic.ini; gọi từ ứng dụng thì giữ logging của ứng dụng.
if config.config_file_name is not None and config.attributes.get("connection") is None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Sinh SQL thay vì chạy trực tiếp (`alembic upgrade head --sql`)."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
