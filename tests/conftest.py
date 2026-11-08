"""
Cấu hình pytest cho test tích hợp: dùng CSDL PostgreSQL riêng cho test
(TEST_DATABASE_URL, mặc định logistics_3pl_test của docker-compose) và thư mục
upload tạm; không đụng tới CSDL dev/prod hay uploads/ thật.

Schema tạo bằng Alembic (migrations/) một lần mỗi phiên; mỗi test chỉ xóa dữ liệu (TRUNCATE).

Trước khi chạy: docker compose up -d db
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

# Khóa ký JWT chỉ dùng cho test (06 §6: JWT_SECRET bắt buộc) – đặt trước khi đọc Settings.
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-" + "x" * 32)

from app.core.config import Settings, get_settings  # noqa: E402

# Đọc qua Settings (biến môi trường + file .env), không dùng os.getenv: đổi cổng/URL trong .env vẫn có hiệu lực.
TEST_DATABASE_URL = Settings().test_database_url

# Bộ test xóa và tạo lại toàn bộ bảng: chặn chạy nhầm trên CSDL không phải CSDL test.
_db_name = make_url(TEST_DATABASE_URL).database or ""
if not _db_name.endswith("_test"):
    raise RuntimeError(f"TEST_DATABASE_URL phải trỏ tới CSDL có tên kết thúc bằng '_test' (đang là '{_db_name}')")

_TMP = Path(tempfile.mkdtemp(prefix="l3pl_test_"))
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
get_settings.cache_clear()  # Settings dùng chung đọc lại với DATABASE_URL = CSDL test

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.migrations import upgrade_to_head  # noqa: E402
from app.legacy_v1.database import Base, engine  # noqa: E402
from app.main import create_app  # noqa: E402


def reset_database() -> None:
    """Xóa sạch schema public của CSDL test (gồm alembic_version) – CSDL về trạng thái trống."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="session", autouse=True)
def _database():
    """Mỗi phiên test: CSDL trống rồi tạo schema bằng Alembic (không dùng create_all)."""
    reset_database()
    upgrade_to_head(engine)
    yield
    engine.dispose()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture()
def client():
    # Xóa dữ liệu, giữ schema; lifespan của create_app() chạy upgrade head (đã ở head – không đổi gì).
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def upload_dir() -> Path:
    return Path(os.environ["UPLOAD_DIR"])
