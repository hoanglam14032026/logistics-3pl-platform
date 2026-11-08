"""Customer admin quản lý tài khoản cùng khách hàng: /api/v2/portal/users (02 §9.2; 06 §4), trên PostgreSQL."""

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.shared.audit.models import SysAuditLog
from app.shared.identity import service
from app.shared.identity.schemas import UserCreate

PORTAL = "/api/v2/portal/users"
INVITE = {"username": "minhan.hung", "full_name": "Trần Hùng", "email": "hung@minhan.vn"}


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


def _customer(party_id: int, username: str, is_admin: bool = False, **fields):
    data = UserCreate(
        username=username,
        full_name=username,
        password="MatKhau@2026",
        roles=["customer"],
        party_id=party_id,
        is_customer_admin=is_admin,
        **fields,
    )
    with SessionLocal() as db:
        return service.create_user(db, data, actor_id=None)


@pytest.fixture()
def world(client, make_party, make_contract, make_user, auth_headers):
    """Hai khách hàng A, B (có hợp đồng dùng cổng); mỗi bên một customer admin + một tài khoản thường; một nhân viên."""
    a, b = make_party("Khách A", tax_code="0100000001"), make_party("Khách B", tax_code="0100000002")
    make_contract(a.id)
    make_contract(b.id)
    users = {
        "a_admin": _customer(a.id, "a.admin", True),
        "a_user": _customer(a.id, "a.user"),
        "a_user2": _customer(a.id, "a.user2"),
        "b_admin": _customer(b.id, "b.admin", True),
        "b_user": _customer(b.id, "b.user"),
        "staff": make_user("nhan.vien", roles=["ops_staff"]),
        "admin": make_user("quan.tri", roles=["admin"]),
    }
    return {"a": a, "b": b, "u": users, "h": lambda key: auth_headers(users[key].username)}


# --- Quyền vào cổng ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["a_user", "staff", "admin"])
def test_only_customer_admin(client, world, who):
    headers = world["h"](who)
    _error(client.get(PORTAL, headers=headers), 403, "FORBIDDEN")
    _error(client.post(PORTAL, json=INVITE, headers=headers), 403, "FORBIDDEN")
    _error(client.post(f"{PORTAL}/{world['u']['a_user2'].id}:deactivate", headers=headers), 403, "FORBIDDEN")


def test_anonymous(client):
    _error(client.get(PORTAL), 401, "UNAUTHENTICATED")


# --- Xem, mời ---------------------------------------------------------------------------------------------------


def test_list_only_own_party(client, world):
    page = client.get(PORTAL, params={"sort": "username"}, headers=world["h"]("a_admin")).json()
    assert [u["username"] for u in page["items"]] == ["a.admin", "a.user", "a.user2"]
    assert page["total"] == 3 and {u["party_id"] for u in page["items"]} == {world["a"].id}


def test_invite_account_in_own_party(client, world, login, accept_invite):
    r = client.post(PORTAL, json=INVITE, headers=world["h"]("a_admin"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert r.headers["Location"] == f"{PORTAL}/{body['id']}"
    assert (body["roles"], body["party_id"], body["is_customer_admin"]) == (["customer"], world["a"].id, False)
    assert "invitation_sent" not in body  # thư gửi nền (Giai đoạn 6)
    accept_invite("hung@minhan.vn")
    assert login("minhan.hung")["access_token"]
    with SessionLocal() as db:
        log = db.scalar(
            select(SysAuditLog).where(SysAuditLog.action == "user.create", SysAuditLog.entity_id == body["id"])
        )
    assert log.actor_id == world["u"]["a_admin"].id


@pytest.mark.parametrize(
    "extra",
    [
        {"is_customer_admin": True},
        {"roles": ["admin"]},
        {"party_id": 2},
        {"roles": ["ops_staff"]},
        {"is_active": False},
    ],
)
def test_invite_cannot_escalate_or_change_party(client, world, extra):
    _error(client.post(PORTAL, json={**INVITE, **extra}, headers=world["h"]("a_admin")), 422, "VALIDATION_ERROR")


def test_invite_duplicate_and_validation(client, world):
    _error(client.post(PORTAL, json={**INVITE, "username": "b.user"}, headers=world["h"]("a_admin")), 409, "DUPLICATE")
    _error(
        client.post(PORTAL, json={**INVITE, "email": "khong-phai-email"}, headers=world["h"]("a_admin")),
        422,
        "VALIDATION_ERROR",
    )


# --- Khóa tài khoản ---------------------------------------------------------------------------------------------


def test_deactivate_other_account_in_own_party(client, world, login):
    target = world["u"]["a_user"]
    tokens = login("a.user")
    r = client.post(f"{PORTAL}/{target.id}:deactivate", headers=world["h"]("a_admin"))
    assert r.status_code == 200 and r.json()["is_active"] is False
    # Tài khoản bị khóa mất phiên ngay
    _error(
        client.get("/api/v2/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}),
        401,
        "UNAUTHENTICATED",
    )
    _error(client.post("/api/v2/auth/refresh", json={"refresh_token": tokens["refresh_token"]}), 401, "UNAUTHENTICATED")
    _error(client.post("/api/v2/auth/login", json={"username": "a.user", "password": "MatKhau@2026"}), 403, "FORBIDDEN")
    # Gọi lại: không lỗi, không ghi nhật ký thêm
    assert client.post(f"{PORTAL}/{target.id}:deactivate", headers=world["h"]("a_admin")).status_code == 200
    with SessionLocal() as db:
        logs = db.scalars(select(SysAuditLog).where(SysAuditLog.action == "user.deactivate")).all()
    assert [(log.entity_id, log.actor_id) for log in logs] == [(target.id, world["u"]["a_admin"].id)]


def test_cannot_deactivate_self(client, world):
    me = world["u"]["a_admin"]
    _error(client.post(f"{PORTAL}/{me.id}:deactivate", headers=world["h"]("a_admin")), 403, "FORBIDDEN")


@pytest.mark.parametrize("target", ["b_user", "b_admin", "staff", "admin"])
def test_cannot_touch_other_party_or_internal_accounts(client, world, target):
    user = world["u"][target]
    # 404: không lộ sự tồn tại của tài khoản ngoài phạm vi
    _error(client.post(f"{PORTAL}/{user.id}:deactivate", headers=world["h"]("a_admin")), 404, "NOT_FOUND")
    with SessionLocal() as db:
        assert db.get(type(user), user.id).is_active is True


def test_unknown_account_404(client, world):
    _error(client.post(f"{PORTAL}/99999:deactivate", headers=world["h"]("a_admin")), 404, "NOT_FOUND")


def test_customer_admin_cannot_use_internal_apis(client, world):
    headers = world["h"]("a_admin")
    _error(client.get("/api/v2/users", headers=headers), 403, "FORBIDDEN")
    _error(
        client.patch(f"/api/v2/users/{world['u']['a_user'].id}", json={"is_active": False}, headers=headers),
        403,
        "FORBIDDEN",
    )
    _error(client.post(f"/api/v2/md/parties/{world['a'].id}/users", json=INVITE, headers=headers), 403, "FORBIDDEN")
    _error(client.get("/api/v2/md/parties", headers=headers), 403, "FORBIDDEN")


def test_revoked_customer_admin_flag_takes_effect_immediately(client, world):
    headers = world["h"]("a_admin")
    assert client.get(PORTAL, headers=headers).status_code == 200
    client.patch(
        f"/api/v2/users/{world['u']['a_admin'].id}", json={"is_customer_admin": False}, headers=world["h"]("admin")
    )
    _error(client.get(PORTAL, headers=headers), 403, "FORBIDDEN")
