"""Test tích hợp /api/v2/users trên PostgreSQL (06 §4 – chỉ `admin`; 06 §2.1–2.4) và CLI tạo admin."""

import io

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.shared.identity import cli
from app.shared.identity.models import SysRefreshToken, SysUser
from tests.conftest import DEFAULT_PASSWORD

USERS = "/api/v2/users"
NEW = {
    "username": "Nguyen.Van-A",
    "email": "A@Example.VN",
    "full_name": "Nguyễn Văn A",
    "password": "MatKhau@2026",
    "roles": ["ops_staff"],
}


@pytest.fixture()
def admin(client, make_user, auth_headers):
    make_user("quantri", roles=["admin"])
    return auth_headers("quantri")


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


def _create(client, admin, **override) -> dict:
    r = client.post(USERS, json={**NEW, **override}, headers=admin)
    assert r.status_code == 201, r.text
    return r.json()


# --- RBAC ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role", ["ops_staff", "manager", "business_manager", "learner", "instructor", "content_editor", "customer"]
)
def test_only_admin_manages_users(client, make_user, make_party, auth_headers, role):
    extra = {"party_id": make_party().id} if role == "customer" else {}
    make_user("nguoidung", roles=[role], **extra)
    headers = auth_headers("nguoidung")
    _error(client.get(USERS, headers=headers), 403, "FORBIDDEN")
    _error(client.post(USERS, json=NEW, headers=headers), 403, "FORBIDDEN")
    _error(client.get(f"{USERS}/1", headers=headers), 403, "FORBIDDEN")
    _error(client.patch(f"{USERS}/1", json={"full_name": "X"}, headers=headers), 403, "FORBIDDEN")
    _error(client.post(f"{USERS}/1:unlock", headers=headers), 403, "FORBIDDEN")


def test_anonymous_gets_401(client):
    for r in (client.get(USERS), client.post(USERS, json=NEW), client.get(f"{USERS}/1")):
        _error(r, 401, "UNAUTHENTICATED")


# --- Tạo ---------------------------------------------------------------------------------------------------


def test_create_user(client, admin):
    r = client.post(USERS, json={**NEW, "roles": ["manager", "ops_staff", "manager"]}, headers=admin)
    assert r.status_code == 201
    body = r.json()
    assert r.headers["Location"] == f"{USERS}/{body['id']}"
    assert r.headers["ETag"] == '"1"' and body["row_version"] == 1
    assert (body["username"], body["email"], body["roles"]) == (
        "nguyen.van-a",
        "a@example.vn",
        ["manager", "ops_staff"],
    )
    assert body["is_2fa_enabled"] is False and "password" not in body and "password_hash" not in body
    with SessionLocal() as db:
        user = db.get(SysUser, body["id"])
        assert user.password_hash.startswith("$argon2id$") and user.totp_secret is None
        assert user.created_by == user.updated_by == 1  # id của admin
    assert (
        client.post("/api/v2/auth/login", json={"username": "nguyen.van-a", "password": "MatKhau@2026"}).status_code
        == 200
    )


def test_create_duplicates(client, admin):
    _create(client, admin)
    err = _error(client.post(USERS, json={**NEW, "email": "khac@example.vn"}, headers=admin), 409, "DUPLICATE")
    assert err["details"][0]["field"] == "body.username"
    err = _error(client.post(USERS, json={**NEW, "username": "khac"}, headers=admin), 409, "DUPLICATE")
    assert err["details"][0]["field"] == "body.email"


@pytest.mark.parametrize(
    "override",
    [
        {"username": "ab"},
        {"username": "co dau cach"},
        {"username": "tiếng-việt"},
        {"email": "khong-phai-email"},
        {"password": "ngan"},
        {"password": "x" * 129},
        {"password": "nguyen.van-a"},  # trùng tên đăng nhập
        {"roles": []},
        {"roles": ["superuser"]},
        {"full_name": "   "},
    ],
)
def test_create_validation(client, admin, override):
    _error(client.post(USERS, json={**NEW, **override}, headers=admin), 422, "VALIDATION_ERROR")


# --- Đọc, danh sách ----------------------------------------------------------------------------------------


def test_get_user_and_404(client, admin):
    created = _create(client, admin)
    r = client.get(f"{USERS}/{created['id']}", headers=admin)
    assert r.status_code == 200 and r.headers["ETag"] == '"1"'
    _error(client.get(f"{USERS}/99999", headers=admin), 404, "NOT_FOUND")
    _error(client.get(f"{USERS}/abc", headers=admin), 404, "NOT_FOUND")


def test_list_pagination_filters_and_sort(client, admin, make_user):
    make_user("binh", roles=["learner"], full_name="Trần Bình")
    make_user("cuong", roles=["learner", "instructor"], full_name="Lê Cường")
    make_user("dung", roles=["manager"], is_active=False)

    page = client.get(USERS, params={"page_size": 2, "sort": "username"}, headers=admin).json()
    assert (page["total"], page["page"], page["page_size"]) == (3, 1, 2)  # mặc định status=active
    assert [u["username"] for u in page["items"]] == ["binh", "cuong"]
    page2 = client.get(USERS, params={"page_size": 2, "page": 2, "sort": "username"}, headers=admin).json()
    assert [u["username"] for u in page2["items"]] == ["quantri"]

    def names(**params):
        return [
            u["username"]
            for u in client.get(USERS, params={"sort": "username", **params}, headers=admin).json()["items"]
        ]

    assert names(sort="-username") == ["quantri", "cuong", "binh"]
    assert names(status="inactive") == ["dung"]
    assert names(status="all") == ["binh", "cuong", "dung", "quantri"]
    assert names(role="learner") == ["binh", "cuong"]
    assert names(q="CƯỜNG") == ["cuong"]
    assert names(q="%") == []  # ký tự đại diện LIKE được escape


@pytest.mark.parametrize(
    "params",
    [{"page_size": 101}, {"page": 0}, {"sort": "password_hash"}, {"q": ""}, {"role": "x"}, {"status": "archived"}],
)
def test_list_validation(client, admin, params):
    _error(client.get(USERS, params=params, headers=admin), 422, "VALIDATION_ERROR")


# --- Cập nhật ----------------------------------------------------------------------------------------------


def test_patch_with_if_match(client, admin):
    user = _create(client, admin)
    url = f"{USERS}/{user['id']}"
    r = client.patch(url, json={"full_name": "Nguyễn Văn Bê"}, headers={**admin, "If-Match": '"1"'})
    assert r.status_code == 200 and r.json()["full_name"] == "Nguyễn Văn Bê"
    assert r.json()["row_version"] == 2 and r.headers["ETag"] == '"2"'
    assert r.json()["roles"] == ["ops_staff"] and r.json()["email"] == "a@example.vn"  # trường không gửi giữ nguyên
    # ETag cũ → xung đột phiên bản
    _error(client.patch(url, json={"full_name": "Khác"}, headers={**admin, "If-Match": '"1"'}), 409, "VERSION_CONFLICT")
    _error(client.patch(url, json={"full_name": "Khác"}, headers={**admin, "If-Match": "abc"}), 422, "VALIDATION_ERROR")
    # Không gửi If-Match: vẫn cập nhật được
    assert client.patch(url, json={"email": None}, headers=admin).json()["email"] is None


def test_patch_roles_bumps_version(client, admin):
    user = _create(client, admin)
    r = client.patch(f"{USERS}/{user['id']}", json={"roles": ["learner", "manager"]}, headers=admin)
    assert r.json()["roles"] == ["learner", "manager"] and r.json()["row_version"] == 2


@pytest.mark.parametrize(
    "body",
    [{"full_name": None}, {"roles": None}, {"is_active": None}, {"password": None}, {"username": "doi"}, {"roles": []}],
)
def test_patch_validation(client, admin, body):
    user = _create(client, admin)
    _error(client.patch(f"{USERS}/{user['id']}", json=body, headers=admin), 422, "VALIDATION_ERROR")


def test_deactivate_revokes_sessions(client, admin, login):
    user = _create(client, admin)
    tokens = login("nguyen.van-a")
    r = client.patch(f"{USERS}/{user['id']}", json={"is_active": False}, headers=admin)
    assert r.json()["is_active"] is False
    _error(
        client.get("/api/v2/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}),
        401,
        "UNAUTHENTICATED",
    )
    _error(client.post("/api/v2/auth/refresh", json={"refresh_token": tokens["refresh_token"]}), 401, "UNAUTHENTICATED")
    with SessionLocal() as db:
        assert (
            db.scalar(select(SysRefreshToken.revoked_reason).where(SysRefreshToken.user_id == user["id"]))
            == "deactivated"
        )


def test_admin_password_reset(client, admin, login):
    user = _create(client, admin)
    tokens = login("nguyen.van-a")
    client.patch(f"{USERS}/{user['id']}", json={"password": "MatKhauMoi@2026"}, headers=admin)
    _error(
        client.get("/api/v2/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}),
        401,
        "UNAUTHENTICATED",
    )
    assert (
        client.post("/api/v2/auth/login", json={"username": "nguyen.van-a", "password": "MatKhau@2026"}).status_code
        == 401
    )
    assert login("nguyen.van-a", "MatKhauMoi@2026")["access_token"]


def test_admin_cannot_lock_themselves_out(client, admin):
    me = client.get("/api/v2/auth/me", headers=admin).json()
    _error(client.patch(f"{USERS}/{me['id']}", json={"is_active": False}, headers=admin), 403, "FORBIDDEN")
    _error(client.patch(f"{USERS}/{me['id']}", json={"roles": ["manager"]}, headers=admin), 403, "FORBIDDEN")
    assert client.patch(f"{USERS}/{me['id']}", json={"roles": ["admin", "manager"]}, headers=admin).status_code == 200


def test_unlock(client, admin):
    user = _create(client, admin)
    for _ in range(5):
        client.post("/api/v2/auth/login", json={"username": "nguyen.van-a", "password": "SaiMatKhau1"})
    assert client.get(f"{USERS}/{user['id']}", headers=admin).json()["locked_until"].endswith("Z")
    r = client.post(f"{USERS}/{user['id']}:unlock", headers=admin)
    assert r.status_code == 200 and r.json()["locked_until"] is None
    assert (
        client.post("/api/v2/auth/login", json={"username": "nguyen.van-a", "password": "MatKhau@2026"}).status_code
        == 200
    )
    _error(client.post(f"{USERS}/99999:unlock", headers=admin), 404, "NOT_FOUND")


# --- CLI ---------------------------------------------------------------------------------------------------


def test_cli_create_admin(client, monkeypatch, capsys, login):
    monkeypatch.setattr("sys.stdin", io.StringIO("MatKhauAdmin@2026\n"))
    assert cli.main(["create-admin", "--username", "Admin", "--full-name", "Quản trị", "--password-stdin"]) == 0
    assert "Đã tạo admin" in capsys.readouterr().out
    headers = {"Authorization": f"Bearer {login('admin', 'MatKhauAdmin@2026')['access_token']}"}
    assert client.get("/api/v2/auth/me", headers=headers).json()["roles"] == ["admin"]

    monkeypatch.setattr("sys.stdin", io.StringIO("MatKhauAdmin@2026\n"))
    assert cli.main(["create-admin", "--username", "admin", "--full-name", "Trùng", "--password-stdin"]) == 1
    monkeypatch.setattr("sys.stdin", io.StringIO("ngan\n"))
    assert cli.main(["create-admin", "--username", "admin2", "--full-name", "Yếu", "--password-stdin"]) == 2
    assert DEFAULT_PASSWORD not in capsys.readouterr().err
