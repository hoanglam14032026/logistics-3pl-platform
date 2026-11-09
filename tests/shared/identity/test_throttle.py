"""Giới hạn tần suất mọi API và tải file theo tài khoản (Giai đoạn 7; 06 §3), trên PostgreSQL."""

import anyio
import jwt
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import JWT_ISSUER
from app.shared.audit.models import SysAuditLog
from app.shared.files.models import SysFile
from tests.conftest import pdf_bytes

ME = "/api/v2/auth/me"


@pytest.fixture()
def limits(monkeypatch):
    def _set(**values):
        for name, value in values.items():
            monkeypatch.setenv(name.upper(), str(value))
        get_settings.cache_clear()

    return _set


def _is_429(r) -> dict:
    assert r.status_code == 429, r.text
    err = r.json()["error"]
    assert err["code"] == "RATE_LIMITED" and err["request_id"] == r.headers["X-Request-ID"]
    assert 1 <= int(r.headers["Retry-After"]) <= get_settings().rate_limit_window_sec
    return err


def _limited_logs() -> list[dict]:
    with SessionLocal() as db:
        return [log.after for log in db.scalars(select(SysAuditLog).where(SysAuditLog.action == "api.rate_limited"))]


def test_api_limit_per_account(client, make_user, auth_headers, limits):
    an = make_user("an.nv")
    make_user("binh.nv")
    a, b = auth_headers("an.nv"), auth_headers("binh.nv")  # đăng nhập chưa có token: tính theo IP, không theo tài khoản
    limits(rate_limit_api_per_user=3)
    assert [client.get(ME, headers=a).status_code for _ in range(3)] == [200, 200, 200]
    _is_429(client.get(ME, headers=a))
    _is_429(client.get("/api/v2/md/parties", headers=a))  # mọi API, không chỉ một endpoint
    assert client.get(ME, headers=b).status_code == 200  # tài khoản khác không bị ảnh hưởng
    assert _limited_logs() == [{"scope": "api_user", "key": f"user:{an.id}"}]  # ghi nhật ký một lần


def test_anonymous_requests_limited_by_ip(client, limits):
    limits(rate_limit_api_per_ip=2)
    assert [client.get(ME).status_code for _ in range(2)] == [401, 401]
    _is_429(client.get(ME))
    _is_429(client.post("/api/v2/auth/password/reset", json={"token": "x" * 20, "new_password": "MatKhau@2026"}))
    assert client.get("/health").status_code == 200  # ngoài /api: không giới hạn
    assert client.options(ME, headers={"Origin": "http://x", "Access-Control-Request-Method": "GET"}).status_code != 429


def test_forged_token_counts_by_ip_not_victim_account(client, make_user, auth_headers, limits):
    """Token giả (sai chữ ký) mang sub của người khác không làm cạn hạn mức của tài khoản đó."""
    make_user("an.nv")
    headers = auth_headers("an.nv")
    forged = jwt.encode({"sub": "1", "type": "access", "iss": JWT_ISSUER, "iat": 0, "exp": 2**31}, "khac" * 10)
    limits(rate_limit_api_per_user=2, rate_limit_api_per_ip=100)
    for _ in range(5):
        assert client.get(ME, headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    assert client.get(ME, headers=headers).status_code == 200


def test_file_upload_limit_rejects_before_storing(client, make_user, auth_headers, make_party, make_contract, limits):
    manager = make_user("quan.ly", roles=["manager"])
    h = auth_headers("quan.ly")
    contract = make_contract(make_party("Khách A", tax_code="0100000001").id, status="draft")
    with SessionLocal() as db:
        files_before = db.scalar(select(func.count()).select_from(SysFile))
    limits(rate_limit_file_upload_per_user=1)

    def upload():
        return client.post(
            f"/api/v2/ops/service-contracts/{contract.id}/attachments",
            files={"file": ("pl.pdf", pdf_bytes("Phu luc"), "application/pdf")},
            data={"kind": "other"},
            headers=h,
        )

    assert upload().status_code == 201
    _is_429(upload())
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SysFile)) == files_before + 1
    assert client.get(ME, headers=h).status_code == 200  # API thường vẫn dùng được
    assert {"scope": "file_upload", "key": f"user:{manager.id}"} in _limited_logs()


def test_file_download_limit(client, make_user, auth_headers, make_party, make_contract, limits):
    make_user("quan.ly", roles=["manager"])
    h = auth_headers("quan.ly")
    contract = make_contract(make_party("Khách A", tax_code="0100000001").id)
    [att] = client.get(f"/api/v2/ops/service-contracts/{contract.id}/attachments", headers=h).json()
    limits(rate_limit_file_download_per_user=2)
    assert [client.get(att["file"]["download_url"], headers=h).status_code for _ in range(2)] == [200, 200]
    _is_429(client.get(att["file"]["download_url"], headers=h))
    assert client.get(f"/api/v2/ops/service-contracts/{contract.id}", headers=h).status_code == 200


def test_rate_limit_can_be_disabled(client, limits):
    limits(rate_limit_api_per_ip=1, rate_limit_enabled="false")
    assert [client.get(ME).status_code for _ in range(3)] == [401, 401, 401]


def test_rejected_upload_body_is_never_read(monkeypatch):
    """429 trả trước khi đọc body: file lớn bị từ chối không chiếm băng thông / đĩa tạm."""
    from app.shared.identity import throttle

    monkeypatch.setattr(throttle, "_check", lambda checks, window: 30)
    reads, sent = [], []

    async def app(scope, receive, send):  # pragma: no cover – không được gọi
        raise AssertionError("app không được chạy khi đã vượt ngưỡng")

    async def receive():
        reads.append(1)
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v2/ops/service-contracts/1/attachments",
        "headers": [(b"content-type", b"multipart/form-data; boundary=x")],
        "client": ("10.0.0.1", 1),
        "query_string": b"",
        "state": {"request_id": "abc"},
    }
    anyio.run(throttle.RateLimitMiddleware(app), scope, receive, send)
    assert reads == [] and sent[0]["status"] == 429
    assert (b"retry-after", b"30") in sent[0]["headers"]
