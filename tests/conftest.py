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

from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.migrations import upgrade_to_head  # noqa: E402
from app.legacy_v1.database import Base as LegacyBase  # noqa: E402
from app.main import create_app  # noqa: E402
from app.shared.identity import service as identity_service  # noqa: E402
from app.shared.identity.schemas import UserCreate  # noqa: E402

# Mọi bảng do ứng dụng quản lý (v1 + v2) – dùng để xóa dữ liệu giữa các test.
ALL_METADATA = [LegacyBase.metadata, Base.metadata]
DEFAULT_PASSWORD = "MatKhau@2026"


def truncate_all() -> None:
    tables = ", ".join(t.name for md in ALL_METADATA for t in md.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


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


@pytest.fixture(autouse=True)
def _fresh_settings():
    """Test đổi biến môi trường (monkeypatch) không để lại Settings đã cache cho test sau."""
    yield
    get_settings.cache_clear()


@pytest.fixture()
def client():
    # Xóa dữ liệu, giữ schema; lifespan của create_app() chạy upgrade head (đã ở head – không đổi gì).
    truncate_all()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def make_user(client):
    """Tạo người dùng trực tiếp qua service: make_user("an", roles=["ops_staff"]) → SysUser."""

    def _make(username: str, roles=("ops_staff",), password: str = DEFAULT_PASSWORD, **fields):
        data = UserCreate(
            username=username,
            full_name=fields.pop("full_name", username.title()),
            password=password,
            roles=list(roles),
            **fields,
        )
        with SessionLocal() as db:
            return identity_service.create_user(db, data, actor_id=None)

    return _make


@pytest.fixture()
def make_party(client):
    """make_party("Cty A", party_type="customer", tax_code=...) → MdParty (tạo trực tiếp, không qua API)."""
    from app.shared.masterdata import service as md_service
    from app.shared.masterdata.schemas import PartyCreate

    def _make(name: str = "Công ty TNHH Minh An", party_type: str = "customer", **fields):
        with SessionLocal() as db:
            return md_service.create_party(db, PartyCreate(name=name, party_type=party_type, **fields), actor_id=0)

    return _make


@pytest.fixture()
def make_contract(client):
    """make_contract(party_id) → hợp đồng `active`, `portal_enabled`, đang hiệu lực (mặc định: từ 30 ngày trước, 1 năm).

    `status="draft"` để giữ bản nháp; tham số khác ghi đè trường của ContractCreate."""
    from datetime import timedelta

    from app.core.timeutil import today_vn
    from app.flows.operations import service as ops_service
    from app.flows.operations.schemas import ContractCreate

    def _make(party_id: int, status: str = "active", **fields):
        today = today_vn()
        data = {
            "customer_id": party_id,
            "signed_date": today - timedelta(days=30),
            "valid_from": today - timedelta(days=30),
            "valid_to": today + timedelta(days=335),
            **fields,
        }
        with SessionLocal() as db:
            contract = ops_service.create_contract(db, ContractCreate(**data), actor_id=0)
            if status == "active":
                contract = ops_service.activate_contract(db, contract.id, actor_id=0, expected_version=None)
            return contract

    return _make


@pytest.fixture()
def login(client):
    """login("an") → phản hồi JSON của POST /api/v2/auth/login (gồm access_token, refresh_token)."""

    def _login(username: str, password: str = DEFAULT_PASSWORD) -> dict:
        r = client.post("/api/v2/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return r.json()

    return _login


@pytest.fixture()
def auth_headers(login):
    """auth_headers("an") → {"Authorization": "Bearer <access token>"}."""

    def _headers(username: str, password: str = DEFAULT_PASSWORD) -> dict[str, str]:
        return {"Authorization": f"Bearer {login(username, password)['access_token']}"}

    return _headers


@pytest.fixture()
def upload_dir() -> Path:
    return Path(os.environ["UPLOAD_DIR"])
