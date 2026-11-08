"""Test tích hợp /api/v2/md/parties trên PostgreSQL (01 §4; 06 §2.1–2.5, §4)."""

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.shared.masterdata.models import MdParty

PARTIES = "/api/v2/md/parties"
NEW = {
    "party_type": "customer",
    "name": "  Công ty TNHH Xuất nhập khẩu Minh An ",
    "tax_code": " 0101234567 ",
    "country_code": "vn",
    "address": "12 Trần Hưng Đạo, Hà Nội",
    "contact_name": "Nguyễn Thị Lan",
    "phone": "024 3826 1234",
    "email": "XNK@MinhAn.VN",
}
INTERNAL = ["ops_staff", "manager", "business_manager", "learner", "instructor", "content_editor", "admin"]
WRITERS = ["manager", "admin"]
READ_ONLY = [r for r in INTERNAL if r not in WRITERS]


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


@pytest.fixture()
def as_role(client, make_user, make_party, auth_headers):
    """as_role("manager") → header đăng nhập của một người dùng có vai trò đó (customer: gắn một khách hàng)."""
    created = {}

    def _headers(role: str) -> dict[str, str]:
        if role not in created:
            extra = {"party_id": make_party(name=f"KH của {role}").id} if role == "customer" else {}
            make_user(f"u.{role}", roles=[role], **extra)
            created[role] = auth_headers(f"u.{role}")
        return created[role]

    return _headers


@pytest.fixture()
def manager(as_role):
    return as_role("manager")


def _create(client, headers, **override) -> dict:
    r = client.post(PARTIES, json={**NEW, **override}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- Quyền (06 §4: md_* – nội bộ đọc; manager, admin ghi; customer không có quyền) ---------------------------


@pytest.mark.parametrize("role", INTERNAL)
def test_internal_roles_can_read(client, as_role, manager, role):
    party = _create(client, manager)
    headers = as_role(role)
    assert client.get(PARTIES, headers=headers).status_code == 200
    assert client.get(f"{PARTIES}/{party['id']}", headers=headers).status_code == 200


@pytest.mark.parametrize("role", WRITERS)
def test_manager_and_admin_can_write(client, as_role, role):
    headers = as_role(role)
    party = _create(client, headers)
    url = f"{PARTIES}/{party['id']}"
    assert client.patch(url, json={"note": "Khách quen"}, headers=headers).status_code == 200
    assert client.post(f"{url}:archive", headers=headers).status_code == 200
    assert client.post(f"{url}:restore", headers=headers).status_code == 200


@pytest.mark.parametrize("role", READ_ONLY)
def test_read_only_roles_cannot_write(client, as_role, manager, role):
    party = _create(client, manager)
    headers, url = as_role(role), f"{PARTIES}/{party['id']}"
    _error(client.post(PARTIES, json=NEW, headers=headers), 403, "FORBIDDEN")
    _error(client.patch(url, json={"note": "x"}, headers=headers), 403, "FORBIDDEN")
    _error(client.post(f"{url}:archive", headers=headers), 403, "FORBIDDEN")
    _error(client.post(f"{url}:restore", headers=headers), 403, "FORBIDDEN")


def test_customer_has_no_access(client, as_role, manager):
    party = _create(client, manager)
    headers = as_role("customer")
    _error(client.get(PARTIES, headers=headers), 403, "FORBIDDEN")
    _error(client.get(f"{PARTIES}/{party['id']}", headers=headers), 403, "FORBIDDEN")
    _error(client.post(PARTIES, json=NEW, headers=headers), 403, "FORBIDDEN")


def test_anonymous_gets_401(client):
    _error(client.get(PARTIES), 401, "UNAUTHENTICATED")
    _error(client.post(PARTIES, json=NEW), 401, "UNAUTHENTICATED")


# --- Tạo ---------------------------------------------------------------------------------------------------


def test_create_party_normalizes_fields(client, manager):
    r = client.post(PARTIES, json=NEW, headers=manager)
    assert r.status_code == 201
    body = r.json()
    assert r.headers["Location"] == f"{PARTIES}/{body['id']}" and r.headers["ETag"] == '"1"'
    assert body["name"] == "Công ty TNHH Xuất nhập khẩu Minh An"
    assert (body["tax_code"], body["country_code"], body["email"]) == ("0101234567", "VN", "xnk@minhan.vn")
    assert (body["status"], body["archived_at"], body["row_version"]) == ("active", None, 1)
    assert body["created_at"].endswith("Z")
    me = client.get("/api/v2/auth/me", headers=manager).json()
    with SessionLocal() as db:
        party = db.get(MdParty, body["id"])
        assert party.created_by == party.updated_by == me["id"]


def test_optional_fields_and_foreign_party(client, manager):
    body = _create(
        client,
        manager,
        party_type="shipping_line",
        name="Evergreen Marine",
        tax_code=None,
        country_code="TW",
        address="  ",
        phone=None,
        email="",
        contact_name=None,
    )
    assert (body["tax_code"], body["address"], body["email"], body["country_code"]) == (None, None, None, "TW")
    # MST nước ngoài không theo định dạng Việt Nam
    assert (
        _create(
            client, manager, party_type="foreign_seller", name="ABC Ltd", tax_code="sg-201912345k", country_code="SG"
        )["tax_code"]
        == "SG-201912345K"
    )
    # Chi nhánh: MST 13 số
    assert _create(client, manager, name="Chi nhánh HCM", tax_code="0101234567-001")["tax_code"] == "0101234567-001"


def test_duplicate_tax_code(client, manager):
    first = _create(client, manager)
    err = _error(client.post(PARTIES, json={**NEW, "name": "Tên khác"}, headers=manager), 409, "DUPLICATE")
    assert err["details"][0]["field"] == "body.tax_code"
    # Kể cả khi bản cũ đã lưu trữ: khôi phục bản cũ thay vì tạo trùng
    client.post(f"{PARTIES}/{first['id']}:archive", headers=manager)
    _error(client.post(PARTIES, json=NEW, headers=manager), 409, "DUPLICATE")
    # Không có MST: không tính trùng
    _create(client, manager, tax_code=None)
    _create(client, manager, tax_code=None)


@pytest.mark.parametrize(
    "override",
    [
        {"party_type": "supplier"},
        {"party_type": None},
        {"name": "   "},
        {"name": "x" * 301},
        {"tax_code": "12345"},  # VN: 10 hoặc 13 số
        {"tax_code": "0101234567001"},
        {"tax_code": "ab cd", "country_code": "SG"},
        {"country_code": "VNM"},
        {"phone": "goi ngay"},
        {"email": "khong-phai-email"},
        {"address": "x" * 501},
    ],
)
def test_create_validation(client, manager, override):
    _error(client.post(PARTIES, json={**NEW, **override}, headers=manager), 422, "VALIDATION_ERROR")


# --- Đọc, danh sách ----------------------------------------------------------------------------------------


def test_get_404(client, manager):
    _error(client.get(f"{PARTIES}/99999", headers=manager), 404, "NOT_FOUND")


def test_list_filters_and_sort(client, manager):
    a = _create(client, manager, name="Bình Minh Logistics", tax_code="0300000001")
    _create(client, manager, name="An Phát", tax_code="0300000002")
    _create(client, manager, party_type="shipping_line", name="Maersk", tax_code=None, country_code="DK")
    client.post(f"{PARTIES}/{a['id']}:archive", headers=manager)

    def names(**params):
        r = client.get(PARTIES, params={"sort": "name", **params}, headers=manager)
        assert r.status_code == 200, r.text
        return [p["name"] for p in r.json()["items"]]

    assert names() == ["An Phát", "Maersk"]  # mặc định status=active
    assert names(status="archived") == ["Bình Minh Logistics"]
    assert names(status="all") == ["An Phát", "Bình Minh Logistics", "Maersk"]
    assert names(sort="-name", status="all") == ["Maersk", "Bình Minh Logistics", "An Phát"]
    assert names(party_type="shipping_line") == ["Maersk"]
    assert names(country_code="dk") == ["Maersk"]
    assert names(q="phát") == ["An Phát"]
    assert names(q="0300000002") == ["An Phát"]
    page = client.get(PARTIES, params={"page_size": 1, "status": "all"}, headers=manager).json()
    assert (page["total"], len(page["items"]), page["page_size"]) == (3, 1, 1)


@pytest.mark.parametrize(
    "params",
    [
        {"page_size": 101},
        {"sort": "note"},
        {"q": ""},
        {"party_type": "x"},
        {"status": "deleted"},
        {"country_code": "V"},
    ],
)
def test_list_validation(client, manager, params):
    _error(client.get(PARTIES, params=params, headers=manager), 422, "VALIDATION_ERROR")


# --- Cập nhật ----------------------------------------------------------------------------------------------


def test_patch_with_if_match(client, manager):
    party = _create(client, manager)
    url = f"{PARTIES}/{party['id']}"
    r = client.patch(url, json={"name": "Minh An JSC", "address": None}, headers={**manager, "If-Match": '"1"'})
    assert r.status_code == 200
    body = r.json()
    assert (body["name"], body["address"], body["row_version"]) == ("Minh An JSC", None, 2)
    assert body["tax_code"] == "0101234567" and r.headers["ETag"] == '"2"'  # trường không gửi giữ nguyên
    _error(client.patch(url, json={"name": "X"}, headers={**manager, "If-Match": '"1"'}), 409, "VERSION_CONFLICT")


@pytest.mark.parametrize(
    "body",
    [
        {"name": None},
        {"party_type": None},
        {"country_code": None},
        {"status": "archived"},
        {"id": 5},
        {"tax_code": "123"},
    ],
)
def test_patch_validation(client, manager, body):
    party = _create(client, manager)
    _error(client.patch(f"{PARTIES}/{party['id']}", json=body, headers=manager), 422, "VALIDATION_ERROR")


def test_patch_tax_code_checked_against_country(client, manager):
    party = _create(client, manager, party_type="agent", name="Agent SG", tax_code="201912345K", country_code="SG")
    # Đổi sang VN mà MST cũ không đúng định dạng VN → 422
    err = _error(
        client.patch(f"{PARTIES}/{party['id']}", json={"country_code": "VN"}, headers=manager), 422, "VALIDATION_ERROR"
    )
    assert err["details"][0]["field"] == "body.tax_code"
    r = client.patch(f"{PARTIES}/{party['id']}", json={"country_code": "VN", "tax_code": "0109999999"}, headers=manager)
    assert r.status_code == 200


def test_patch_duplicate_tax_code(client, manager):
    _create(client, manager)
    other = _create(client, manager, name="Khác", tax_code="0109999999")
    _error(client.patch(f"{PARTIES}/{other['id']}", json={"tax_code": "0101234567"}, headers=manager), 409, "DUPLICATE")


# --- Lưu trữ, khôi phục --------------------------------------------------------------------------------------


def test_archive_and_restore(client, manager):
    party = _create(client, manager)
    url = f"{PARTIES}/{party['id']}"
    _error(client.post(f"{url}:archive", headers={**manager, "If-Match": '"9"'}), 409, "VERSION_CONFLICT")
    r = client.post(f"{url}:archive", headers={**manager, "If-Match": '"1"'})
    assert r.status_code == 200
    assert r.json()["status"] == "archived" and r.json()["archived_at"].endswith("Z")
    _error(client.post(f"{url}:archive", headers=manager), 409, "INVALID_STATE_TRANSITION")
    _error(client.patch(url, json={"note": "x"}, headers=manager), 409, "INVALID_STATE_TRANSITION")
    assert client.get(url, headers=manager).json()["status"] == "archived"  # vẫn đọc được

    r = client.post(f"{url}:restore", headers=manager)
    assert r.json()["status"] == "active" and r.json()["archived_at"] is None
    _error(client.post(f"{url}:restore", headers=manager), 409, "INVALID_STATE_TRANSITION")
    _error(client.post(f"{PARTIES}/99999:archive", headers=manager), 404, "NOT_FOUND")
    with SessionLocal() as db:
        assert db.scalar(select(MdParty.archived_by).where(MdParty.id == party["id"])) is None


def test_no_hard_delete(client, manager):
    party = _create(client, manager)
    assert client.delete(f"{PARTIES}/{party['id']}", headers=manager).status_code == 405
