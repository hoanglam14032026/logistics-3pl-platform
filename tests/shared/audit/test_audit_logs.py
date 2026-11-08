"""Nhật ký thao tác sys_audit_logs (06 §5) trên PostgreSQL: party, tài khoản, đăng nhập thất bại; tính bất biến."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.core.db import SessionLocal
from app.core.timeutil import now_utc
from app.shared.audit import service as audit
from app.shared.audit.models import SysAuditLog
from app.shared.identity.models import SysUser
from tests.conftest import DEFAULT_PASSWORD

PARTIES = "/api/v2/md/parties"
PARTY = {"party_type": "customer", "name": "Công ty Minh An", "tax_code": "0101234567"}


def _logs(action: str | None = None) -> list[SysAuditLog]:
    with SessionLocal() as db:
        stmt = select(SysAuditLog).order_by(SysAuditLog.id)
        if action:
            stmt = stmt.where(SysAuditLog.action == action)
        return list(db.scalars(stmt))


def _me(client, headers) -> int:
    return client.get("/api/v2/auth/me", headers=headers).json()["id"]


@pytest.fixture()
def staff(client, make_user, auth_headers):
    make_user("quanly", roles=["manager"])
    make_user("quantri", roles=["admin"])
    return {"manager": auth_headers("quanly"), "admin": auth_headers("quantri")}


# --- Party ---------------------------------------------------------------------------------------------------


def test_party_lifecycle_is_logged(client, staff):
    manager = staff["manager"]
    r = client.post(PARTIES, json=PARTY, headers=manager)
    pid, rid = r.json()["id"], r.headers["X-Request-ID"]
    client.patch(f"{PARTIES}/{pid}", json={"name": "Minh An JSC", "address": "Hà Nội"}, headers=manager)
    client.patch(f"{PARTIES}/{pid}", json={"name": "Minh An JSC"}, headers=manager)  # không đổi gì → không ghi
    client.post(f"{PARTIES}/{pid}:archive", headers=manager)
    client.post(f"{PARTIES}/{pid}:restore", headers=manager)

    logs = [log for log in _logs() if log.entity_type == "party"]
    assert [log.action for log in logs] == ["party.create", "party.update", "party.archive", "party.restore"]
    create, upd, arch, rest = logs
    actor = _me(client, manager)
    assert {log.actor_id for log in logs} == {actor} and {log.entity_id for log in logs} == {pid}
    assert (
        create.before is None and create.after["name"] == "Công ty Minh An" and create.after["tax_code"] == "0101234567"
    )
    assert (create.ip, create.request_id) == ("testclient", rid)
    assert upd.before == {"name": "Công ty Minh An", "address": None}
    assert upd.after == {"name": "Minh An JSC", "address": "Hà Nội"}
    assert (arch.before, arch.after) == ({"status": "active"}, {"status": "archived"})
    assert (rest.before, rest.after) == ({"status": "archived"}, {"status": "active"})
    assert all(log.at is not None for log in logs)


def test_failed_change_leaves_no_log(client, staff):
    client.post(PARTIES, json=PARTY, headers=staff["manager"])
    assert client.post(PARTIES, json=PARTY, headers=staff["manager"]).status_code == 409  # trùng MST
    assert len(_logs("party.create")) == 1  # nhật ký cùng giao dịch: lỗi thì không có dòng thừa


# --- Tài khoản --------------------------------------------------------------------------------------------------


def test_account_creation_and_deactivation_are_logged(client, staff, make_party, make_contract):
    party = make_party()
    make_contract(party.id)
    r = client.post(
        f"{PARTIES}/{party.id}/users",
        json={"username": "minhan.lan", "full_name": "Lan", "email": "lan@minhan.vn", "is_customer_admin": True},
        headers=staff["manager"],
    )
    uid = r.json()["id"]
    admin = staff["admin"]
    client.patch(f"/api/v2/users/{uid}", json={"full_name": "Nguyễn Thị Lan", "is_active": False}, headers=admin)
    client.patch(f"/api/v2/users/{uid}", json={"is_active": True, "password": "MatKhauMoi@2026"}, headers=admin)

    logs = [log for log in _logs() if log.entity_type == "user" and log.entity_id == uid]
    assert [log.action for log in logs] == [
        "user.create",
        "user.invite_sent",
        "user.deactivate",
        "user.update",
        "user.activate",
        "user.password_reset",
    ]
    create = logs[0]
    assert create.actor_id == _me(client, staff["manager"])
    assert create.after == {
        "username": "minhan.lan",
        "email": "lan@minhan.vn",
        "full_name": "Lan",
        "role_codes": ["customer"],
        "is_active": True,
        "party_id": party.id,
        "is_customer_admin": True,
    }
    assert logs[1].after == {"email": "lan@minhan.vn"}  # thư mời
    assert (logs[2].before, logs[2].after) == ({"is_active": True}, {"is_active": False})
    assert (logs[3].before, logs[3].after) == ({"full_name": "Lan"}, {"full_name": "Nguyễn Thị Lan"})
    assert logs[5].before is None and logs[5].after is None


def test_lock_unlock_and_password_change_are_logged(client, staff, make_user, auth_headers):
    user = make_user("hang")
    for _ in range(5):
        client.post("/api/v2/auth/login", json={"username": "hang", "password": "SaiMatKhau1"})
    lock = _logs("user.lock")
    assert len(lock) == 1 and lock[0].actor_id is None and lock[0].entity_id == user.id  # hệ thống tự khóa
    assert lock[0].after["failed_attempts"] == 5 and lock[0].after["locked_until"].endswith("Z")

    client.post(f"/api/v2/users/{user.id}:unlock", headers=staff["admin"])
    unlock = _logs("user.unlock")[0]
    assert unlock.after == {"locked_until": None, "failed_login_count": 0}
    assert unlock.before["locked_until"] is not None

    headers = auth_headers("hang")
    body = {"current_password": DEFAULT_PASSWORD, "new_password": "MatKhauMoi@2026"}
    client.post("/api/v2/auth/change-password", json=body, headers=headers)
    assert _logs("user.password_change")[0].actor_id == user.id


# --- Đăng nhập thất bại -------------------------------------------------------------------------------------------


def test_failed_logins_are_logged_with_reason(client, make_user):
    make_user("anh")
    make_user("dung", is_active=False)
    client.post("/api/v2/auth/login", json={"username": "khong-co", "password": "SaiMatKhau1"})
    client.post("/api/v2/auth/login", json={"username": "ANH", "password": "SaiMatKhau1"})
    client.post("/api/v2/auth/login", json={"username": "dung", "password": DEFAULT_PASSWORD})
    with SessionLocal() as db:
        db.execute(
            update(SysUser).where(SysUser.username == "anh").values(locked_until=now_utc() + timedelta(minutes=5))
        )
        db.commit()
    client.post("/api/v2/auth/login", json={"username": "anh", "password": DEFAULT_PASSWORD})
    client.post("/api/v2/auth/login", json={"username": "anh", "password": DEFAULT_PASSWORD})

    logs = _logs("auth.login_failed")
    assert [(log.after["username"], log.after["reason"]) for log in logs] == [
        ("khong-co", "unknown_user"),
        ("anh", "invalid_password"),
        ("dung", "disabled"),
        ("anh", "locked"),
        ("anh", "locked"),
    ]
    assert logs[0].entity_id is None and logs[1].entity_id is not None
    assert all(log.actor_id is None and log.ip == "testclient" and log.request_id for log in logs)
    assert _logs("auth.login_succeeded") == []  # đăng nhập thành công không ghi (chưa yêu cầu)


def test_refresh_token_reuse_is_logged(client, make_user, login):
    user = make_user("son")
    first = login("son")
    client.post("/api/v2/auth/refresh", json={"refresh_token": first["refresh_token"]})
    client.post("/api/v2/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert [log.entity_id for log in _logs("auth.refresh_reuse_detected")] == [user.id]


# --- An toàn của nhật ký -------------------------------------------------------------------------------------------


def test_no_secrets_in_logs(client, staff, make_user, login):
    make_user("bi.mat", password="BiMat@123456")
    client.post("/api/v2/auth/login", json={"username": "bi.mat", "password": "SaiBiMat@999"})
    tokens = login("bi.mat", "BiMat@123456")
    client.patch(
        f"/api/v2/users/{_me(client, {'Authorization': 'Bearer ' + tokens['access_token']})}",
        json={"password": "MoiBiMat@777"},
        headers=staff["admin"],
    )
    with SessionLocal() as db:
        dump = json.dumps([[r.before, r.after] for r in db.scalars(select(SysAuditLog))], ensure_ascii=False)
        hashes = db.scalars(select(SysUser.password_hash)).all()
    for secret in [
        "BiMat@123456",
        "SaiBiMat@999",
        "MoiBiMat@777",
        tokens["access_token"],
        tokens["refresh_token"],
        *hashes,
    ]:
        assert secret not in dump
    with SessionLocal() as db:
        keys = {k for r in db.scalars(select(SysAuditLog)) for part in (r.before, r.after) if part for k in part}
    assert not {k for k in keys if any(w in k for w in ("password", "hash", "token", "totp"))}


def test_record_rejects_sensitive_fields(client):
    with SessionLocal() as db, pytest.raises(ValueError, match="password_hash"):
        audit.record(db, "x", "user", 1, None, after={"password_hash": "..."})


def test_audit_logs_are_append_only(client, staff):
    client.post(PARTIES, json=PARTY, headers=staff["manager"])
    for sql in ("UPDATE sys_audit_logs SET action = 'sua'", "DELETE FROM sys_audit_logs"):
        with SessionLocal() as db, pytest.raises(DBAPIError, match="chỉ được thêm"):
            db.execute(text(sql))
            db.commit()
    assert [log.action for log in _logs()][-1] == "party.create"
