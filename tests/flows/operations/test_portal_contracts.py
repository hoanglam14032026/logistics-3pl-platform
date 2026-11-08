"""Cổng khách hàng xem hợp đồng /api/v2/portal/contracts (PRT-01; BR-111; 06 §2.1), trên PostgreSQL."""

import pytest
from sqlalchemy import update

from app.core.db import SessionLocal
from app.flows.operations.models import OpsServiceContract
from tests.conftest import attach

PORTAL = "/api/v2/portal/contracts"
INTERNAL_FIELDS = {
    "status_reason",
    "row_version",
    "customer_id",
    "customer_name",
    "created_at",
    "updated_at",
    "activated_at",
    "portal_enabled",
    "mode",
}


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


@pytest.fixture()
def world(client, make_party, make_contract, make_user, auth_headers):
    a, b = make_party("Khách A", tax_code="0100000001"), make_party("Khách B", tax_code="0100000002")
    a1 = make_contract(a.id, customer_contract_ref="A-01")
    a2 = make_contract(a.id, customer_contract_ref="A-02")
    a_draft = make_contract(a.id, status="draft", customer_contract_ref="A-NHAP")
    a_hidden = make_contract(a.id, portal_enabled=False, customer_contract_ref="A-AN")
    b1 = make_contract(b.id, customer_contract_ref="B-01")
    make_user("a.lan", roles=["customer"], party_id=a.id)
    make_user("a.admin", roles=["customer"], party_id=a.id, is_customer_admin=True)
    make_user("b.hung", roles=["customer"], party_id=b.id)
    make_user("quan.ly", roles=["manager"])
    make_user("quan.tri", roles=["admin"])
    return {
        "a1": a1,
        "a2": a2,
        "a_draft": a_draft,
        "a_hidden": a_hidden,
        "b1": b1,
        "h": {u: auth_headers(u) for u in ("a.lan", "a.admin", "b.hung", "quan.ly", "quan.tri")},
    }


def test_customer_sees_only_own_portal_contracts(client, world):
    page = client.get(PORTAL, params={"sort": "contract_no"}, headers=world["h"]["a.lan"]).json()
    refs = [c["customer_contract_ref"] for c in page["items"]]
    assert refs == ["A-01", "A-02", "A-NHAP"]  # không có hợp đồng tắt cổng, không có hợp đồng của B
    assert page["total"] == 3
    assert not INTERNAL_FIELDS & page["items"][0].keys()
    other = client.get(PORTAL, headers=world["h"]["b.hung"]).json()
    assert [c["customer_contract_ref"] for c in other["items"]] == ["B-01"]


def test_status_filter(client, world):
    items = client.get(PORTAL, params={"status": "draft"}, headers=world["h"]["a.lan"]).json()["items"]
    assert [c["customer_contract_ref"] for c in items] == ["A-NHAP"]


def test_detail_own_contract(client, world):
    r = client.get(f"{PORTAL}/{world['a1'].id}", headers=world["h"]["a.lan"])
    assert r.status_code == 200
    body = r.json()
    assert body["contract_no"] == world["a1"].contract_no and body["status"] == "active"
    assert [s["service_code"] for s in body["service_scopes"]] == ["CUSTOMS_CLEARANCE"]
    assert body["rates"][0]["unit_price"] == "1500000.00"
    assert not INTERNAL_FIELDS & body.keys()


@pytest.mark.parametrize("key", ["b1", "a_hidden"])
def test_detail_not_visible_is_404(client, world, key):
    cid = world[key].id
    for path in ("", "/versions", "/attachments"):
        _error(client.get(f"{PORTAL}/{cid}{path}", headers=world["h"]["a.lan"]), 404, "NOT_FOUND")


def test_training_contract_never_visible(client, world):
    with SessionLocal() as db:
        db.execute(update(OpsServiceContract).where(OpsServiceContract.id == world["a2"].id).values(mode="training"))
        db.commit()
    _error(client.get(f"{PORTAL}/{world['a2'].id}", headers=world["h"]["a.lan"]), 404, "NOT_FOUND")
    refs = [c["customer_contract_ref"] for c in client.get(PORTAL, headers=world["h"]["a.lan"]).json()["items"]]
    assert "A-02" not in refs


def test_versions(client, world):
    versions = client.get(f"{PORTAL}/{world['a1'].id}/versions", headers=world["h"]["a.lan"]).json()
    assert [v["contract_version"] for v in versions] == [1]
    assert set(versions[0]) == {"contract_version", "effective_from", "amendment_ref", "snapshot"}
    assert "status_reason" not in versions[0]["snapshot"]


def test_attachments_only_customer_visible(client, world):
    attach(world["a1"].id, kind="other")  # nội bộ
    items = client.get(f"{PORTAL}/{world['a1'].id}/attachments", headers=world["h"]["a.lan"]).json()
    assert [a["kind"] for a in items] == ["signed_contract"]
    assert set(items[0]) == {"kind", "description", "file"}
    d = client.get(items[0]["file"]["download_url"], headers=world["h"]["a.lan"])
    assert d.status_code == 200 and d.content.startswith(b"%PDF-")
    _error(client.get(items[0]["file"]["download_url"], headers=world["h"]["b.hung"]), 404, "NOT_FOUND")


def test_customer_admin_same_view(client, world):
    a = client.get(PORTAL, headers=world["h"]["a.lan"]).json()
    b = client.get(PORTAL, headers=world["h"]["a.admin"]).json()
    assert a == b


@pytest.mark.parametrize("who", ["quan.ly", "quan.tri"])
def test_internal_users_use_internal_api(client, world, who):
    _error(client.get(PORTAL, headers=world["h"][who]), 403, "FORBIDDEN")


def test_anonymous(client):
    _error(client.get(PORTAL), 401, "UNAUTHENTICATED")


def test_customer_cannot_use_internal_contract_api(client, world):
    _error(client.get("/api/v2/ops/service-contracts", headers=world["h"]["a.lan"]), 403, "FORBIDDEN")
    _error(client.get(f"/api/v2/ops/service-contracts/{world['a1'].id}", headers=world["h"]["a.lan"]), 403, "FORBIDDEN")
