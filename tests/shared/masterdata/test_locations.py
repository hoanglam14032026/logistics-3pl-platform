"""Danh mục địa điểm `/api/v2/md/locations` (Giai đoạn 16) – PostgreSQL."""

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.shared.audit.models import SysAuditLog

URL = "/api/v2/md/locations"
PORT = {
    "code": "vnhph",
    "name": "Cảng Hải Phòng",
    "location_type": "seaport",
    "address": "Số 8A Trần Phú, Ngô Quyền, Hải Phòng",
    "latitude": "20.865139",
    "longitude": "106.683830",
}


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


@pytest.fixture()
def world(client, make_party, make_user, auth_headers):
    a = make_party("Công ty Minh An", tax_code="0100000001")
    b = make_party("Công ty Bình Minh", tax_code="0100000002")
    users = {r: make_user(f"u.{r}", roles=[r]) for r in ("admin", "manager", "ops_staff", "business_manager")}
    users["kh_a"] = make_user("kh.a", roles=["customer"], party_id=a.id)
    users["kh_b"] = make_user("kh.b", roles=["customer"], party_id=b.id)
    return {"a": a, "b": b, "u": users, "h": {k: auth_headers(u.username) for k, u in users.items()}}


def _create(client, h, **overrides) -> dict:
    r = client.post(URL, json={**PORT, **overrides}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_by_admin_and_manager_only(client, world):
    h = world["h"]
    port = _create(client, h["admin"])
    assert (port["code"], port["country_code"], port["latitude"], port["status"]) == (
        "VNHPH",
        "VN",
        "20.865139",
        "active",
    )
    wh = _create(
        client,
        h["manager"],
        code="KHO-MA-01",
        name="Kho Minh An – Bắc Ninh",
        location_type="warehouse",
        party_id=world["a"].id,
        latitude=None,
        longitude=None,
    )
    assert wh["party_id"] == world["a"].id and wh["latitude"] is None
    for who in ("ops_staff", "business_manager", "kh_a"):
        _error(client.post(URL, json={**PORT, "code": "X-1"}, headers=h[who]), 403, "FORBIDDEN")
    _error(client.post(URL, json=PORT), 401, "UNAUTHENTICATED")
    dup = _error(client.post(URL, json={**PORT, "name": "Trùng"}, headers=h["admin"]), 409, "DUPLICATE")
    assert dup["details"][0]["field"] == "body.code"
    with SessionLocal() as db:
        log = db.scalar(select(SysAuditLog).where(SysAuditLog.action == "location.create").order_by(SysAuditLog.id))
    assert log.after["code"] == "VNHPH" and log.actor_id == world["u"]["admin"].id


@pytest.mark.parametrize(
    "bad",
    [
        {"code": "!"},
        {"location_type": "moon"},
        {"latitude": "91", "longitude": "100"},
        {"latitude": "10", "longitude": "181"},
        {"latitude": "10", "longitude": None},
        {"country_code": "VNM"},
        {"name": ""},
    ],
)
def test_validation(client, world, bad):
    _error(client.post(URL, json={**PORT, **bad}, headers=world["h"]["admin"]), 422, "VALIDATION_ERROR")


def test_party_must_be_active(client, world, make_party):
    h = world["h"]["admin"]
    _error(client.post(URL, json={**PORT, "party_id": 999999}, headers=h), 422, "VALIDATION_ERROR")
    old = make_party("Công ty đã giải thể", tax_code="0100000009")
    r = client.post(f"/api/v2/md/parties/{old.id}:archive", headers=world["h"]["manager"])
    assert r.status_code == 200, r.text
    _error(client.post(URL, json={**PORT, "party_id": old.id}, headers=h), 422, "VALIDATION_ERROR")


def test_customer_sees_only_own_active_locations(client, world):
    h = world["h"]
    public = _create(client, h["admin"])
    mine = _create(client, h["admin"], code="DG-MA-01", name="Điểm giao Minh An", location_type="delivery_point",
                   party_id=world["a"].id)  # fmt: skip
    theirs = _create(client, h["admin"], code="KHO-BM-01", name="Kho Bình Minh", location_type="warehouse",
                     party_id=world["b"].id)  # fmt: skip
    old = _create(client, h["admin"], code="KHO-MA-OLD", name="Kho cũ", location_type="warehouse",
                  party_id=world["a"].id)  # fmt: skip
    assert client.post(f"{URL}/{old['id']}:archive", headers=h["manager"]).status_code == 200
    ids = [x["id"] for x in client.get(URL, headers=h["kh_a"]).json()["items"]]
    assert ids == [mine["id"]]
    ids_all = [x["id"] for x in client.get(URL, params={"status": "all"}, headers=h["kh_a"]).json()["items"]]
    assert ids_all == [mine["id"]]  # customer không thấy địa điểm lưu trữ
    assert client.get(f"{URL}/{mine['id']}", headers=h["kh_a"]).status_code == 200
    for other in (public, theirs, old):
        _error(client.get(f"{URL}/{other['id']}", headers=h["kh_a"]), 404, "NOT_FOUND")
    assert [x["id"] for x in client.get(URL, headers=h["kh_b"]).json()["items"]] == [theirs["id"]]
    # nội bộ thấy mọi địa điểm; lọc
    staff = client.get(URL, params={"status": "all"}, headers=h["ops_staff"]).json()
    assert staff["total"] == 4
    wh = client.get(URL, params={"location_type": "warehouse"}, headers=h["ops_staff"]).json()
    assert [x["code"] for x in wh["items"]] == ["KHO-BM-01"]
    q = client.get(URL, params={"q": "minh an"}, headers=h["business_manager"]).json()
    assert [x["code"] for x in q["items"]] == ["DG-MA-01"]
    by_party = client.get(URL, params={"party_id": world["a"].id, "status": "all"}, headers=h["ops_staff"]).json()
    assert by_party["total"] == 2


def test_update_archive_restore(client, world):
    h = world["h"]
    loc = _create(client, h["admin"])
    url = f"{URL}/{loc['id']}"
    _error(client.patch(url, json={"code": "VNSGN"}, headers=h["manager"]), 422, "VALIDATION_ERROR")  # mã cố định
    _error(client.patch(url, json={"latitude": "10"}, headers=h["manager"]), 422, "VALIDATION_ERROR")  # thiếu cặp
    _error(client.patch(url, json={"name": None}, headers=h["manager"]), 422, "VALIDATION_ERROR")
    _error(client.patch(url, json={"name": "X"}, headers=h["ops_staff"]), 403, "FORBIDDEN")
    stale = client.patch(url, json={"name": "X"}, headers={**h["manager"], "If-Match": '"99"'})
    _error(stale, 409, "VERSION_CONFLICT")
    r = client.patch(
        url,
        json={"name": "Cảng Hải Phòng (Đình Vũ)", "latitude": "20.84", "longitude": "106.77"},
        headers={**h["manager"], "If-Match": f'"{loc["row_version"]}"'},
    )
    assert r.status_code == 200 and r.json()["latitude"] == "20.840000" and r.headers["ETag"] == '"2"'
    r = client.patch(url, json={"latitude": None, "longitude": None}, headers=h["manager"])
    assert r.status_code == 200 and r.json()["longitude"] is None
    assert client.post(f"{url}:archive", headers=h["admin"]).json()["status"] == "archived"
    _error(client.post(f"{url}:archive", headers=h["admin"]), 409, "INVALID_STATE_TRANSITION")
    _error(client.patch(url, json={"name": "Y"}, headers=h["manager"]), 409, "INVALID_STATE_TRANSITION")
    assert client.get(URL, headers=h["ops_staff"]).json()["total"] == 0  # mặc định chỉ đang hoạt động
    assert client.post(f"{url}:restore", headers=h["manager"]).json()["status"] == "active"
    with SessionLocal() as db:
        actions = [
            a.action
            for a in db.scalars(
                select(SysAuditLog).where(SysAuditLog.entity_type == "location").order_by(SysAuditLog.id)
            )
        ]
    assert actions == [
        "location.create",
        "location.update",
        "location.update",
        "location.archive",
        "location.restore",
    ]
