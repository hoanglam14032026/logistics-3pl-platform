"""Cổng khách hàng – theo dõi lô hàng `/api/v2/portal/shipments` (Giai đoạn 8; PRT-06, BR-111), trên PostgreSQL."""

import pytest

from tests.flows.operations.test_shipments import _act, _create, _deliver_body, _error

PORTAL = "/api/v2/portal/shipments"
INTERNAL_FIELDS = {"assignee_id", "priority", "status_reason", "created_by", "row_version", "history", "customer_id"}


@pytest.fixture()
def world(client, make_party, make_contract, make_user, auth_headers):
    from tests.flows.operations.test_shipments import ROLES

    a = make_party("Công ty Minh An", tax_code="0100000001")
    b = make_party("Công ty Bình Minh", tax_code="0100000002")
    seller = make_party("Shanghai Export Co.", party_type="foreign_seller", country_code="CN")
    users = {role: make_user(f"u.{role}", roles=[role]) for role in ROLES}
    users["ops_staff_2"] = make_user("u.ops2", roles=["ops_staff"])
    users["kh_a"] = make_user("kh.a", roles=["customer"], party_id=a.id, email="kh@a.vn")
    users["kh_a2"] = make_user("kh.a2", roles=["customer"], party_id=a.id, email="kh2@a.vn")
    users["kh_b"] = make_user("kh.b", roles=["customer"], party_id=b.id, email="kh@b.vn")
    w = {
        "a": a,
        "b": b,
        "seller": seller,
        "contract_a": make_contract(a.id),
        "contract_b": make_contract(b.id),
        "contract_a_hidden": make_contract(a.id, portal_enabled=False),
        "users": users,
    }
    w["h"] = {role: auth_headers(u.username) for role, u in users.items()}
    return w


def _ids(client, headers, **params) -> list[int]:
    r = client.get(PORTAL, params=params, headers=headers)
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def test_customer_sees_only_own_non_draft_shipments(client, world):
    mine = _create(client, world)
    _act(client, world, mine["id"], "submit")
    draft = _create(client, world)  # nháp: nội bộ
    hidden = _create(client, world, contract="contract_a_hidden")  # hợp đồng không bật cổng
    _act(client, world, hidden["id"], "submit")
    other = _create(client, world, contract="contract_b")
    _act(client, world, other["id"], "submit")

    assert _ids(client, world["h"]["kh_a"]) == [mine["id"]]
    assert _ids(client, world["h"]["kh_a2"]) == [mine["id"]]  # mọi tài khoản của khách hàng
    assert _ids(client, world["h"]["kh_b"]) == [other["id"]]
    for sid in (draft["id"], hidden["id"], other["id"], 999999):
        _error(client.get(f"{PORTAL}/{sid}", headers=world["h"]["kh_a"]), 404, "NOT_FOUND")  # như không tồn tại
    assert _ids(client, world["h"]["kh_a"], status="docs_collecting") == [mine["id"]]
    assert _ids(client, world["h"]["kh_a"], status="delivered") == []
    assert _ids(client, world["h"]["kh_a"], contract_id=world["contract_b"].id) == []


def test_tracking_detail_has_milestones_without_internal_data(client, world):
    s = _create(client, world)
    for step, body in (
        ("submit", None),
        ("verify-documents", {"note": "Ghi chú nội bộ"}),
        ("start", None),
        ("deliver", _deliver_body()),
    ):
        assert _act(client, world, s["id"], step, body).status_code == 200
    r = client.get(f"{PORTAL}/{s['id']}", headers=world["h"]["kh_a"])
    assert r.status_code == 200
    body = r.json()
    assert not INTERNAL_FIELDS & body.keys()
    assert "Ghi chú nội bộ" not in r.text and "changed_by" not in r.text
    assert (body["shipment_no"], body["contract_no"]) == (s["shipment_no"], world["contract_a"].contract_no)
    assert body["status"] == "delivered" and body["delivered_at"] and body["eta"] == s["eta"]
    assert [m["status"] for m in body["milestones"]] == [
        "docs_collecting",
        "docs_verified",
        "in_execution",
        "delivered",
    ]
    assert all(m["at"].endswith("Z") for m in body["milestones"])
    assert body["cargo_summary"]["packages"] == 120
    listed = client.get(PORTAL, headers=world["h"]["kh_a"]).json()
    assert listed["total"] == 1 and not INTERNAL_FIELDS & listed["items"][0].keys()


def test_cancelled_shipment_hides_reason(client, world):
    s = _create(client, world)
    _act(client, world, s["id"], "submit")
    _act(client, world, s["id"], "cancel", {"reason": "Lý do nội bộ: khách nợ phí"})
    r = client.get(f"{PORTAL}/{s['id']}", headers=world["h"]["kh_a"])
    assert r.json()["status"] == "cancelled" and "nợ phí" not in r.text


def test_portal_roles(client, world):
    s = _create(client, world)
    _act(client, world, s["id"], "submit")
    for role in ("ops_staff", "manager", "admin", "instructor"):
        _error(client.get(PORTAL, headers=world["h"][role]), 403, "FORBIDDEN")
    _error(client.get("/api/v2/ops/shipments", headers=world["h"]["kh_a"]), 403, "FORBIDDEN")
    _error(client.get(f"/api/v2/ops/shipments/{s['id']}", headers=world["h"]["kh_a"]), 403, "FORBIDDEN")
    assert client.get(PORTAL).status_code == 401
