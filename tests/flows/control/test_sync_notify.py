"""Giai đoạn 13: đồng bộ bước Luồng 2 ↔ Luồng 1 (EV-131/132/133), leo thang BR-205, cảnh báo qua email / Zalo –
PostgreSQL + Redis thật; Zalo OA giả lập bằng máy chủ HTTP trên 127.0.0.1."""

import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from rq.registry import FailedJobRegistry
from sqlalchemy import select, text

from app.core import jobs
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.timeutil import now_utc
from app.flows.control import tracking
from app.flows.control.models import CtlAlert, CtlAlertNotification, CtlShipmentTask
from app.flows.operations.models import OpsTask
from app.shared.audit.models import SysAuditLog
from app.shared.events import consumer, outbox
from app.shared.events.models import SysEvent
from app.shared.notifications import mail
from tests.flows.control.test_planning import ALL_SKILLS, CTL, SHIPMENTS, profile, shipment

OPS = "/api/v2/ops"


def drain_all(url: str) -> None:
    """Chạy worker cả hàng `default` (thông báo) và `events` (outbox ↔ các luồng) tới khi hết việc."""
    conn = Redis.from_url(url)
    queues = [Queue(jobs.QUEUE_NAME, connection=conn), Queue(outbox.EVENTS_QUEUE, connection=conn)]
    for _ in range(30):
        if all(q.count == 0 for q in queues):
            return
        SimpleWorker(queues, connection=conn).work(burst=True)


@pytest.fixture()
def setup(client, world, make_contract, redis_url):
    profile(client, world, "s1", ALL_SKILLS)
    with SessionLocal() as db:
        for who, email in (("m1", "an@cty.vn"), ("m2", "binh@cty.vn"), ("bm", "em@cty.vn")):
            db.execute(text("UPDATE sys_users SET email = :e WHERE id = :i"), {"e": email, "i": world["u"][who].id})
        db.commit()
    contract = make_contract(world["customer"].id)
    drain_all(redis_url)
    mail.OUTBOX.clear()
    return {"contract": contract, "url": redis_url}


def _new(client, world, setup) -> tuple[int, dict[str, dict]]:
    s = shipment(client, world, setup["contract"])
    drain_all(setup["url"])
    tasks = client.get(f"{OPS}/shipments/{s['id']}/tasks", headers=world["h"]["s1"]).json()
    return s["id"], {t["step_code"]: t for t in tasks}


def _pairs(shipment_id: int) -> list[tuple]:
    """(step, trạng thái L1, trạng thái L2, revision L1, revision L2, người L1, người L2)"""
    with SessionLocal() as db:
        ops = {t.ctl_task_id: t for t in db.scalars(select(OpsTask).where(OpsTask.shipment_id == shipment_id))}
        ctl = {t.id: t for t in db.scalars(select(CtlShipmentTask).where(CtlShipmentTask.id.in_(list(ops))))}
    return [
        (
            o.step_code,
            o.status,
            ctl[k].status,
            o.revision,
            ctl[k].revision,
            o.assignee_id,
            ctl[k].assignee_id,
            o.pending_local,
        )
        for k, o in sorted(ops.items())
    ]


def _assert_in_sync(shipment_id: int) -> None:
    for step, s1, s2, r1, r2, a1, a2, pending in _pairs(shipment_id):
        assert (s1, r1, a1, pending) == (s2, r2, a2, False), step


def _codes(*codes: str) -> list[str]:
    with SessionLocal() as db:
        return [
            e.event_code
            for e in db.scalars(select(SysEvent).where(SysEvent.event_code.in_(codes)).order_by(SysEvent.id))
        ]


def _logs(action: str) -> list[SysAuditLog]:
    with SessionLocal() as db:
        return list(db.scalars(select(SysAuditLog).where(SysAuditLog.action == action).order_by(SysAuditLog.id)))


# --- Đồng bộ bước --------------------------------------------------------------------------------------------


def test_tasks_created_in_ops_and_l2_changes_flow_to_l1_with_ev13x(client, world, setup):
    sid, tasks = _new(client, world, setup)
    assert [t["step_code"] for t in tasks.values()] == [f"NK-{i:02d}" for i in range(1, 11)]
    assert {t["revision"] for t in tasks.values()} == {1} and {t["assignee_id"] for t in tasks.values()} == {
        world["u"]["s1"].id
    }
    _assert_in_sync(sid)
    nk1 = tasks["NK-01"]["ctl_task_id"]
    h = world["h"]["s1"]
    assert client.post(f"{CTL}/tasks/{nk1}:start", headers=h).status_code == 200
    assert client.post(f"{CTL}/tasks/{nk1}:block", json={"reason": "Thiếu B/L gốc"}, headers=h).status_code == 200
    assert client.post(f"{CTL}/tasks/{nk1}:unblock", headers=h).status_code == 200
    assert client.post(f"{CTL}/tasks/{nk1}:complete", headers=h).status_code == 200
    drain_all(setup["url"])
    _assert_in_sync(sid)
    # Luồng 1 phát EV-131/133/unblocked/132 (chuyển tiếp thay đổi của Luồng 2); Luồng 2 không xử lý lại (không vòng lặp)
    assert _codes("EV-131", "EV-132", "EV-133", "EV-135") == [
        "EV-131",
        "EV-133",
        "EV-135",
        "EV-132",
    ]
    with SessionLocal() as db:
        ev132 = db.scalar(select(SysEvent).where(SysEvent.event_code == "EV-132"))
    assert ev132.payload["data"]["origin"] == "control" and ev132.payload["data"]["sequence_ok"] is True
    assert ev132.consumers == ["control", "assessment"]
    assert len(_codes("EV-215")) == 10 + 4  # tạo 10 bước + 4 thao tác
    ops_nk1 = [t for t in client.get(f"{OPS}/shipments/{sid}/tasks", headers=h).json() if t["step_code"] == "NK-01"]
    assert ops_nk1[0]["status"] == "done" and ops_nk1[0]["revision"] == 5 and ops_nk1[0]["actual_end"]


def test_l1_changes_flow_to_l2(client, world, setup):
    sid, tasks = _new(client, world, setup)
    h = world["h"]["s1"]
    ops1 = tasks["NK-01"]["id"]
    r = client.post(f"{OPS}/tasks/{ops1}:start", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "in_progress" and r.json()["pending_local"] is True
    assert client.post(f"{OPS}/tasks/{tasks['NK-02']['id']}:start", headers=h).status_code == 409  # phụ thuộc
    drain_all(setup["url"])
    _assert_in_sync(sid)
    with SessionLocal() as db:
        ctl1 = db.get(CtlShipmentTask, tasks["NK-01"]["ctl_task_id"])
    assert ctl1.status == "in_progress" and ctl1.revision == 2
    assert _codes("EV-211") == []  # Luồng 4 đã nhận EV-131 – Luồng 2 không phát trùng
    [log] = _logs("task.start")
    assert log.after["source"] == "ops" and log.actor_id == world["u"]["s1"].id

    client.post(f"{OPS}/tasks/{ops1}:block", json={"reason": "Cảng ùn tắc"}, headers=h)
    drain_all(setup["url"])
    _assert_in_sync(sid)
    with SessionLocal() as db:
        [alert] = db.scalars(select(CtlAlert)).all()
    assert alert.alert_type == "task_blocked" and "Cảng ùn tắc" in alert.message
    client.post(f"{OPS}/tasks/{ops1}:unblock", headers=h)
    client.post(f"{OPS}/tasks/{ops1}:complete", headers=h)
    drain_all(setup["url"])
    _assert_in_sync(sid)
    with SessionLocal() as db:
        assert db.get(CtlShipmentTask, tasks["NK-01"]["ctl_task_id"]).status == "done"
        assert db.get(CtlAlert, alert.id).status == "resolved"


def test_concurrent_changes_converge_to_l2_state(client, world, setup):
    """Luồng 1 bắt đầu bước, cùng lúc Luồng 2 báo chặn → xung đột: Luồng 1 quay về trạng thái của Luồng 2, cảnh báo
    manager, nhật ký."""
    sid, tasks = _new(client, world, setup)
    h = world["h"]["s1"]
    assert client.post(f"{OPS}/tasks/{tasks['NK-01']['id']}:start", headers=h).status_code == 200  # chưa gửi đi
    r = client.post(
        f"{CTL}/tasks/{tasks['NK-01']['ctl_task_id']}:block", json={"reason": "Sai tên hàng"}, headers=world["h"]["m1"]
    )
    assert r.status_code == 200
    drain_all(setup["url"])
    _assert_in_sync(sid)
    [(step, s1, *_)] = [p for p in _pairs(sid) if p[0] == "NK-01"]
    assert s1 == "blocked"
    [conflict] = _logs("task.sync_conflict")
    assert conflict.after["ops_action"] == "start"
    with SessionLocal() as db:
        types = sorted(a.alert_type for a in db.scalars(select(CtlAlert)))
    assert types == ["task_blocked", "task_sync_conflict"]


def test_stale_sync_ignored_and_permissions(client, world, setup):
    sid, tasks = _new(client, world, setup)
    ops1 = tasks["NK-01"]
    with SessionLocal() as db:
        ev = db.scalar(
            select(SysEvent).where(SysEvent.event_code == "EV-215", SysEvent.entity_id == ops1["ctl_task_id"])
        )
    stale = {
        **ev.payload,
        "event_id": "00000000-0000-0000-0000-000000000001",
        "data": {**ev.payload["data"], "revision": 0, "status": "done"},
    }
    assert consumer.process("ops", stale) == "processed"
    with SessionLocal() as db:
        assert db.get(OpsTask, ops1["id"]).status == "open"  # sự kiện cũ hơn bị bỏ qua
    for who in ("s2", "m1"):
        assert client.post(f"{OPS}/tasks/{ops1['id']}:start", headers=world["h"][who]).status_code == 403
    assert client.get(f"{OPS}/shipments/{sid}/tasks", headers=world["h"]["learner"]).status_code == 403
    r = client.post(f"{SHIPMENTS}/{sid}:cancel", json={"reason": "Khách hủy"}, headers=world["h"]["s1"])
    assert r.status_code == 200
    drain_all(setup["url"])
    assert {p[1] for p in _pairs(sid)} == {"closed"} and {p[2] for p in _pairs(sid)} == {"closed"}
    assert client.post(f"{OPS}/tasks/{ops1['id']}:start", headers=world["h"]["s1"]).status_code == 409


def test_reassign_updates_l1_tasks(client, world, setup):
    sid, _ = _new(client, world, setup)
    s2 = world["u"]["s2"].id
    body = {"shipment_id": sid, "assignee_id": s2, "reason": "Điều chuyển"}
    assert client.post(f"{CTL}/assignments", json=body, headers=world["h"]["m1"]).status_code == 201
    drain_all(setup["url"])
    _assert_in_sync(sid)
    assert {p[5] for p in _pairs(sid)} == {s2}
    assert client.get(f"{SHIPMENTS}/{sid}", headers=world["h"]["m1"]).json()["assignee_id"] == s2


# --- BR-205 leo thang ----------------------------------------------------------------------------------------


def test_unresolved_alert_escalates_to_business_manager_after_2_hours(client, world, setup):
    sid, tasks = _new(client, world, setup)
    client.post(
        f"{CTL}/tasks/{tasks['NK-01']['ctl_task_id']}:block", json={"reason": "Chờ giấy phép"}, headers=world["h"]["s1"]
    )
    drain_all(setup["url"])
    assert [(m.to, "critical" in m.subject) for m in mail.OUTBOX] == [("an@cty.vn", True)]  # account_manager
    with SessionLocal() as db:
        alert = db.scalar(select(CtlAlert))
    h = world["h"]
    assert client.post(f"{CTL}/alerts/{alert.id}:acknowledge", headers=h["bm"]).status_code == 403  # chưa leo thang
    with SessionLocal() as db:
        assert tracking.escalate(db, now_utc() + timedelta(hours=1)) == 0
        db.commit()
    with SessionLocal() as db:
        stats = tracking.scan(db, now_utc() + timedelta(hours=2, minutes=1))
    assert stats["escalated"] == 1
    with SessionLocal() as db:
        assert tracking.scan(db, now_utc() + timedelta(hours=5))["escalated"] == 0  # một lần
    drain_all(setup["url"])
    [log] = [entry for entry in _logs("alert.escalate") if entry.after.get("kind") == "business_manager"]
    assert log.entity_id == alert.id and log.after["rule"] == "BR-205" and log.after["unresolved_hours"] >= 2
    assert _codes("EV-222") == ["EV-222"]
    escalated = [m for m in mail.OUTBOX if m.to == "em@cty.vn"]
    assert len(escalated) == 1 and escalated[0].subject.startswith("[LEO THANG]")
    [mine] = client.get(f"{CTL}/alerts", params={"mine": True}, headers=h["bm"]).json()
    assert mine["escalated_to_role"] == "business_manager" and mine["escalated_at"]
    assert client.post(f"{CTL}/alerts/{alert.id}:acknowledge", headers=h["bm"]).status_code == 200
    r = client.post(f"{CTL}/alerts/{alert.id}:resolve", json={"note": "Đã có giấy phép"}, headers=h["bm"])
    assert r.status_code == 200 and r.json()["resolved_by"] == world["u"]["bm"].id


def test_resolved_alert_is_not_escalated(client, world, setup):
    _, tasks = _new(client, world, setup)
    nk1 = tasks["NK-01"]["ctl_task_id"]
    client.post(f"{CTL}/tasks/{nk1}:block", json={"reason": "Chờ"}, headers=world["h"]["s1"])
    client.post(f"{CTL}/tasks/{nk1}:unblock", headers=world["h"]["s1"])  # tự đóng
    with SessionLocal() as db:
        assert tracking.scan(db, now_utc() + timedelta(hours=3))["escalated"] == 0


# --- Gửi qua email / Zalo, thử lại -----------------------------------------------------------------------------


class FakeZalo:
    def __init__(self) -> None:
        self.requests: list[tuple[dict, dict]] = []
        self.errors_before_ok = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append((dict(self.headers), body))
                failing = fake.errors_before_ok < 0 or len(fake.requests) <= fake.errors_before_ok
                payload = {"error": -216, "message": "Access token invalid"} if failing else {"error": 0}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v3.0/oa/message/cs"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake_zalo(monkeypatch):
    fake = FakeZalo()
    monkeypatch.setenv("ZALO_TOKEN", "zalo-test-token-bi-mat")
    monkeypatch.setenv("ZALO_API_URL", fake.url)
    get_settings.cache_clear()
    yield fake
    fake.close()


def _block_first(client, world, setup) -> int:
    _, tasks = _new(client, world, setup)
    client.post(
        f"{CTL}/tasks/{tasks['NK-01']['ctl_task_id']}:block",
        json={"reason": "Lỗi cổng VNACCS"},
        headers=world["h"]["s1"],
    )
    with SessionLocal() as db:
        return db.scalar(select(CtlAlert.id))


def _notifications(alert_id: int) -> list[CtlAlertNotification]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(CtlAlertNotification)
                .where(CtlAlertNotification.alert_id == alert_id)
                .order_by(CtlAlertNotification.id)
            )
        )


def test_zalo_sent_when_configured_token_not_leaked(client, world, setup, fake_zalo, caplog):
    profile(client, world, "m1", {}, available=True)
    client.put(
        f"{CTL}/staff-profiles/{world['u']['m1'].id}", json={"zalo_user_id": "8899001122"}, headers=world["h"]["m1"]
    )
    alert_id = _block_first(client, world, setup)
    raw = b"".join(
        v
        for k in Redis.from_url(setup["url"]).keys("rq:job:*")
        for v in Redis.from_url(setup["url"]).hgetall(k).values()
    )
    assert b"zalo-test-token" not in raw  # hàng đợi chỉ mang id
    drain_all(setup["url"])
    rows = _notifications(alert_id)
    assert sorted((r.channel, r.status, r.address) for r in rows) == [
        ("email", "sent", "an@cty.vn"),
        ("zalo", "sent", "8899001122"),
    ]
    headers, body = fake_zalo.requests[0]
    headers = {k.lower(): v for k, v in headers.items()}
    assert headers["access_token"] == "zalo-test-token-bi-mat" and body["recipient"] == {"user_id": "8899001122"}
    assert "Lỗi cổng VNACCS" in body["message"]["text"]
    assert "zalo-test-token" not in caplog.text


def test_zalo_errors_retry_then_succeed(client, world, setup, fake_zalo):
    client.put(
        f"{CTL}/staff-profiles/{world['u']['m1'].id}", json={"zalo_user_id": "8899001122"}, headers=world["h"]["m1"]
    )
    fake_zalo.errors_before_ok = 2
    alert_id = _block_first(client, world, setup)
    drain_all(setup["url"])
    [zalo_row] = [r for r in _notifications(alert_id) if r.channel == "zalo"]
    assert (zalo_row.status, zalo_row.attempts) == ("sent", 3) and len(fake_zalo.requests) == 3


def test_final_failure_is_audited_and_kept_in_failed_registry(client, world, setup, caplog):
    def broken(m, settings=None):
        raise mail.MailError("Gửi thư thất bại: SMTPServerDisconnected")

    with mock.patch.object(mail, "send", broken):
        alert_id = _block_first(client, world, setup)
        drain_all(setup["url"])
    [row] = _notifications(alert_id)
    assert (row.status, row.attempts) == ("failed", 3) and "SMTPServerDisconnected" in row.last_error
    [log] = _logs("alert.notify_failed")
    assert log.entity_id == alert_id and log.after["attempts"] == 3
    queue = Queue(jobs.QUEUE_NAME, connection=Redis.from_url(setup["url"]))
    assert FailedJobRegistry(queue=queue).count == 1
    assert "hết lượt" in caplog.text


def test_without_queue_sends_inline(client, world, make_contract, monkeypatch):
    monkeypatch.setenv("JOB_QUEUE_URL", "")
    get_settings.cache_clear()
    with SessionLocal() as db:
        db.execute(text("UPDATE sys_users SET email = 'an@cty.vn' WHERE id = :i"), {"i": world["u"]["m1"].id})
        db.commit()
    contract = make_contract(world["customer"].id)
    from app.shared.events import cli

    cli.main(["replay", "control"])
    s = shipment(client, world, contract)
    cli.main(["replay", "control"])
    cli.main(["replay", "ops"])
    mail.OUTBOX.clear()
    with SessionLocal() as db:
        task_id = db.scalar(select(CtlShipmentTask.id).order_by(CtlShipmentTask.seq).limit(1))
    who = next(
        k
        for k, u in world["u"].items()
        if u.id == client.get(f"{CTL}/shipment-plans/{s['id']}", headers=world["h"]["m1"]).json()["assignee_id"]
    )
    r = client.post(f"{CTL}/tasks/{task_id}:block", json={"reason": "Mất điện"}, headers=world["h"][who])
    assert r.status_code == 200
    assert [m.to for m in mail.OUTBOX] == ["an@cty.vn"]  # gửi ngay trong tiến trình (không có hàng đợi)


def test_resync_repairs_l1_after_missed_events(client, world, setup):
    """L1 lệch (vd. sự kiện bị dead / sửa tay) → manager :resync → L1 về đúng trạng thái của L2."""
    sid, tasks = _new(client, world, setup)
    client.post(f"{CTL}/tasks/{tasks['NK-01']['ctl_task_id']}:start", headers=world["h"]["s1"])
    drain_all(setup["url"])
    with SessionLocal() as db:  # giả lập lệch
        db.execute(text("UPDATE ops_tasks SET status = 'open', actual_start = NULL WHERE shipment_id = :s"), {"s": sid})
        db.commit()
    assert any(p[1] != p[2] for p in _pairs(sid))
    assert client.post(f"{CTL}/shipment-plans/{sid}:resync", headers=world["h"]["s1"]).status_code == 403
    r = client.post(f"{CTL}/shipment-plans/{sid}:resync", headers=world["h"]["m1"])
    assert r.status_code == 200 and r.json() == {"tasks": 10}
    drain_all(setup["url"])
    _assert_in_sync(sid)
    assert _logs("task.resync")[0].actor_id == world["u"]["m1"].id


def test_conflict_resync_repairs_stale_l1(client, world, setup):
    """Luồng 1 lệch (lỡ sự kiện) rồi thao tác tiếp → Luồng 2 từ chối (xung đột) và gửi trạng thái chuẩn → Luồng 1 về
    đúng, không còn pending_local."""
    sid, tasks = _new(client, world, setup)
    with SessionLocal() as db:  # Luồng 1 tưởng NK-01 đang làm (Luồng 2 vẫn open)
        db.execute(
            text("UPDATE ops_tasks SET status = 'in_progress', actual_start = now() WHERE id = :i"),
            {"i": tasks["NK-01"]["id"]},
        )
        db.commit()
    r = client.post(f"{OPS}/tasks/{tasks['NK-01']['id']}:complete", headers=world["h"]["s1"])
    assert r.status_code == 200 and r.json()["status"] == "done"
    drain_all(setup["url"])
    _assert_in_sync(sid)
    assert [p[1] for p in _pairs(sid) if p[0] == "NK-01"] == ["open"]
    [log] = _logs("ops_task.sync_conflict")
    assert log.before == {"status": "done"} and log.after["status"] == "open"
