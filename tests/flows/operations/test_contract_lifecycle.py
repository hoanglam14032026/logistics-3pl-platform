# ruff: noqa: F401, F811  (fixture world, redis_url nhập từ module test khác)
"""Giai đoạn 19 – phụ lục (duyệt 2 người), chấm dứt trước hạn, quyết toán BR-110 (PostgreSQL + Redis thật)."""

from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import SessionLocal
from app.core.timeutil import today_vn
from app.flows.assessment.models import AsmFactContract, AsmFactSettlement
from app.shared.audit.models import SysAuditLog
from app.shared.events.models import SysEvent
from tests.conftest import DEFAULT_RATE, DEFAULT_SCOPE, attach
from tests.flows.control.test_planning import redis_url
from tests.flows.control.test_sync_notify import drain_all
from tests.flows.operations.test_shipments import URL as SHIP
from tests.flows.operations.test_shipments import _act, _create, _error, world

URL = "/api/v2/ops/service-contracts"
TODAY = today_vn()


@pytest.fixture()
def extra(world, make_user, auth_headers):
    """Người kiêm hai vai trò – kiểm "khác người" chứ không chỉ khác vai trò."""
    both = make_user("u.ql_kd", roles=["manager", "business_manager"])
    return {"both": auth_headers(both.username), "both_user": both}


def _events(code: str) -> list[dict]:
    with SessionLocal() as db:
        return [
            e.payload for e in db.scalars(select(SysEvent).where(SysEvent.event_code == code).order_by(SysEvent.id))
        ]


def _logs(prefix: str) -> list[str]:
    with SessionLocal() as db:
        return [
            a.action
            for a in db.scalars(
                select(SysAuditLog).where(SysAuditLog.action.like(f"{prefix}%")).order_by(SysAuditLog.id)
            )
        ]


def _price_change(price: str = "1700000") -> dict:
    return {
        "reason": "Điều chỉnh giá dịch vụ thông quan",
        "effective_from": str(TODAY),
        "rates": [{**DEFAULT_RATE, "unit_price": price}],
        "valid_to": str(TODAY + timedelta(days=500)),
    }


# --- Phụ lục duyệt 2 người ------------------------------------------------------------------------------------------


def test_amendment_requires_two_distinct_approvers(client, world, extra):
    h, cid = world["h"], world["contract_a"].id
    url = f"{URL}/{cid}"
    for who in ("business_manager", "customer", "instructor"):
        _error(client.post(f"{url}/amendments", json=_price_change(), headers=h[who]), 403, "FORBIDDEN")
    r = client.post(f"{url}/amendments", json=_price_change(), headers=h["ops_staff"])
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["amendment_no"], a["status"], a["base_contract_version"]) == ("PL-01", "pending_approval", 1)
    rate = lambda: client.get(url, headers=h["manager"]).json()["rates"][0]["unit_price"]  # noqa: E731
    assert rate() == "1500000.00"  # chưa duyệt → chưa hiệu lực
    ap = f"{URL}/amendments/{a['id']}"
    _error(client.post(f"{ap}:approve", headers=h["ops_staff"]), 403, "FORBIDDEN")
    r = client.post(f"{ap}:approve", json={"note": "Đồng ý mức giá"}, headers=h["manager"])
    assert r.json()["status"] == "pending_second_approval" and rate() == "1500000.00"
    _error(client.post(f"{ap}:approve", headers=h["manager"]), 403, "FORBIDDEN")  # cùng người duyệt 1
    _error(client.post(f"{ap}:withdraw", headers=h["manager"]), 403, "FORBIDDEN")  # không phải người lập
    r = client.post(f"{ap}:approve", headers=h["business_manager"])
    body = r.json()
    assert (body["status"], body["applied_version"], body["second_approved_by"]) == (
        "effective",
        2,
        world["users"]["business_manager"].id,
    )
    detail = client.get(url, headers=h["manager"]).json()
    assert (detail["contract_version"], rate()) == (2, "1700000.00")
    v1 = client.get(f"{url}/versions/1", headers=h["manager"]).json()
    assert v1["snapshot"]["rates"][0]["unit_price"] == "1500000.00"  # phiên bản cũ giữ nguyên
    assert client.get(f"{url}/versions/2", headers=h["manager"]).json()["amendment_ref"] == "PL-01"
    ev = _events("EV-103")
    assert len(ev) == 1 and (ev[0]["data"]["amendment_no"], ev[0]["data"]["contract_version"]) == ("PL-01", 2)
    assert ev[0]["data"]["changed_groups"] == ["rates", "valid_to"]
    _error(client.post(f"{ap}:approve", headers=h["admin"]), 403, "FORBIDDEN")
    _error(client.post(f"{ap}:approve", headers=extra["both"]), 409, "INVALID_STATE_TRANSITION")
    # người lập là manager: tự duyệt lần 1 được, lần 2 phải người khác
    a2 = client.post(f"{url}/amendments", json=_price_change("1800000"), headers=extra["both"]).json()
    assert a2["amendment_no"] == "PL-02"
    client.post(f"{URL}/amendments/{a2['id']}:approve", headers=h["business_manager"])
    _error(client.post(f"{URL}/amendments/{a2['id']}:approve", headers=extra["both"]), 403, "FORBIDDEN")
    assert _logs("amendment.")[:3] == ["amendment.create", "amendment.approve_first", "amendment.approve_second"]
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(
            text("UPDATE ops_contract_amendments SET second_approved_by = first_approved_by WHERE id = :i"),
            {"i": a2["id"]},
        )


def test_amendment_stale_reject_withdraw(client, world):
    h, cid = world["h"], world["contract_a"].id
    url = f"{URL}/{cid}/amendments"
    a1 = client.post(url, json=_price_change("1600000"), headers=h["ops_staff"]).json()
    a2 = client.post(url, json=_price_change("1650000"), headers=h["ops_staff"]).json()
    a3 = client.post(url, json=_price_change("1660000"), headers=h["ops_staff"]).json()
    for a in (a1, a2):
        client.post(f"{URL}/amendments/{a['id']}:approve", headers=h["manager"])
    client.post(f"{URL}/amendments/{a1['id']}:approve", headers=h["business_manager"])
    err = _error(
        client.post(f"{URL}/amendments/{a2['id']}:approve", headers=h["business_manager"]),
        409,
        "INVALID_STATE_TRANSITION",
    )
    assert "lập phụ lục mới" in err["message"]  # lập trên phiên bản 1, hợp đồng đã sang 2
    _error(client.post(f"{URL}/amendments/{a3['id']}:reject", json={}, headers=h["manager"]), 422, "VALIDATION_ERROR")
    r = client.post(f"{URL}/amendments/{a3['id']}:reject", json={"note": "Giá chưa hợp lý"}, headers=h["manager"])
    assert (r.json()["status"], r.json()["decision_note"]) == ("rejected", "Giá chưa hợp lý")
    _error(client.post(f"{URL}/amendments/{a3['id']}:approve", headers=h["manager"]), 409, "INVALID_STATE_TRANSITION")
    r = client.post(f"{URL}/amendments/{a2['id']}:withdraw", headers=h["ops_staff"])
    assert r.json()["status"] == "withdrawn"
    assert [a["status"] for a in client.get(url, headers=h["instructor"]).json()] == [
        "effective",
        "withdrawn",
        "rejected",
    ]


# --- Chấm dứt trước hạn -------------------------------------------------------------------------------------------


def _terminate_req(client, h, cid, **over):
    body = {"reason": "Khách hàng đề nghị chấm dứt do tái cơ cấu", "effective_date": str(TODAY), **over}
    return client.post(f"{URL}/{cid}/terminations", json=body, headers=h)


def test_termination_cancels_unstarted_shipments(client, world, extra):
    from tests.flows.operations.test_shipments import pass_document_check

    h, cid = world["h"], world["contract_a"].id
    s_draft = _create(client, world)
    s_docs = _create(client, world)
    _act(client, world, s_docs["id"], "submit")
    s_exec = _create(client, world)
    for step in ("submit", "verify-documents", "start"):
        assert _act(client, world, s_exec["id"], step).status_code == 200
    _error(_terminate_req(client, h["ops_staff"], cid), 403, "FORBIDDEN")
    _error(
        _terminate_req(client, h["manager"], cid, effective_date=str(TODAY + timedelta(days=4000))),
        422,
        "VALIDATION_ERROR",
    )
    _error(_terminate_req(client, h["manager"], cid, reason="ngắn"), 422, "VALIDATION_ERROR")
    t = _terminate_req(client, h["manager"], cid).json()
    assert t["status"] == "pending"
    _error(_terminate_req(client, h["manager"], cid), 409, "DUPLICATE")
    tp = f"{URL}/terminations/{t['id']}"
    _error(client.post(f"{tp}:approve", headers=h["manager"]), 403, "FORBIDDEN")
    assert client.get(f"{URL}/{cid}", headers=h["manager"]).json()["status"] == "active"
    r = client.post(f"{tp}:approve", json={"note": "Đồng ý"}, headers=h["business_manager"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], sorted(body["cancelled_shipment_ids"]), body["open_shipment_ids"]) == (
        "approved",
        sorted([s_draft["id"], s_docs["id"]]),
        [s_exec["id"]],
    )
    status = {
        s: client.get(f"{SHIP}/{s}", headers=h["manager"]).json() for s in (s_draft["id"], s_docs["id"], s_exec["id"])
    }
    assert (
        status[s_draft["id"]]["status"] == "cancelled" and "Chấm dứt hợp đồng" in status[s_draft["id"]]["status_reason"]
    )
    assert status[s_docs["id"]]["status"] == "cancelled" and status[s_exec["id"]]["status"] == "in_execution"
    contract = client.get(f"{URL}/{cid}", headers=h["manager"]).json()
    assert contract["status"] == "liquidating" and contract["status_reason"].startswith(
        f"Chấm dứt trước hạn từ {TODAY}"
    )
    assert _events("EV-104")[0]["data"] == {"contract_id": cid, "reason": "terminated", "termination_id": t["id"]}
    cancelled = [e for e in _events("EV-112") if e["data"]["to"] == "cancelled"]
    assert len(cancelled) == 2
    assert "contract.terminate" in _logs("contract.") and _logs("termination.") == [
        "termination.request",
        "termination.approve",
    ]
    _error(_create_raw(client, world), 422, "VALIDATION_ERROR")  # hợp đồng thanh lý: không tạo lô mới
    _error(
        client.post(f"{tp}:reject", json={"note": "Muộn"}, headers=h["business_manager"]),
        409,
        "INVALID_STATE_TRANSITION",
    )
    # người kiêm 2 vai trò không tự duyệt đề xuất của mình; từ chối giữ hợp đồng
    cb = world["contract_b"].id
    t2 = _terminate_req(client, extra["both"], cb).json()
    _error(client.post(f"{URL}/terminations/{t2['id']}:approve", headers=extra["both"]), 403, "FORBIDDEN")
    r = client.post(
        f"{URL}/terminations/{t2['id']}:reject", json={"note": "Chưa đủ căn cứ"}, headers=h["business_manager"]
    )
    assert (
        r.json()["status"] == "rejected"
        and client.get(f"{URL}/{cb}", headers=h["manager"]).json()["status"] == "active"
    )


def _create_raw(client, world):
    from tests.flows.operations.test_shipments import _payload

    return client.post(SHIP, json=_payload(world), headers=world["h"]["ops_staff"])


# --- Quyết toán BR-110 ----------------------------------------------------------------------------------------------


def _invoice(client, h, cid, no, currency, amount):
    body = {
        "contract_id": cid,
        "invoice_no": no,
        "invoice_date": str(TODAY - timedelta(days=3)),
        "seller_name": "Shanghai Export Co.",
        "buyer_name": "Công ty Minh An",
        "currency": currency,
        "total_amount": amount,
        "items": [{"description": "Hàng", "quantity": "1", "unit_price": amount}],
    }
    r = client.post("/api/v2/ops/commercial-invoices", json=body, headers=h)
    assert r.status_code == 201, r.text


def test_settlement_br110_and_close(client, world, extra, redis_url):
    h, cid = world["h"], world["contract_a"].id
    url = f"{URL}/{cid}"
    _create(client, world)
    _invoice(client, h["manager"], cid, "INV-1", "VND", "100000000")
    _invoice(client, h["manager"], cid, "INV-2", "VND", "50000000.50")
    _invoice(client, h["manager"], cid, "INV-3", "USD", "1200")
    err = _error(client.post(f"{url}/settlement", headers=h["manager"]), 422, "VALIDATION_ERROR")
    assert {d["field"] for d in err["details"]} == {"body.status", "body.shipments"}  # còn hạn + còn lô
    # còn phụ lục chờ duyệt cũng chặn
    pending = client.post(f"{url}/amendments", json=_price_change(), headers=h["ops_staff"]).json()
    t = _terminate_req(client, h["manager"], cid).json()
    client.post(f"{URL}/terminations/{t['id']}:approve", headers=h["business_manager"])
    err = _error(client.post(f"{url}/settlement", headers=h["manager"]), 422, "VALIDATION_ERROR")
    assert {d["field"] for d in err["details"]} == {"body.amendments"}  # lô nháp đã tự hủy
    client.post(f"{URL}/amendments/{pending['id']}:reject", json={"note": "Đã chấm dứt"}, headers=h["manager"])
    with SessionLocal() as db:  # ngày chấm dứt đã qua → kỳ quyết toán tới ngày chấm dứt
        db.execute(text("UPDATE ops_contract_terminations SET effective_date = :d"), {"d": TODAY - timedelta(days=2)})
        db.commit()
    _error(client.post(f"{url}/settlement", headers=h["business_manager"]), 403, "FORBIDDEN")
    r = client.post(f"{url}/settlement", json={"currency": "VND"}, headers=h["manager"])
    assert r.status_code == 201, r.text
    st = r.json()
    assert st["settlement_no"].startswith(f"QT-{TODAY.year}-")
    assert (st["invoice_count"], st["invoice_total"], st["other_currency_totals"], st["balance_due"]) == (
        2,
        "150000000.50",
        {"USD": "1200.00"},
        "150000000.50",
    )
    two_days_ago = str(TODAY - timedelta(days=2))
    assert (st["period_from"], st["period_to"]) == (str(world["contract_a"].valid_from), two_days_ago)
    assert st["kpi_snapshot"]["period"]["to"] == two_days_ago and "targets" in st["kpi_snapshot"]
    _error(client.post(f"{url}/settlement", headers=h["manager"]), 409, "DUPLICATE")
    _error(
        client.patch(f"{url}/settlement", json={"sla_deduction": "1000000"}, headers=h["manager"]),
        422,
        "VALIDATION_ERROR",
    )  # giảm trừ cần căn cứ
    r = client.patch(
        f"{url}/settlement",
        json={
            "sla_deduction": "1000000",
            "deduction_note": "KPI-07 tháng 10 dưới mục tiêu",
            "paid_amount": "100000000",
        },
        headers=h["manager"],
    )
    assert r.json()["balance_due"] == "49000000.50"
    _error(client.post(f"{url}/settlement:approve", headers=h["business_manager"]), 409, "INVALID_STATE_TRANSITION")
    assert client.post(f"{url}/settlement:submit", headers=h["manager"]).json()["status"] == "pending_approval"
    _error(
        client.patch(f"{url}/settlement", json={"paid_amount": "1"}, headers=h["manager"]),
        409,
        "INVALID_STATE_TRANSITION",
    )
    _error(client.post(f"{url}/settlement:approve", headers=h["manager"]), 403, "FORBIDDEN")
    r = client.post(
        f"{url}/settlement:reject", json={"note": "Thiếu bồi thường lô hỏng"}, headers=h["business_manager"]
    )
    assert r.json()["status"] == "rejected"
    client.patch(f"{url}/settlement", json={"compensation": "2000000"}, headers=h["manager"])
    client.post(f"{url}/settlement:submit", headers=h["manager"])
    # đóng hợp đồng trước khi duyệt quyết toán → 422
    attach(cid, kind="liquidation_minutes")
    err = _error(client.post(f"{url}:close", headers=h["manager"]), 422, "VALIDATION_ERROR")
    assert [d["field"] for d in err["details"]] == ["body.settlement"]
    r = client.post(f"{url}/settlement:approve", json={"note": "Đồng ý"}, headers=h["business_manager"])
    assert (r.json()["status"], r.json()["balance_due"]) == ("approved", "47000000.50")
    ev = _events("EV-144")
    assert len(ev) == 1 and ev[0]["data"]["balance_due"] == "47000000.50" and ev[0]["data"]["currency"] == "VND"
    assert _logs("settlement.") == [
        "settlement.create",
        "settlement.update",
        "settlement.submit",
        "settlement.reject",
        "settlement.update",
        "settlement.submit",
        "settlement.approve",
    ]
    assert client.post(f"{url}:close", headers=h["manager"]).json()["status"] == "closed"
    drain_all(redis_url)
    with SessionLocal() as db:
        fact = db.get(AsmFactSettlement, st["id"])
    assert (str(fact.balance_due), fact.invoice_count) == ("47000000.50", 2)
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("DELETE FROM ops_contract_settlements"))


def test_settlement_preparer_cannot_approve_and_ev103_reaches_assessment(client, world, extra, redis_url):
    h, cid = world["h"], world["contract_b"].id
    # EV-103 → Luồng 4 cập nhật SLA / thời hạn
    a = client.post(f"{URL}/{cid}/amendments", json=_price_change(), headers=h["ops_staff"]).json()
    client.post(f"{URL}/amendments/{a['id']}:approve", headers=h["manager"])
    client.post(f"{URL}/amendments/{a['id']}:approve", headers=h["business_manager"])
    from app.shared.events import consumer

    consumer.replay("assessment")  # EV-101 phát trước khi bật hàng đợi trong test → xử lý thẳng từ outbox
    with SessionLocal() as db:
        assert str(db.get(AsmFactContract, cid).valid_to) == str(TODAY + timedelta(days=500))
    # hết hạn → thanh lý → quyết toán do người kiêm 2 vai trò lập: không tự duyệt
    with SessionLocal() as db:
        db.execute(
            text("UPDATE ops_service_contracts SET valid_to = :d WHERE id = :i"),
            {"d": today_vn() - timedelta(days=1), "i": cid},
        )
        db.commit()
    assert client.post(f"{URL}/{cid}:start-liquidation", headers=h["manager"]).json()["status"] == "liquidating"
    assert client.post(f"{URL}/{cid}/settlement", headers=extra["both"]).status_code == 201
    client.post(f"{URL}/{cid}/settlement:submit", headers=extra["both"])
    _error(client.post(f"{URL}/{cid}/settlement:approve", headers=extra["both"]), 403, "FORBIDDEN")
    assert client.post(f"{URL}/{cid}/settlement:approve", headers=h["business_manager"]).json()["status"] == "approved"
