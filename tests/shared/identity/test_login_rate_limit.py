"""Giới hạn tần suất POST /api/v2/auth/login theo IP và theo tên đăng nhập (06 §3), trên PostgreSQL."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.main import create_app
from app.shared.audit.models import SysAuditLog
from app.shared.identity.models import SysUser
from tests.conftest import DEFAULT_PASSWORD

LOGIN = "/api/v2/auth/login"


@pytest.fixture()
def limits(monkeypatch):
    def _set(per_ip: int = 100, per_username: int = 100):
        monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_PER_IP", str(per_ip))
        monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_PER_USERNAME", str(per_username))
        monkeypatch.setenv("AUTH_MAX_FAILED_LOGINS", "50")  # tách khỏi khóa tạm
        get_settings.cache_clear()

    return _set


@pytest.fixture()
def from_ip(client):
    """from_ip("1.2.3.4") → TestClient gửi request với IP client đó."""
    clients = []

    def _client(ip: str) -> TestClient:
        c = TestClient(create_app(), client=(ip, 50000))
        clients.append(c)
        return c

    yield _client
    for c in clients:
        c.close()


def _login(c, username: str, password: str = "SaiMatKhau1"):
    return c.post(LOGIN, json={"username": username, "password": password})


def test_limit_per_ip(client, make_user, limits, from_ip):
    limits(per_ip=3)
    make_user("anh")
    c = from_ip("10.0.0.1")
    for name in ("anh", "binh", "chi"):  # khác tên đăng nhập, cùng IP
        assert _login(c, name).status_code == 401
    r = _login(c, "dung")
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "RATE_LIMITED" and err["request_id"] == r.headers["X-Request-ID"]
    assert 1 <= int(r.headers["Retry-After"]) <= 60
    # Đúng mật khẩu cũng bị chặn khi đã vượt ngưỡng
    assert _login(c, "anh", DEFAULT_PASSWORD).status_code == 429
    # IP khác không bị ảnh hưởng
    assert _login(from_ip("10.0.0.2"), "anh", DEFAULT_PASSWORD).status_code == 200


def test_limit_per_username_across_ips(client, make_user, limits, from_ip):
    limits(per_username=2)
    make_user("binh")
    assert _login(from_ip("10.0.1.1"), "binh").status_code == 401
    assert _login(from_ip("10.0.1.2"), "  BINH ").status_code == 401  # chuẩn hóa như khi đăng nhập
    assert _login(from_ip("10.0.1.3"), "binh", DEFAULT_PASSWORD).status_code == 429
    assert _login(from_ip("10.0.1.4"), "nguoi-khac").status_code == 401


def test_rate_limited_attempts_do_not_reach_password_check(client, make_user, limits, from_ip):
    limits(per_username=2)
    make_user("chi")
    c = from_ip("10.0.2.1")
    for _ in range(6):
        _login(c, "chi")
    with SessionLocal() as db:
        user = db.scalar(select(SysUser).where(SysUser.username == "chi"))
        assert user.failed_login_count == 2  # 4 lần bị 429 không được tính là đăng nhập sai
        failed = db.scalars(select(SysAuditLog).where(SysAuditLog.action == "auth.login_failed")).all()
        limited = db.scalars(select(SysAuditLog).where(SysAuditLog.action == "auth.rate_limited")).all()
    assert len(failed) == 2
    assert len(limited) == 1  # chỉ ghi lần đầu vượt ngưỡng trong cửa sổ
    assert limited[0].after == {"scope": "login_username", "key": "chi"} and limited[0].ip == "10.0.2.1"


def test_default_limits_allow_normal_use(client, make_user, login):
    make_user("dung")
    for _ in range(5):
        login("dung")
