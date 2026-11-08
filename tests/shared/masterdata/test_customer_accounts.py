"""Tài khoản `customer` gắn khách hàng (02 §9.2; 06 §4 – manager tạo tài khoản customer), trên PostgreSQL."""

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal
from app.shared.identity.models import SysUser
from app.shared.masterdata.models import MdParty

PARTIES = "/api/v2/md/parties"
ACCOUNT = {
    "username": "minhan.lan",
    "email": "lan@minhan.vn",
    "full_name": "Nguyễn Thị Lan",
}
PASSWORD = "MatKhau@2026"


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


@pytest.fixture()
def staff(client, make_user, auth_headers):
    make_user("quanly", roles=["manager"])
    make_user("quantri", roles=["admin"])
    make_user("nhanvien", roles=["ops_staff"])
    return {"manager": auth_headers("quanly"), "admin": auth_headers("quantri"), "ops_staff": auth_headers("nhanvien")}


@pytest.fixture()
def customer_party(make_party, make_contract):
    """Khách hàng có hợp đồng đang hiệu lực, portal_enabled – manager tạo được tài khoản customer."""
    party = make_party("Công ty TNHH Minh An", tax_code="0101234567")
    make_contract(party.id)
    return party


def _admin_account(client, staff, party_id, username="minhan.lan"):
    """Admin tạo tài khoản customer qua /api/v2/users (không cần hợp đồng – 06 §4: admin toàn quyền người dùng)."""
    body = {**ACCOUNT, "username": username, "password": PASSWORD, "roles": ["customer"], "party_id": party_id}
    r = client.post("/api/v2/users", json=body, headers=staff["admin"])
    assert r.status_code == 201, r.text
    return r.json()


def _create_account(client, headers, pid, /, **override):
    return client.post(f"{PARTIES}/{pid}/users", json={**ACCOUNT, **override}, headers=headers)


# --- Manager tạo tài khoản customer --------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["manager", "admin"])
def test_manager_creates_customer_account(client, staff, customer_party, login, accept_invite, who):
    r = _create_account(client, staff[who], customer_party.id, is_customer_admin=True)
    assert r.status_code == 201, r.text
    body = r.json()
    assert r.headers["Location"] == f"/api/v2/users/{body['id']}"
    assert (body["roles"], body["party_id"], body["is_customer_admin"]) == (["customer"], customer_party.id, True)

    assert body["invitation_sent"] is True
    # Nhận lời mời (đặt mật khẩu) → đăng nhập (thiết lập 2FA bắt buộc) → thấy khách hàng của mình; không truy cập
    # danh mục nội bộ, quản lý người dùng
    accept_invite("lan@minhan.vn")
    headers = {"Authorization": f"Bearer {login('minhan.lan')['access_token']}"}
    me = client.get("/api/v2/auth/me", headers=headers).json()
    assert (me["party_id"], me["is_customer_admin"]) == (customer_party.id, True)
    _error(client.get(PARTIES, headers=headers), 403, "FORBIDDEN")
    _error(client.get("/api/v2/users", headers=headers), 403, "FORBIDDEN")
    _error(_create_account(client, headers, customer_party.id, username="khac"), 403, "FORBIDDEN")


def test_default_not_customer_admin(client, staff, customer_party):
    assert _create_account(client, staff["manager"], customer_party.id).json()["is_customer_admin"] is False


def test_manager_cannot_choose_roles(client, staff, customer_party):
    # Body không nhận roles/party_id: manager không tự cấp vai trò nội bộ qua endpoint này
    for extra in ({"roles": ["admin"]}, {"party_id": 999}, {"is_active": False}):
        _error(_create_account(client, staff["manager"], customer_party.id, **extra), 422, "VALIDATION_ERROR")
    # …và vẫn không dùng được /api/v2/users (chỉ admin)
    body = {**ACCOUNT, "roles": ["customer"], "party_id": customer_party.id}
    _error(client.post("/api/v2/users", json=body, headers=staff["manager"]), 403, "FORBIDDEN")


def test_other_roles_cannot_create_accounts(client, staff, customer_party):
    _error(_create_account(client, staff["ops_staff"], customer_party.id), 403, "FORBIDDEN")
    _error(client.get(f"{PARTIES}/{customer_party.id}/users", headers=staff["ops_staff"]), 403, "FORBIDDEN")


def test_party_must_be_active_customer(client, staff, make_party):
    agent = make_party("Đại lý Sài Gòn", party_type="agent")
    err = _error(_create_account(client, staff["manager"], agent.id), 422, "VALIDATION_ERROR")
    assert err["details"][0]["field"] == "body.party_id"

    archived = make_party("Khách cũ")
    client.post(f"{PARTIES}/{archived.id}:archive", headers=staff["manager"])
    assert _error(_create_account(client, staff["manager"], archived.id), 422, "VALIDATION_ERROR")["message"] == (
        "Khách hàng đã lưu trữ"
    )
    _error(_create_account(client, staff["manager"], 99999), 404, "NOT_FOUND")


def test_many_accounts_per_customer_and_listing(client, staff, customer_party, make_party, make_contract):
    other = make_party("Khách khác", tax_code="0109999999")
    make_contract(other.id)
    _create_account(client, staff["manager"], customer_party.id, is_customer_admin=True)
    _create_account(client, staff["manager"], customer_party.id, username="minhan.hung", email="hung@minhan.vn")
    _create_account(client, staff["manager"], other.id, username="khac.mot", email="mot@khac.vn")
    _error(_create_account(client, staff["manager"], customer_party.id), 409, "DUPLICATE")

    page = client.get(
        f"{PARTIES}/{customer_party.id}/users", params={"sort": "username"}, headers=staff["manager"]
    ).json()
    assert page["total"] == 2
    assert [u["username"] for u in page["items"]] == ["minhan.hung", "minhan.lan"]
    assert {u["party_id"] for u in page["items"]} == {customer_party.id}


# --- Quy tắc khi admin dùng /api/v2/users ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"roles": ["customer"]}, "party_id"),  # customer phải gắn khách hàng
        ({"roles": ["customer", "ops_staff"], "with_party": True}, "roles"),  # không kèm vai trò nội bộ
        ({"roles": ["ops_staff"], "with_party": True}, "party_id"),  # tài khoản nội bộ không gắn khách hàng
        ({"roles": ["ops_staff"], "is_customer_admin": True}, "is_customer_admin"),
    ],
)
def test_admin_create_rules(client, staff, customer_party, override, field):
    body = {**ACCOUNT, "password": PASSWORD, **override}
    if body.pop("with_party", False):
        body["party_id"] = customer_party.id
    err = _error(client.post("/api/v2/users", json=body, headers=staff["admin"]), 422, "VALIDATION_ERROR")
    assert err["details"][0]["field"] == f"body.{field}"


def test_admin_creates_customer_via_users(client, staff, customer_party):
    body = {
        **ACCOUNT,
        "password": PASSWORD,
        "roles": ["customer"],
        "party_id": customer_party.id,
        "is_customer_admin": True,
    }
    r = client.post("/api/v2/users", json=body, headers=staff["admin"])
    assert r.status_code == 201 and r.json()["party_id"] == customer_party.id


def test_patch_rules(client, staff, customer_party):
    customer = _create_account(client, staff["manager"], customer_party.id).json()
    internal = client.get("/api/v2/auth/me", headers=staff["ops_staff"]).json()
    admin = staff["admin"]

    r = client.patch(f"/api/v2/users/{customer['id']}", json={"is_customer_admin": True}, headers=admin)
    assert r.status_code == 200 and r.json()["is_customer_admin"] is True
    for user, body in [
        (customer, {"roles": ["ops_staff"]}),  # customer → nội bộ
        (customer, {"roles": ["customer", "manager"]}),
        (internal, {"roles": ["customer"]}),  # nội bộ → customer
        (internal, {"is_customer_admin": True}),
        (customer, {"is_customer_admin": None}),
    ]:
        _error(client.patch(f"/api/v2/users/{user['id']}", json=body, headers=admin), 422, "VALIDATION_ERROR")


# --- Lưu trữ, đổi loại khách hàng có tài khoản ---------------------------------------------------------------


def test_cannot_archive_customer_with_active_accounts(client, staff, make_party):
    party = make_party("Không hợp đồng", tax_code="0107777777")  # chỉ đo quy tắc tài khoản, không vướng hợp đồng
    account = _admin_account(client, staff, party.id)
    url = f"{PARTIES}/{party.id}"
    _error(client.post(f"{url}:archive", headers=staff["manager"]), 409, "INVALID_STATE_TRANSITION")
    client.patch(f"/api/v2/users/{account['id']}", json={"is_active": False}, headers=staff["admin"])
    assert client.post(f"{url}:archive", headers=staff["manager"]).status_code == 200


def test_cannot_change_type_of_customer_with_accounts(client, staff, customer_party, make_party):
    _create_account(client, staff["manager"], customer_party.id)
    _error(
        client.patch(f"{PARTIES}/{customer_party.id}", json={"party_type": "agent"}, headers=staff["manager"]),
        409,
        "INVALID_STATE_TRANSITION",
    )
    lonely = make_party("Chưa có tài khoản", tax_code="0108888888")
    r = client.patch(f"{PARTIES}/{lonely.id}", json={"party_type": "agent"}, headers=staff["manager"])
    assert r.status_code == 200 and r.json()["party_type"] == "agent"


# --- Ràng buộc ở CSDL --------------------------------------------------------------------------------------------


def test_database_constraints(client, make_user, make_party, staff):
    user = make_user("noibo")
    with SessionLocal() as db, pytest.raises(IntegrityError, match="ck_sys_users_customer_admin_has_party"):
        db.execute(update(SysUser).where(SysUser.id == user.id).values(is_customer_admin=True))
        db.commit()
    party = make_party("Không hợp đồng", tax_code="0107777777")
    _admin_account(client, staff, party.id)
    # Khóa ngoại RESTRICT: không xóa được khách hàng còn tài khoản
    with SessionLocal() as db, pytest.raises(IntegrityError, match="fk_sys_users_party_id_md_parties"):
        db.execute(delete(MdParty).where(MdParty.id == party.id))
        db.commit()


def test_archive_and_account_creation_do_not_race(client, staff, customer_party):
    """Tạo tài khoản (FOR SHARE) và lưu trữ (FOR UPDATE) song song: không có tài khoản hoạt động gắn party
    đã lưu trữ."""
    from concurrent.futures import ThreadPoolExecutor

    from app.core.errors import AppError
    from app.shared.identity.schemas import CustomerAccountCreate
    from app.shared.masterdata import service

    def create(i):
        with SessionLocal() as db:
            try:
                data = CustomerAccountCreate(**{**ACCOUNT, "username": f"song.song{i}", "email": f"ss{i}@minhan.vn"})
                service.create_customer_account(db, customer_party.id, data, actor_id=1)
            except AppError:
                pass

    def archive(_):
        with SessionLocal() as db:
            try:
                service.archive_party(db, customer_party.id, actor_id=1, expected_version=None)
            except AppError:
                pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: (archive if i == 3 else create)(i), range(8)))
    with SessionLocal() as db:
        party = db.get(MdParty, customer_party.id)
        active = db.query(SysUser).filter(SysUser.party_id == party.id, SysUser.is_active.is_(True)).count()
    assert not (party.status == "archived" and active > 0)
