"""
Cấu hình pytest cho test tích hợp: dùng CSDL PostgreSQL riêng cho test
(TEST_DATABASE_URL, mặc định logistics_3pl_test của docker-compose) và thư mục
upload tạm; không đụng tới CSDL dev/prod hay uploads/ thật.

Schema tạo bằng Alembic (migrations/) một lần mỗi phiên; mỗi test chỉ xóa dữ liệu (TRUNCATE).

Trước khi chạy: docker compose up -d db
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

# Khóa ký JWT chỉ dùng cho test (06 §6: JWT_SECRET bắt buộc) – đặt trước khi đọc Settings.
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-" + "x" * 32)
# Khóa Fernet mã hóa totp_secret chỉ dùng cho test (Giai đoạn 6) – khóa cố định, không phải khóa thật.
os.environ.setdefault("TOTP_ENCRYPTION_KEY", "dGVzdC1vbmx5LXRvdHAta2V5LTMyLWJ5dGVzLWxvbmc=")
# Thư không gửi thật: giữ trong app.shared.notifications.mail.OUTBOX để test đọc đường dẫn
os.environ["MAIL_BACKEND"] = "memory"
# Mặc định không dùng hàng đợi Redis (tác vụ chạy nền trong TestClient); test hàng đợi tự bật – tests/core/test_jobs.py
os.environ["JOB_QUEUE_URL"] = ""

from app.core.config import Settings, get_settings  # noqa: E402

# Đọc qua Settings (biến môi trường + file .env), không dùng os.getenv: đổi cổng/URL trong .env vẫn có hiệu lực.
TEST_DATABASE_URL = Settings().test_database_url

# Bộ test xóa và tạo lại toàn bộ bảng: chặn chạy nhầm trên CSDL không phải CSDL test.
_db_name = make_url(TEST_DATABASE_URL).database or ""
if not _db_name.endswith("_test"):
    raise RuntimeError(f"TEST_DATABASE_URL phải trỏ tới CSDL có tên kết thúc bằng '_test' (đang là '{_db_name}')")

_TMP = Path(tempfile.mkdtemp(prefix="l3pl_test_"))
# Dọn khi tiến trình kết thúc – kể cả `pytest --co` (không chạy fixture) hay phiên test bị dừng giữa chừng
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
get_settings.cache_clear()  # Settings dùng chung đọc lại với DATABASE_URL = CSDL test

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.migrations import upgrade_to_head  # noqa: E402
from app.legacy_v1.database import Base as LegacyBase  # noqa: E402
from app.main import create_app  # noqa: E402
from app.shared.identity import service as identity_service  # noqa: E402
from app.shared.identity.schemas import UserCreate  # noqa: E402

# Mọi bảng do ứng dụng quản lý (v1 + v2) – dùng để xóa dữ liệu giữa các test.
ALL_METADATA = [LegacyBase.metadata, Base.metadata]
DEFAULT_PASSWORD = "MatKhau@2026"


# Dữ liệu khởi tạo do migration seed (Giai đoạn 11: quy trình mẫu) – nạp lại sau mỗi lần xóa dữ liệu giữa các test.
SEED_TABLES = (
    "md_process_templates",
    "md_process_steps",
    "asm_kpi_definitions",  # Giai đoạn 15: danh mục KPI
    "ops_document_requirements",  # Giai đoạn 17: quy tắc chứng từ bắt buộc
    "md_cashbook_categories",  # Giai đoạn 20: khoản mục thu – chi
)
_SEED: dict[str, list[dict]] = {}


def capture_seed() -> None:
    with engine.connect() as conn:
        for table in SEED_TABLES:
            _SEED[table] = [dict(r) for r in conn.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()]


def truncate_all() -> None:
    tables = ", ".join(t.name for md in ALL_METADATA for t in md.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        for table in SEED_TABLES:
            rows = _SEED.get(table)
            if rows:
                conn.execute(Base.metadata.tables[table].insert(), rows)
                conn.execute(
                    text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))")
                )


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
    capture_seed()
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_settings():
    """Test đổi biến môi trường (monkeypatch) không để lại Settings đã cache cho test sau; hộp thư trống mỗi test."""
    from app.shared.notifications import mail

    mail.OUTBOX.clear()
    yield
    get_settings.cache_clear()
    mail.OUTBOX.clear()


def link_token(to: str, path: str) -> str:
    """Token trong đường dẫn …/<path>#token=… của thư gần nhất gửi tới `to`."""
    import re

    from app.shared.notifications import mail

    for m in reversed(mail.OUTBOX):
        if m.to == to:
            found = re.search(rf"/{path}#token=([A-Za-z0-9_-]+)", m.body)
            if found:
                return found.group(1)
    raise AssertionError(f"Không có thư {path} gửi tới {to}")


def totp_now(username: str) -> str:
    """Mã TOTP hiện tại của người dùng (đọc secret trong CSDL). Chỉ dùng trong test: xóa totp_last_step để đăng
    nhập nhiều lần trong cùng chu kỳ 30 giây không bị coi là dùng lại mã."""
    from sqlalchemy import select, update

    from app.core import secretbox, totp
    from app.core.timeutil import now_utc
    from app.shared.identity.models import SysUser

    with SessionLocal() as db:
        db.execute(update(SysUser).where(SysUser.username == username).values(totp_last_step=None))
        db.commit()
        secret = db.scalar(select(SysUser.totp_secret).where(SysUser.username == username))
    return totp.code_at(secretbox.decrypt(secret), now_utc())


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


def pdf_bytes(text: str = "Hop dong") -> bytes:
    """PDF tối thiểu hợp lệ (đủ để nhận dạng theo magic bytes %PDF-)."""
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
        + f"% {text}\n".encode()
        + b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )


DEFAULT_SCOPE = {"service_code": "CUSTOMS_CLEARANCE", "params": {"port": "VNHPH"}}
DEFAULT_RATE = {
    "rate_code": "TK-01",
    "service_code": "CUSTOMS_CLEARANCE",
    "charge_name": "Phí khai báo hải quan",
    "basis": "per_declaration",
    "unit_price": "1500000.00",
    "currency": "VND",
    "tax_rate": "8.00",
}


def attach(contract_id: int, kind: str = "signed_contract", content: bytes | None = None, filename: str = "hd.pdf"):
    """Đính kèm file vào hợp đồng trực tiếp qua service (không qua API)."""
    from io import BytesIO

    from starlette.datastructures import UploadFile

    from app.flows.operations import service as ops_service
    from app.flows.operations.models import AttachmentKind

    upload = UploadFile(file=BytesIO(content or pdf_bytes()), filename=filename)
    with SessionLocal() as db:
        return ops_service.add_attachment(db, contract_id, upload, AttachmentKind(kind), None, None, actor_id=0)


@pytest.fixture()
def make_contract(client):
    """make_contract(party_id) → hợp đồng `active`, `portal_enabled`, đang hiệu lực (mặc định: từ 30 ngày trước, 1 năm),
    có 1 phạm vi, 1 biểu giá, bản scan đã ký (đủ điều kiện kích hoạt). `status="draft"` để giữ bản nháp; tham số khác
    ghi đè trường của ContractCreate."""
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
            "service_scopes": [DEFAULT_SCOPE],
            "rates": [DEFAULT_RATE],
            **fields,
        }
        with SessionLocal() as db:
            contract = ops_service.create_contract(db, ContractCreate(**data), actor_id=0)
        if status == "active":
            attach(contract.id)
            with SessionLocal() as db:
                contract = ops_service.activate_contract(db, contract.id, actor_id=0, expected_version=None)
        return contract

    return _make


@pytest.fixture()
def login(client):
    """login("an") → phản hồi JSON của POST /api/v2/auth/login (gồm access_token, refresh_token)."""

    def _login(username: str, password: str = DEFAULT_PASSWORD) -> dict:
        """Đi hết các bước: mật khẩu → (thiết lập 2FA lần đầu | mã TOTP) → token."""
        r = client.post("/api/v2/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        body = r.json()
        if body.get("mfa_setup_required"):
            setup = client.post("/api/v2/auth/login/2fa-setup", json={"mfa_token": body["mfa_token"]})
            assert setup.status_code == 200, setup.text
            r = client.post(
                "/api/v2/auth/login/2fa-enable", json={"mfa_token": body["mfa_token"], "code": totp_now(username)}
            )
        elif body.get("mfa_required"):
            r = client.post(
                "/api/v2/auth/login/totp", json={"mfa_token": body["mfa_token"], "code": totp_now(username)}
            )
        assert r.status_code == 200, r.text
        return r.json()

    return _login


@pytest.fixture()
def accept_invite(client):
    """accept_invite("lan@minhan.vn") → đặt mật khẩu DEFAULT_PASSWORD bằng đường dẫn trong thư mời gần nhất."""

    def _accept(email: str, password: str = DEFAULT_PASSWORD) -> None:
        token = link_token(email, "accept-invite")
        r = client.post("/api/v2/auth/invitations/accept", json={"token": token, "new_password": password})
        assert r.status_code == 204, r.text

    return _accept


@pytest.fixture()
def auth_headers(login):
    """auth_headers("an") → {"Authorization": "Bearer <access token>"}."""

    def _headers(username: str, password: str = DEFAULT_PASSWORD) -> dict[str, str]:
        return {"Authorization": f"Bearer {login(username, password)['access_token']}"}

    return _headers


@pytest.fixture()
def upload_dir() -> Path:
    return Path(os.environ["UPLOAD_DIR"])


def location_ids(*codes: str) -> list[int]:
    """Giai đoạn 17: id địa điểm `md_locations` theo mã (tạo nếu chưa có) – điểm đi / đến của lô hàng trong test."""
    from app.shared.masterdata.models import MdLocation

    names = {"CNSHA": ("Cảng Thượng Hải", "seaport", "CN"), "VNHPH": ("Cảng Hải Phòng", "seaport", "VN")}
    out = []
    with SessionLocal() as db:
        for code in codes or ("CNSHA", "VNHPH"):
            loc = db.scalar(select(MdLocation).where(MdLocation.code == code))
            if loc is None:
                name, kind, country = names.get(code, (code, "warehouse", "VN"))
                loc = MdLocation(code=code, name=name, location_type=kind, country_code=country)
                db.add(loc)
                db.commit()
            out.append(loc.id)
    return out
