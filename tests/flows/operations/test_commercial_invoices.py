"""Hóa đơn thương mại `/api/v2/ops/commercial-invoices` (Giai đoạn 10), trên PostgreSQL."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import SessionLocal
from app.shared.audit.models import SysAuditLog

URL = "/api/v2/ops/commercial-invoices"


@pytest.fixture()
def world(client, make_party, make_contract, make_user, auth_headers):
    customer = make_party("Công ty Minh An", tax_code="0100000001")
    seller = make_party("Shanghai Export Co.", party_type="foreign_seller", country_code="CN")
    contract = make_contract(customer.id)
    draft = make_contract(customer.id, status="draft")
    roles = {
        "ops": "ops_staff",
        "ops2": "ops_staff",
        "manager": "manager",
        "bm": "business_manager",
        "learner": "learner",
        "admin": "admin",
    }
    users = {k: make_user(f"u.{k}", roles=[r]) for k, r in roles.items()}
    h = {k: auth_headers(u.username) for k, u in users.items()}
    shipment = {
        "contract_id": contract.id,
        "direction": "import",
        "transport_mode": "sea_fcl",
        "incoterm": "CIF",
        "shipper_id": seller.id,
        "consignee_id": customer.id,
        "origin_location": "Thượng Hải",
        "destination_location": "Hải Phòng",
        "required_delivery_at": "2026-12-31T00:00:00+07:00",
    }
    r = client.post("/api/v2/ops/shipments", json=shipment, headers=h["ops"])
    assert r.status_code == 201, r.text
    return {"contract": contract, "draft": draft, "shipment": r.json(), "h": h, "users": users}


def _body(**overrides) -> dict:
    return {
        "invoice_no": "INV-2026-001",
        "invoice_date": "2026-11-05",
        "seller_name": "Shanghai Export Co.",
        "buyer_name": "Công ty Minh An",
        "incoterm": "cif",
        "currency": "usd",
        "total_amount": "12500.5",
        "items": [
            {
                "description": "Máy in laser",
                "hs_code": "8443.32",
                "quantity": "10",
                "unit": "chiếc",
                "unit_price": "1000.05",
                "amount": "10000.50",
            },
            {"description": "Hộp mực", "quantity": 100, "unit_price": 25, "amount": 2500},
        ],
        **overrides,
    }


def test_create_with_shipment_amounts_are_strings(client, world):
    s = world["shipment"]
    r = client.post(URL, json=_body(shipment_id=s["id"]), headers=world["h"]["ops"])
    assert r.status_code == 201, r.text
    inv = r.json()
    assert (inv["contract_id"], inv["shipment_id"]) == (world["contract"].id, s["id"])  # hợp đồng lấy theo lô
    assert inv["total_amount"] == "12500.50" and isinstance(inv["total_amount"], str)
    assert (inv["incoterm"], inv["currency"], inv["source_doc_job_id"]) == ("CIF", "USD", None)
    assert [(i["line_no"], i["amount"], i["unit_price"], i["quantity"]) for i in inv["items"]] == [
        (1, "10000.50", "1000.0500", "10.000"),
        (2, "2500.00", "25.0000", "100.000"),
    ]
    assert client.get(f"{URL}/{inv['id']}", headers=world["h"]["bm"]).json() == inv
    listed = client.get(URL, params={"shipment_id": s["id"]}, headers=world["h"]["admin"]).json()
    assert listed["total"] == 1 and listed["items"][0]["total_amount"] == "12500.50"
    with SessionLocal() as db:
        [log] = db.scalars(select(SysAuditLog).where(SysAuditLog.action == "invoice.create")).all()
    assert log.after["total_amount"] == "12500.50" and log.after["item_count"] == 2

    dup = client.post(URL, json=_body(shipment_id=s["id"]), headers=world["h"]["ops"])
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "DUPLICATE"


def test_contract_only_and_ownership_rules(client, world):
    h, s = world["h"], world["shipment"]
    assert client.post(URL, json=_body(contract_id=world["contract"].id), headers=h["ops2"]).status_code == 201
    assert (
        client.post(URL, json=_body(contract_id=world["draft"].id, invoice_no="X"), headers=h["ops"]).status_code == 422
    )
    mismatch = client.post(URL, json=_body(shipment_id=s["id"], contract_id=world["draft"].id), headers=h["ops"])
    assert mismatch.status_code == 422 and mismatch.json()["error"]["details"][0]["field"] == "body.contract_id"
    # ops_staff chỉ gắn vào lô được phân công; manager mọi lô
    assert client.post(URL, json=_body(shipment_id=s["id"], invoice_no="B"), headers=h["ops2"]).status_code == 403
    assert client.post(URL, json=_body(shipment_id=s["id"], invoice_no="B"), headers=h["manager"]).status_code == 201
    assert client.post(URL, json=_body(), headers=h["ops"]).status_code == 422  # thiếu cả lô và hợp đồng
    assert client.post(URL, json=_body(shipment_id=99999), headers=h["ops"]).status_code == 422


def test_cancelled_shipment_rejected(client, world):
    s = world["shipment"]
    r = client.post(f"/api/v2/ops/shipments/{s['id']}:cancel", json={"reason": "Khách hủy"}, headers=world["h"]["ops"])
    assert r.status_code == 200, r.text
    assert client.post(URL, json=_body(shipment_id=s["id"]), headers=world["h"]["ops"]).status_code == 422


@pytest.mark.parametrize(
    "override",
    [
        {"total_amount": "-1"},
        {"total_amount": "1.234"},
        {"total_amount": "12345678901234567.00"},
        {"currency": "US"},
        {"incoterm": "DDU"},
        {"items": [{"description": ""}]},
        {"items": [{"description": "x", "hs_code": "abc"}]},
        {"invoice_date": "05/11/2026"},
        {"extra": 1},
    ],
)
def test_validation(client, world, override):
    r = client.post(URL, json=_body(contract_id=world["contract"].id, **override), headers=world["h"]["ops"])
    assert r.status_code == 422, r.text


def test_rbac(client, world):
    for who in ("bm", "learner", "admin"):
        assert (
            client.post(URL, json=_body(contract_id=world["contract"].id), headers=world["h"][who]).status_code == 403
        )
    assert client.get(URL, headers=world["h"]["learner"]).status_code == 403
    assert client.get(URL).status_code == 401


def test_database_guards(client, world):
    inv = client.post(URL, json=_body(contract_id=world["contract"].id), headers=world["h"]["ops"]).json()
    for sql in (
        f"DELETE FROM ops_commercial_invoice_items WHERE invoice_id = {inv['id']}",
        f"UPDATE ops_commercial_invoice_items SET amount = 1 WHERE invoice_id = {inv['id']}",
        f"DELETE FROM ops_commercial_invoices WHERE id = {inv['id']}",
    ):
        with SessionLocal() as db, pytest.raises(DBAPIError, match="RestrictViolation"):
            db.execute(text(sql))
            db.commit()
