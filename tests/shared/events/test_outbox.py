"""Outbox sự kiện `sys_events` (Giai đoạn 10; 01 §4, N5, N6) – PostgreSQL + Redis thật.

Sự kiện ghi cùng giao dịch với thay đổi; sau commit xếp tác vụ (hàng rq `events`) chỉ mang id; worker gửi phong bì vào
Redis Stream của Luồng 2 (`control`), Luồng 4 (`assessment`); lỗi → log + thử lại như Giai đoạn 7.
"""

import json
from unittest import mock

import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from rq.registry import FailedJobRegistry
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings, get_settings
from app.core.db import SessionLocal
from app.shared.audit.models import SysAuditLog
from app.shared.events import outbox
from app.shared.events.models import SysEvent


@pytest.fixture()
def redis_url(client, monkeypatch) -> str:
    url = Settings().test_job_queue_url
    conn = Redis.from_url(url)
    if conn.connection_pool.connection_kwargs.get("db", 0) == 0:
        pytest.fail("TEST_JOB_QUEUE_URL không được dùng CSDL Redis số 0")
    try:
        conn.ping()
    except Exception as exc:
        pytest.fail(f"Không kết nối được Redis cho test ({url}): docker compose up -d redis – {exc}")
    conn.flushdb()
    monkeypatch.setenv("JOB_QUEUE_URL", url)
    monkeypatch.setenv("JOB_MAX_RETRIES", "2")
    monkeypatch.setenv("JOB_RETRY_INTERVALS", "0")
    get_settings.cache_clear()
    yield url
    conn.flushdb()


def _queue(url: str) -> Queue:
    return Queue(outbox.EVENTS_QUEUE, connection=Redis.from_url(url))


def _work(url: str) -> None:
    queue = _queue(url)
    SimpleWorker([queue], connection=queue.connection).work(burst=True)


def _events(code: str | None = None) -> list[SysEvent]:
    with SessionLocal() as db:
        stmt = select(SysEvent).order_by(SysEvent.id)
        if code:
            stmt = stmt.where(SysEvent.event_code == code)
        return list(db.scalars(stmt))


def _stream(url: str, consumer: str) -> list[dict]:
    entries = Redis.from_url(url).xrange(f"l3pl:events:{consumer}")
    return [{k.decode(): v.decode() for k, v in fields.items()} for _, fields in entries]


def test_event_written_in_same_transaction_and_matches_audit(client, make_party, make_contract):
    party = make_party("Cty A", tax_code="0100000001")
    contract = make_contract(party.id)  # kích hoạt → EV-101
    [ev] = _events("EV-101")
    assert (ev.entity_type, ev.entity_id, ev.status, ev.consumers) == (
        "service_contract",
        contract.id,
        "pending",  # chưa có JOB_QUEUE_URL: chờ trong outbox
        ["control", "assessment"],
    )
    with SessionLocal() as db:
        log = db.scalar(select(SysAuditLog).where(SysAuditLog.action == "event.EV-101"))
    assert ev.payload == log.after and str(ev.event_id) == ev.payload["event_id"]
    assert ev.payload["data"]["contract_no"] == contract.contract_no


def test_rollback_leaves_no_event(client, make_party, monkeypatch):
    from app.flows.operations import service as ops_service
    from app.flows.operations.schemas import ContractCreate
    from app.shared.events import publisher

    party = make_party("Cty A", tax_code="0100000001")
    with SessionLocal() as db:
        contract = ops_service.create_contract(
            db, ContractCreate(customer_id=party.id, valid_from="2026-11-01", valid_to="2027-10-31"), actor_id=0
        )
        contract_id = contract.id
        from app.shared.events.contracts import DocFailed

        publisher.publish(
            db,
            DocFailed(
                job_id=1, doc_type="x", status="failed", target_type=None, ai_model_id=1, model_code="m", attempts=1
            ),
            "doc_job",
            1,
            None,
        )
        db.rollback()
        assert db.info.get(publisher.PENDING_KEY) is None
    assert _events() == [] and contract_id


def test_queue_carries_only_ids_and_worker_delivers_to_flows_2_and_4(client, redis_url, make_party, make_contract):
    party = make_party("Cty A", tax_code="0100000001")
    contract = make_contract(party.id)
    [ev] = _events("EV-101")
    [job] = _queue(redis_url).jobs
    assert job.func_name == "app.shared.events.outbox.dispatch_events" and job.args == ([ev.id],)
    raw = b"".join(
        v for k in Redis.from_url(redis_url).keys("rq:job:*") for v in Redis.from_url(redis_url).hgetall(k).values()
    )
    assert contract.contract_no.encode() not in raw  # hàng đợi không chứa nội dung sự kiện

    _work(redis_url)
    [ev] = _events("EV-101")
    assert ev.status == "sent" and ev.dispatched_at and ev.attempts == 1
    assert {c: d["status"] for c, d in ev.deliveries.items()} == {"control": "sent", "assessment": "sent"}
    for consumer in ("control", "assessment"):
        [entry] = _stream(redis_url, consumer)
        assert entry["event_id"] == str(ev.event_id) and entry["event_code"] == "EV-101"
        assert json.loads(entry["payload"]) == ev.payload

    # Gửi lặp (rq "ít nhất một lần") – không gửi lại sự kiện đã sent (N5)
    outbox.dispatch_events([ev.id])
    assert len(_stream(redis_url, "control")) == 1


def test_disabled_flow_keeps_event_pending_until_enabled(client, redis_url, make_party, make_contract, monkeypatch):
    """N6: tắt Luồng 4 – Luồng 1 vẫn chạy, sự kiện cho Luồng 4 chờ trong outbox; bật lại → gửi."""
    monkeypatch.setenv("ENABLED_FLOWS", "ops,control")
    get_settings.cache_clear()
    make_contract(make_party("Cty A", tax_code="0100000001").id)
    _work(redis_url)
    [ev] = _events("EV-101")
    assert ev.status == "pending" and ev.deliveries["control"]["status"] == "sent"
    assert ev.deliveries["assessment"] == {"status": "pending", "reason": "consumer_disabled"}
    assert _stream(redis_url, "assessment") == []

    monkeypatch.setenv("ENABLED_FLOWS", "ops,control,learning,assessment")
    get_settings.cache_clear()
    assert outbox.schedule_pending() == 1
    _work(redis_url)
    [ev] = _events("EV-101")
    assert (
        ev.status == "sent" and len(_stream(redis_url, "control")) == 1 and len(_stream(redis_url, "assessment")) == 1
    )


def test_delivery_error_is_logged_and_retried_like_phase_7(client, redis_url, make_party, make_contract, caplog):
    calls = []
    real = Redis.xadd

    def flaky(self, name, fields, *args, **kwargs):
        if not str(name).startswith("l3pl:events:"):  # rq cũng dùng XADD (kết quả job)
            return real(self, name, fields, *args, **kwargs)
        calls.append(name)
        if len(calls) <= 2:
            from redis.exceptions import ConnectionError

            raise ConnectionError("Redis bận")
        return real(self, name, fields, *args, **kwargs)

    make_contract(make_party("Cty A", tax_code="0100000001").id)
    with mock.patch.object(Redis, "xadd", flaky):
        _work(redis_url)
    [ev] = _events("EV-101")
    assert ev.status == "sent" and ev.attempts == 2 and ev.last_error is None
    assert "Gửi sự kiện outbox" in caplog.text and "rq sẽ thử lại" in caplog.text


def test_final_failure_marks_failed_audits_and_can_be_resent(client, redis_url, make_party, make_contract, caplog):
    from redis.exceptions import ConnectionError

    real = Redis.xadd

    def broken(self, name, fields, *args, **kwargs):
        if str(name).startswith("l3pl:events:"):
            raise ConnectionError("Redis stream hỏng")
        return real(self, name, fields, *args, **kwargs)

    make_contract(make_party("Cty A", tax_code="0100000001").id)
    with mock.patch.object(Redis, "xadd", broken):
        _work(redis_url)
    [ev] = _events("EV-101")
    assert ev.status == "failed" and ev.attempts == 3 and "Redis stream hỏng" in ev.last_error
    assert "hết lượt" in caplog.text
    with SessionLocal() as db:
        [log] = db.scalars(select(SysAuditLog).where(SysAuditLog.action == "outbox.dispatch_failed")).all()
    assert log.entity_id == ev.id and log.after["attempts"] == 3
    assert FailedJobRegistry(queue=_queue(redis_url)).count == 1

    from app.shared.events import cli

    assert cli.main(["dispatch", "--failed"]) == 0
    _work(redis_url)
    assert _events("EV-101")[0].status == "sent"


def test_redis_down_at_enqueue_keeps_event_and_startup_resends(client, make_party, make_contract, monkeypatch, caplog):
    monkeypatch.setenv("JOB_QUEUE_URL", "redis://127.0.0.1:1/0")
    get_settings.cache_clear()
    make_contract(make_party("Cty A", tax_code="0100000001").id)  # request không lỗi
    assert _events("EV-101")[0].status == "pending"
    assert "sự kiện chờ trong outbox" in caplog.text


def test_shipment_and_doc_events_go_to_outbox(client, make_party, make_contract, make_user, auth_headers):
    party = make_party("Cty A", tax_code="0100000001")
    seller = make_party("Seller", party_type="foreign_seller", country_code="CN")
    contract = make_contract(party.id)
    make_user("nv.an", roles=["ops_staff"])
    h = auth_headers("nv.an")
    body = {
        "contract_id": contract.id,
        "direction": "import",
        "transport_mode": "sea_fcl",
        "incoterm": "CIF",
        "shipper_id": seller.id,
        "consignee_id": party.id,
        "origin_location": "Thượng Hải",
        "destination_location": "Hải Phòng",
        "required_delivery_at": "2026-12-31T00:00:00+07:00",
    }
    sid = client.post("/api/v2/ops/shipments", json=body, headers=h).json()["id"]
    assert client.post(f"/api/v2/ops/shipments/{sid}:submit", headers=h).status_code == 200
    codes = [e.event_code for e in _events()]
    assert codes == ["EV-101", "EV-111", "EV-112"]


def test_outbox_rows_cannot_be_changed_or_deleted(client, make_party, make_contract):
    make_contract(make_party("Cty A", tax_code="0100000001").id)
    [ev] = _events()
    for sql in (
        f"DELETE FROM sys_events WHERE id = {ev.id}",
        f"UPDATE sys_events SET payload = '{{}}' WHERE id = {ev.id}",
        f"UPDATE sys_events SET event_code = 'EV-999' WHERE id = {ev.id}",
    ):
        with SessionLocal() as db, pytest.raises(DBAPIError, match="RestrictViolation"):
            db.execute(text(sql))
            db.commit()
    with SessionLocal() as db:  # trạng thái gửi đổi được
        db.execute(text(f"UPDATE sys_events SET attempts = 1 WHERE id = {ev.id}"))
        db.commit()
