"""Duyệt 2 người cho job hợp đồng / hóa đơn giá trị lớn, tạo hóa đơn thương mại từ job đã duyệt, sự kiện `doc.*` vào
outbox (Giai đoạn 10), trên PostgreSQL – model giả lập qua HTTP local."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal
from app.shared.docai import registry
from app.shared.docai.service import requires_dual_approval
from app.shared.events.models import SysEvent
from tests.shared.docai.conftest import JOBS, answer, logs, upload

CONTRACT_TEXT = (
    "HỢP ĐỒNG DỊCH VỤ số MA-2026/HD-07 ký ngày 01/11/2026 với CÔNG TY TNHH MINH AN. "
    "Hiệu lực từ 01/11/2026 đến 31/10/2027."
)
CONTRACT_ANSWER = answer(
    customer_name=("CÔNG TY TNHH MINH AN", 0.95),
    customer_contract_ref=("MA-2026/HD-07", 0.96),
    signed_date=("2026-11-01", 0.95),
    valid_from=("2026-11-01", 0.95),
    valid_to=("2027-10-31", 0.95),
)


def _invoice(total: str, currency: str = "USD") -> tuple[bytes, dict]:
    text_ = f"COMMERCIAL INVOICE INV-01 05/11/2026 Seller Shanghai Export Buyer Minh An CIF {currency} TOTAL {total}"
    reply = answer(
        invoice_no=("INV-01", 0.95),
        invoice_date=("2026-11-05", 0.95),
        seller_name=("Shanghai Export", 0.95),
        buyer_name=("Minh An", 0.95),
        incoterm=("CIF", 0.95),
        currency=(currency, 0.95),
        total_amount=(total, 0.95),
        items=([{"description": "Printer", "quantity": 2, "unit_price": total, "amount": total}], 0.9),
    )
    return text_.encode(), reply


@pytest.fixture()
def model(fake_ollama, make_model):
    return make_model()


@pytest.fixture()
def manager2(make_user, auth_headers):
    make_user("ql.giang", roles=["manager"])
    return auth_headers("ql.giang")


def _job(client, users, model_id, fake, content: bytes, reply: dict, doc_type: str) -> dict:
    fake.reply = reply
    r = upload(client, users["h"]["ops"], model_id, content=content, doc_type=doc_type)
    assert r.status_code == 202, r.text
    job = client.get(f"{JOBS}/{r.json()['id']}", headers=users["h"]["ops"]).json()
    assert job["status"] == "ready_for_review", job["review_reasons"]
    return job


def _approve(client, job_id, headers):
    return client.post(f"{JOBS}/{job_id}/approve", headers=headers)


def test_contract_needs_two_different_approvers(client, users, model, fake_ollama, manager2):
    job = _job(client, users, model.id, fake_ollama, CONTRACT_TEXT.encode(), CONTRACT_ANSWER, "service_contract")
    jid, h = job["id"], users["h"]
    first = _approve(client, jid, h["manager"])
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "pending_second_approval" and body["approved_at"] is None and body["create_hint"] is None
    # người duyệt đầu, người tải lên → không được duyệt lần 2
    same = _approve(client, jid, h["manager"])
    assert same.status_code == 403 and "khác người duyệt đầu" in same.json()["error"]["message"]
    uploader = _approve(client, jid, h["ops"])
    assert uploader.status_code == 403 and "tải chứng từ lên" in uploader.json()["error"]["message"]
    # đang chờ người thứ 2: không sửa được
    assert (
        client.patch(f"{JOBS}/{jid}/review", json={"fields": {"valid_to": "2027-12-31"}}, headers=h["ops"]).status_code
        == 409
    )
    # chưa đủ 2 người → chưa tạo bản ghi được
    r = client.post(f"/api/v2/ops/service-contracts?source_doc_job_id={jid}", json={}, headers=h["ops"])
    assert r.status_code == 422

    done = _approve(client, jid, manager2)
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["status"] == "approved" and out["approved_data"]["valid_to"] == "2027-10-31"
    first_log, second_log = logs("doc_job.approve", jid)
    assert first_log.after["stage"] == "first" and first_log.actor_id == users["user"]["manager"].id
    assert (
        second_log.after["stage"] == "second" and second_log.after["first_approved_by"] == users["user"]["manager"].id
    )
    assert _approve(client, jid, manager2).status_code == 409


def test_uploader_may_be_first_approver(client, users, model, fake_ollama):
    job = _job(client, users, model.id, fake_ollama, CONTRACT_TEXT.encode(), CONTRACT_ANSWER, "service_contract")
    assert _approve(client, job["id"], users["h"]["ops"]).json()["status"] == "pending_second_approval"
    assert _approve(client, job["id"], users["h"]["manager"]).json()["status"] == "approved"


def test_second_approver_can_reject(client, users, model, fake_ollama, manager2):
    job = _job(client, users, model.id, fake_ollama, CONTRACT_TEXT.encode(), CONTRACT_ANSWER, "service_contract")
    _approve(client, job["id"], users["h"]["ops"])
    r = client.post(f"{JOBS}/{job['id']}/reject", json={"reason": "Sai thời hạn hợp đồng"}, headers=manager2)
    assert r.status_code == 200 and r.json()["status"] == "rejected"


def test_database_enforces_distinct_approvers(client, users, model, fake_ollama):
    job = _job(client, users, model.id, fake_ollama, CONTRACT_TEXT.encode(), CONTRACT_ANSWER, "service_contract")
    _approve(client, job["id"], users["h"]["manager"])
    for approver in (users["user"]["manager"].id, users["user"]["ops"].id):
        with SessionLocal() as db, pytest.raises(IntegrityError, match="dual_approval_distinct_people"):
            db.execute(
                text(
                    "UPDATE sys_doc_jobs SET status = 'approved', approved_at = now(), approved_by = :a, "
                    "approved_data = '{}' WHERE id = :id"
                ),
                {"a": approver, "id": job["id"]},
            )
            db.commit()


@pytest.mark.parametrize(
    ("total", "currency", "dual"),
    [
        ("1250.50", "USD", False),
        ("25000", "USD", True),
        ("600000000", "VND", True),
        ("100000", "VND", False),
        ("10", "JPY", True),
    ],  # đồng tiền không có ngưỡng → luôn 2 người
)
def test_invoice_dual_approval_by_amount(client, users, model, fake_ollama, total, currency, dual):
    content, reply = _invoice(total, currency)
    job = _job(client, users, model.id, fake_ollama, content, reply, "commercial_invoice")
    status = _approve(client, job["id"], users["h"]["ops"]).json()["status"]
    assert status == ("pending_second_approval" if dual else "approved")


def test_threshold_is_configurable(monkeypatch):
    from app.core.config import get_settings

    schema = registry.get_schema("commercial_invoice")
    monkeypatch.setenv("DOCAI_DUAL_APPROVAL_THRESHOLDS", "USD:1000")
    get_settings.cache_clear()
    assert requires_dual_approval(schema, {"total_amount": "1000.00", "currency": "usd"}) is True
    assert requires_dual_approval(schema, {"total_amount": "999.99", "currency": "USD"}) is False
    assert requires_dual_approval(schema, {"total_amount": None, "currency": "USD"}) is True
    assert requires_dual_approval(registry.get_schema("party"), {}) is False


def test_invoice_created_from_approved_job_with_doc_events(
    client, users, model, fake_ollama, make_party, make_contract
):
    contract = make_contract(make_party("Công ty Minh An", tax_code="0100000001").id)
    content, reply = _invoice("1250.5")
    job = _job(client, users, model.id, fake_ollama, content, reply, "commercial_invoice")
    assert job["form"]["total_amount"] == "1250.5" and job["form"]["items"][0]["description"] == "Printer"
    url = f"/api/v2/ops/commercial-invoices?source_doc_job_id={job['id']}"
    body = {**job["form"], "contract_id": contract.id}
    assert client.post(url, json=body, headers=users["h"]["ops"]).status_code == 422  # chưa duyệt
    approved = _approve(client, job["id"], users["h"]["ops"]).json()
    assert approved["create_hint"]["url"] == url
    r = client.post(url, json=body, headers=users["h"]["ops"])
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["source_doc_job_id"] == job["id"] and inv["total_amount"] == "1250.50"
    assert inv["items"][0]["amount"] == "1250.50" and inv["invoice_date"] == "2026-11-05"
    [linked] = logs("doc_job.linked", job["id"])
    assert linked.after["linked_entity_type"] == "commercial_invoice" and linked.after["overrides"] == {}
    assert client.post(url, json={**body, "invoice_no": "INV-02"}, headers=users["h"]["ops"]).status_code == 409

    with SessionLocal() as db:
        events = db.scalars(select(SysEvent).where(SysEvent.entity_type == "doc_job").order_by(SysEvent.id)).all()
    assert [e.event_code for e in events] == ["doc.extracted", "doc.approved", "doc.linked"]
    assert all(e.status == "pending" and e.consumers == ["control", "assessment"] for e in events)
    data = events[0].payload["data"]
    assert set(data) == {
        "job_id",
        "doc_type",
        "status",
        "target_type",
        "ai_model_id",
        "model_code",
        "overall_confidence",
        "low_confidence_count",
        "missing_required_count",
    }
    assert "1250" not in str(events[0].payload) and "Shanghai" not in str(events[0].payload)  # không giá trị trích được
    assert events[2].payload["data"]["linked_entity_id"] == inv["id"]


def test_failed_and_rejected_doc_events(client, users, model, fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": "không phải json"}})
    failed = upload(client, users["h"]["ops"], model.id).json()["id"]
    fake_ollama.reply = answer(name=("CÔNG TY TNHH XUẤT NHẬP KHẨU MINH AN", 0.95), party_type=("customer", 0.95))
    rejected = upload(client, users["h"]["ops"], model.id).json()["id"]
    client.post(f"{JOBS}/{rejected}/reject", json={"reason": "Ảnh mờ"}, headers=users["h"]["ops"])
    with SessionLocal() as db:
        rows = db.execute(select(SysEvent.entity_id, SysEvent.event_code).order_by(SysEvent.id)).all()
    assert [tuple(r) for r in rows] == [
        (failed, "doc.failed"),
        (rejected, "doc.extracted"),
        (rejected, "doc.rejected"),
    ]
