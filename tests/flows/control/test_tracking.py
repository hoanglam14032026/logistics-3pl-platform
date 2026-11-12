"""Luồng 2 – theo dõi thực thi, sự kiện ctl.*, cảnh báo chậm / lỗi (Giai đoạn 12), PostgreSQL + Redis thật."""

import json
from datetime import timedelta

import pytest
from redis import Redis
from rq import Queue
from rq.registry import ScheduledJobRegistry
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core import jobs
from app.core.db import SessionLocal
from app.core.timeutil import now_utc
from app.flows.control import tracking
from app.flows.control.models import CtlAlert, CtlShipmentTask
from app.shared.audit.models import SysAuditLog
from app.shared.events.models import SysEvent
from tests.flows.control.test_planning import ALL_SKILLS, CTL, SHIPMENTS, drain, profile, shipment


@pytest.fixture()
def setup(client, world, make_contract, redis_url):
    """s1 đủ năng lực → mọi lô giao s1; account_manager = m1."""
    profile(client, world, "s1", ALL_SKILLS)
    contract = make_contract(world["customer"].id)
    drain(redis_url)
    return {"contract": contract, "url": redis_url}


def _new(client, world, setup, **overrides) -> tuple[dict, dict[str, dict]]:
    s = shipment(client, world, setup["contract"], **overrides)
    drain(setup["url"])
    detail = client.get(f"{CTL}/tracking/shipments/{s['id']}", headers=world["h"]["m1"]).json()
    return detail, {t["step_code"]: t for t in detail["tasks"]}


def _act(client, world, task_id, action, who="s1", json=None):
    return client.post(f"{CTL}/tasks/{task_id}:{action}", json=json, headers=world["h"][who])


def _events(code: str) -> list[SysEvent]:
    with SessionLocal() as db:
        return list(db.scalars(select(SysEvent).where(SysEvent.event_code == code).order_by(SysEvent.id)))


def _alerts() -> list[CtlAlert]:
    with SessionLocal() as db:
        return list(db.scalars(select(CtlAlert).order_by(CtlAlert.id)))


def _logs(action: str) -> list[SysAuditLog]:
    with SessionLocal() as db:
        return list(db.scalars(select(SysAuditLog).where(SysAuditLog.action == action).order_by(SysAuditLog.id)))


def _scan(hours: float = 0) -> dict:
    with SessionLocal() as db:
        return tracking.scan(db, now_utc() + timedelta(hours=hours))


# --- Cập nhật thực tế + sự kiện bắt đầu / hoàn thành ------------------------------------------------------------


def test_start_progress_complete_with_events_and_history(client, world, setup):
    detail, t = _new(client, world, setup)
    assert detail["tracking"]["health"] == "on_track" and detail["tracking"]["progress_pct"] == 0
    nk1, nk2 = t["NK-01"]["id"], t["NK-02"]["id"]
    r = _act(client, world, nk2, "start")
    assert r.status_code == 409 and "NK-01" in r.json()["error"]["message"]  # bước phụ thuộc chưa xong
    assert _act(client, world, nk1, "complete").status_code == 409  # chưa bắt đầu
    assert _act(client, world, nk1, "start", who="s2").status_code == 404  # không liên quan lô
    started = _act(client, world, nk1, "start", json={"note": "Đã nhận bộ chứng từ"})
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress" and started.json()["actual_start"]
    assert _act(client, world, nk1, "start").status_code == 409
    r = client.patch(f"{CTL}/tasks/{nk1}", json={"progress_pct": 50}, headers=world["h"]["s1"])
    assert r.status_code == 200 and r.json()["progress_pct"] == 50
    assert client.patch(f"{CTL}/tasks/{nk1}", json={"progress_pct": 100}, headers=world["h"]["s1"]).status_code == 422
    track = client.get(f"{CTL}/tracking/shipments/{detail['shipment_id']}", headers=world["h"]["s1"])
    assert track.json()["tracking"]["progress_pct"] == round(4 * 0.5 / 92 * 100, 1)
    assert track.json()["tracking"]["current_step"] == "NK-01"

    done = _act(client, world, nk1, "complete", who="m1")  # manager cũng cập nhật được
    assert done.status_code == 200 and done.json()["status"] == "done" and done.json()["progress_pct"] == 100
    assert [(u["action"], u["from_status"], u["to_status"]) for u in done.json()["updates"]] == [
        ("start", "open", "in_progress"),
        ("progress", "in_progress", "in_progress"),
        ("complete", "in_progress", "done"),
    ]
    assert _act(client, world, nk2, "start").status_code == 200  # NK-01 xong → NK-02 bắt đầu được
    [ev_start, _] = _events("EV-211")
    assert ev_start.consumers == ["assessment"] and ev_start.payload["data"]["step_code"] == "NK-01"
    [ev_done] = _events("EV-212")
    assert ev_done.payload["data"]["delay_hours"] == 0.0 and ev_done.payload["data"]["task_id"] == nk1
    assert [log.action for log in _logs("task.start")] == ["task.start", "task.start"]
    # sự kiện Luồng 2 gửi tới Luồng 4 qua stream
    drain(setup["url"])
    stream = Redis.from_url(setup["url"]).xrange("l3pl:events:assessment")
    codes = [json.loads(f[b"payload"])["event_code"] for _, f in stream]
    assert codes.count("EV-211") == 2 and codes.count("EV-212") == 1


# --- Lỗi (bị chặn) → cảnh báo manager --------------------------------------------------------------------------


def test_block_raises_alert_and_unblock_resolves_it(client, world, setup):
    detail, t = _new(client, world, setup)
    nk1 = t["NK-01"]["id"]
    _act(client, world, nk1, "start")
    assert _act(client, world, nk1, "block", json={"reason": ""}).status_code == 422
    r = _act(client, world, nk1, "block", json={"reason": "Thiếu C/O bản gốc"})
    assert (
        r.status_code == 200 and r.json()["status"] == "blocked" and r.json()["blocked_reason"] == "Thiếu C/O bản gốc"
    )
    assert _act(client, world, nk1, "block", json={"reason": "Lần nữa"}).status_code == 409
    [alert] = _alerts()
    assert (alert.alert_type, alert.severity, alert.status, alert.recipient_id) == (
        "task_blocked",
        "critical",
        "open",
        world["u"]["m1"].id,  # account_manager của kế hoạch
    )
    assert "Thiếu C/O" in alert.message
    track = client.get(f"{CTL}/tracking/shipments/{detail['shipment_id']}", headers=world["h"]["m1"]).json()
    assert track["tracking"]["health"] == "blocked"
    [blocked] = _events("EV-213")
    assert blocked.payload["data"]["reason"] == "Thiếu C/O bản gốc"
    assert _events("EV-221")[0].payload["data"]["alert_id"] == alert.id
    assert _logs("alert.raise")[0].entity_id == alert.id

    r = _act(client, world, nk1, "unblock", json={"note": "Khách đã gửi C/O"})
    assert r.status_code == 200 and r.json()["status"] == "in_progress" and r.json()["blocked_reason"] is None
    [alert] = _alerts()
    assert alert.status == "resolved" and alert.resolved_by is None and alert.resolution_note == "Bước đã gỡ chặn"
    # chặn lại → cảnh báo mới (cảnh báo cũ đã xử lý)
    _act(client, world, nk1, "block", json={"reason": "Hải quan yêu cầu bổ sung"})
    assert [a.status for a in _alerts()] == ["resolved", "open"]


# --- Chậm → cảnh báo, nâng mức, tự đóng --------------------------------------------------------------------------


def test_overdue_scan_warns_escalates_and_resolves(client, world, setup):
    _, t = _new(client, world, setup)
    nk1 = t["NK-01"]["id"]
    assert _scan(0) == {"task_overdue": 0, "shipment_late_risk": 0, "resolved": 0, "escalated": 0}
    stats = _scan(5)  # NK-01 kế hoạch 4 giờ
    assert stats["task_overdue"] == 1
    [alert] = _alerts()
    assert (alert.alert_type, alert.severity, alert.task_id) == ("task_overdue", "warning", nk1)
    [delayed] = _events("EV-214")
    assert delayed.payload["data"]["overdue_hours"] >= 1.0
    assert _scan(6)["task_overdue"] == 0 and len(_alerts()) == 1  # không cảnh báo lặp

    stats = _scan(40)  # trễ ≥ 24 giờ → critical; các bước sau cũng quá hạn
    by_task = {a.task_id: a for a in _alerts()}
    assert by_task[nk1].severity == "critical"
    assert _logs("alert.escalate")[0].entity_id == by_task[nk1].id
    assert stats["task_overdue"] >= 1

    _act(client, world, nk1, "start")
    done = _act(client, world, nk1, "complete")
    assert done.status_code == 200
    with SessionLocal() as db:
        assert db.get(CtlAlert, by_task[nk1].id).status == "resolved"


def test_shipment_late_risk_vs_required_delivery(client, world, setup):
    soon = (now_utc() + timedelta(hours=10)).isoformat()
    late, _ = _new(client, world, setup, required_delivery_at=soon)
    ok, _ = _new(client, world, setup)  # hạn 31/12/2026
    assert late["tracking"]["health"] == "late" and late["tracking"]["late_vs_required_hours"] > 80
    assert ok["tracking"]["health"] == "on_track"
    stats = _scan(0)
    assert stats["shipment_late_risk"] == 1
    [alert] = [a for a in _alerts() if a.alert_type == "shipment_late_risk"]
    assert alert.shipment_id == late["shipment_id"] and alert.severity == "critical" and alert.task_id is None
    assert _scan(0)["shipment_late_risk"] == 0  # không lặp


# --- API cảnh báo cho manager -----------------------------------------------------------------------------------


def test_alert_api_acknowledge_resolve_and_rbac(client, world, setup):
    _, t = _new(client, world, setup)
    _act(client, world, t["NK-01"]["id"], "block", json={"reason": "Sai số container"})
    h = world["h"]
    [a] = client.get(f"{CTL}/alerts", params={"mine": True}, headers=h["m1"]).json()
    assert client.get(f"{CTL}/alerts", params={"mine": True}, headers=h["m2"]).json() == []  # gửi m1
    assert len(client.get(f"{CTL}/alerts", params={"status": "unresolved"}, headers=h["bm"]).json()) == 1
    for who in ("s1", "learner"):
        assert client.get(f"{CTL}/alerts", headers=h[who]).status_code == 403
    assert client.post(f"{CTL}/alerts/{a['id']}:acknowledge", headers=h["bm"]).status_code == 403
    ack = client.post(f"{CTL}/alerts/{a['id']}:acknowledge", headers=h["m1"])
    assert ack.status_code == 200 and ack.json()["status"] == "acknowledged"
    assert client.post(f"{CTL}/alerts/{a['id']}:acknowledge", headers=h["m1"]).status_code == 409
    assert client.post(f"{CTL}/alerts/{a['id']}:resolve", json={"note": ""}, headers=h["m2"]).status_code == 422
    res = client.post(f"{CTL}/alerts/{a['id']}:resolve", json={"note": "Đã liên hệ hãng tàu"}, headers=h["m2"])
    assert res.status_code == 200 and res.json()["resolved_by"] == world["u"]["m2"].id
    assert [log.actor_id for log in _logs("alert.acknowledge")] == [world["u"]["m1"].id]
    assert _logs("alert.resolve")[0].after == {"status": "resolved", "note": "Đã liên hệ hãng tàu"}
    assert client.post(f"{CTL}/alerts:scan", headers=h["m1"]).status_code == 200
    assert client.post(f"{CTL}/alerts:scan", headers=h["s1"]).status_code == 403


# --- Theo dõi: danh sách, kế hoạch hợp đồng, phân quyền --------------------------------------------------------


def test_tracking_views(client, world, setup):
    a, ta = _new(client, world, setup)
    b, _ = _new(client, world, setup)
    _act(client, world, ta["NK-01"]["id"], "start")
    _act(client, world, ta["NK-01"]["id"], "block", json={"reason": "Chờ khách"})
    h = world["h"]
    blocked = client.get(f"{CTL}/tracking/shipments", params={"health": "blocked"}, headers=h["m1"]).json()
    assert [s["shipment_id"] for s in blocked["items"]] == [a["shipment_id"]] and blocked["total"] == 1
    all_rows = client.get(f"{CTL}/tracking/shipments", headers=h["s1"]).json()
    assert all_rows["total"] == 2
    assert client.get(f"{CTL}/tracking/shipments", headers=h["s2"]).json()["total"] == 0
    assert client.get(f"{CTL}/tracking/shipments/{b['shipment_id']}", headers=h["s2"]).status_code == 404
    plan_id = a["plan_id"]
    agg = client.get(f"{CTL}/tracking/contract-plans/{plan_id}", headers=h["m1"]).json()
    assert agg["shipments"] == 2 and agg["by_health"] == {"blocked": 1, "on_track": 1} and agg["open_alerts"] == 1
    task = client.get(f"{CTL}/tasks/{ta['NK-01']['id']}", headers=h["s1"]).json()
    assert task["status"] == "blocked" and [u["action"] for u in task["updates"]] == ["start", "block"]
    assert client.get(f"{CTL}/tasks/{ta['NK-01']['id']}", headers=h["s2"]).status_code == 404
    workload = {w["username"]: w for w in client.get(f"{CTL}/workload", headers=h["m1"]).json()}
    assert workload["nv.cuong"]["open_tasks"] == 20  # in_progress/blocked vẫn tính vào tải


def test_cancel_closes_active_tasks(client, world, setup):
    a, ta = _new(client, world, setup)
    _act(client, world, ta["NK-01"]["id"], "start")
    _act(client, world, ta["NK-01"]["id"], "block", json={"reason": "Chờ khách"})
    r = client.post(f"{SHIPMENTS}/{a['shipment_id']}:cancel", json={"reason": "Khách hủy"}, headers=world["h"]["s1"])
    assert r.status_code == 200, r.text
    drain(setup["url"])
    detail = client.get(f"{CTL}/tracking/shipments/{a['shipment_id']}", headers=world["h"]["m1"]).json()
    assert detail["tracking"]["health"] == "closed" and {t["status"] for t in detail["tasks"]} == {"closed"}
    assert _act(client, world, ta["NK-02"]["id"], "start").status_code == 409


def test_scan_reschedules_itself_in_worker_queue(client, setup, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("CONTROL_SCAN_INTERVAL_SEC", "120")
    get_settings.cache_clear()
    assert tracking.schedule_scan() is True
    queue = Queue(jobs.QUEUE_NAME, connection=Redis.from_url(setup["url"]))
    assert tracking.SCAN_JOB_ID in ScheduledJobRegistry(queue=queue).get_job_ids()
    assert tracking.scheduled_scan() == {"task_overdue": 0, "shipment_late_risk": 0, "resolved": 0, "escalated": 0}
    monkeypatch.setenv("CONTROL_SCAN_INTERVAL_SEC", "0")
    get_settings.cache_clear()
    assert tracking.schedule_scan() is False


def test_database_guards(client, world, setup):
    _, t = _new(client, world, setup)
    nk1 = t["NK-01"]["id"]
    _act(client, world, nk1, "start")
    _act(client, world, nk1, "block", json={"reason": "Lỗi hệ thống hải quan"})
    with SessionLocal() as db:
        uid = db.scalar(select(text("id")).select_from(text("ctl_task_updates")).limit(1))
        aid = db.scalar(select(CtlAlert.id))
    for sql in (
        f"UPDATE ctl_task_updates SET note = 'sửa' WHERE id = {uid}",
        f"DELETE FROM ctl_task_updates WHERE id = {uid}",
        f"DELETE FROM ctl_alerts WHERE id = {aid}",
    ):
        with SessionLocal() as db, pytest.raises(DBAPIError, match="RestrictViolation"):
            db.execute(text(sql))
            db.commit()
    with SessionLocal() as db, pytest.raises(DBAPIError, match="blocked_has_reason"):
        db.execute(text(f"UPDATE ctl_shipment_tasks SET blocked_reason = NULL WHERE id = {nk1}"))
        db.commit()
    with SessionLocal() as db:
        assert db.get(CtlShipmentTask, nk1).status == "blocked"
