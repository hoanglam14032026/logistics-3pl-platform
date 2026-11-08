"""
Cấu hình pytest cho test tích hợp: dùng CSDL PostgreSQL riêng cho test
(TEST_DATABASE_URL, mặc định logistics_3pl_test của docker-compose) và thư mục
upload tạm; không đụng tới CSDL dev/prod hay uploads/ thật.

Trước khi chạy: docker compose up -d db
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://logistics:logistics@localhost:5432/logistics_3pl_test",
)

# Bộ test xóa và tạo lại toàn bộ bảng: chặn chạy nhầm trên CSDL không phải CSDL test.
_db_name = make_url(TEST_DATABASE_URL).database or ""
if not _db_name.endswith("_test"):
    raise RuntimeError(f"TEST_DATABASE_URL phải trỏ tới CSDL có tên kết thúc bằng '_test' (đang là '{_db_name}')")

_TMP = Path(tempfile.mkdtemp(prefix="l3pl_test_"))
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")

from fastapi.testclient import TestClient  # noqa: E402

from app.legacy_v1.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp():
    yield
    engine.dispose()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def upload_dir() -> Path:
    return Path(os.environ["UPLOAD_DIR"])
