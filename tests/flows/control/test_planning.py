"""Luồng 2 – lập kế hoạch + phân công tự động (Giai đoạn 11), PostgreSQL + Redis thật.

Chuỗi đầy đủ: Luồng 1 ghi sự kiện vào outbox → worker gửi vào Redis Stream của Luồng 2 → Luồng 2 xử lý (ghi
`sys_event_consumptions`) → phân công, phát EV-202 → worker gửi stream Luồng 1 → Luồng 1 đặt người phụ trách lô.
"""

from datetime import datetime, timedelta
from unittest import mock

import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings, get_settings
from app.core.db import SessionLocal
from app.flows.control import service as control_service
from app.flows.control.models import CtlAssignment, CtlContractPlan, CtlShipmentPlan
from app.flows.operations.models import OpsShipment
from app.shared.audit.models import SysAuditLog
from app.shared.events import consumer, outbox
from app.shared.events.models import SysEvent, SysEventConsumption

SHIPMENTS = "/api/v2/ops/shipments"
CTL = "/api/v2/control"
SLA = {
    "sla_code": "CLR-24H",
    "description": "Thông quan ≤ 24 giờ",
    "target_value": "24",
    "unit": "hour",
    "kpi_code": "KPI-CLR",
    "measure_level": "per_shipment",
}
ALL_SKILLS = {"CUSTOMS_CLEARANCE": 4, "DOCS": 3, "ROAD_TRANSPORT": 2}


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


def drain(url: str) -> None:
    """Chạy worker hàng `events` tới khi hết việc (gửi outbox ↔ nhận của các luồng)."""
    queue = Queue(outbox.EVENTS_QUEUE, connection=Redis.from_url(url))
    for _ in range(20):
        if queue.count == 0:
            return
        SimpleWorker([queue], connection=queue.connection).work(burst=True)


@pytest.fixture()
def world(client, redis_url, make_party, make_user, auth_headers):
    users = {
        "m1": make_user("ql.an", roles=["manager"]),
        "m2": make_user("ql.binh", roles=["manager"]),
        "creator": make_user("nv.tao", roles=["ops_staff"]),
        "s1": make_user("nv.cuong", roles=["ops_staff"]),
        "s2": make_user("nv.dung", roles=["ops_staff"]),
        "bm": make_user("kd.em", roles=["business_manager"]),
        "learner": make_user("hv.giang", roles=["learner"]),
    }
    h = {k: auth_headers(u.username) for k, u in users.items()}
    customer = make_party("Công ty Minh An", tax_code="0100000001")
    seller = make_party("Shanghai Export", party_type="foreign_seller", country_code="CN")
    return {"u": users, "h": h, "customer": customer, "seller": seller}


def profile(client, world, who: str, skills: dict, max_load: str = "40", available: bool = True):
    r = client.put(
        f"{CTL}/staff-profiles/{world['u'][who].id}",
        json={"skills": skills, "max_load_hours": max_load, "is_available": available},
        headers=world["h"]["m1"],
    )
    assert r.status_code == 200, r.text


def shipment(client, world, contract, **overrides) -> dict:
    body = {
        "contract_id": contract.id,
        "direction": "import",
        "transport_mode": "sea_fcl",
        "incoterm": "CIF",
        "shipper_id": world["seller"].id,
        "consignee_id": world["customer"].id,
        "origin_location": "Thượng Hải",
        "destination_location": "Hải Phòng",
        "required_delivery_at": "2026-12-31T00:00:00+07:00",
        **overrides,
    }
    r = client.post(SHIPMENTS, json=body, headers=world["h"]["creator"])
    assert r.status_code == 201, r.text
    return r.json()


def _sp(shipment_id: int) -> CtlShipmentPlan:
    with SessionLocal() as db:
        return db.scalar(select(CtlShipmentPlan).where(CtlShipmentPlan.shipment_id == shipment_id))


def _assignee_in_ops(shipment_id: int) -> int:
    with SessionLocal() as db:
        return db.get(OpsShipment, shipment_id).assignee_id


def _logs(action: str) -> list[SysAuditLog]:
    with SessionLocal() as db:
        return list(db.scalars(select(SysAuditLog).where(SysAuditLog.action == action).order_by(SysAuditLog.id)))


def _consumptions(consumer_name: str | None = None) -> list[SysEventConsumption]:
    with SessionLocal() as db:
        stmt = select(SysEventConsumption).order_by(SysEventConsumption.id)
        if consumer_name:
            stmt = stmt.where(SysEventConsumption.consumer == consumer_name)
        return list(db.scalars(stmt))


# --- EV-101 → kế hoạch hợp đồng ---------------------------------------------------------------------------------


def test_ev101_creates_draft_plan_with_kpi_targets_and_account_manager(client, world, make_contract):
    contract = make_contract(world["customer"].id, sla_terms=[SLA])
    drain(world and Settings().test_job_queue_url)
    with SessionLocal() as db:
        plan = db.scalar(select(CtlContractPlan).where(CtlContractPlan.contract_id == contract.id))
    assert (plan.status, plan.contract_no, plan.account_manager_id) == (
        "draft",
        contract.contract_no,
        world["u"]["m1"].id,
    )
    detail = client.get(f"{CTL}/contract-plans/{plan.id}", headers=world["h"]["m2"]).json()
    assert [(k["kpi_code"], k["target_value"], k["source"]) for k in detail["kpi_targets"]] == [
        ("KPI-CLR", "24.000", "contract_sla")
    ]
    assert detail["members"][0]["role_in_contract"] == "account_manager"
    [c] = _consumptions("control")
    assert (c.event_code, c.status, c.result["plan_id"]) == ("EV-101", "processed", plan.id)
    assert _logs("plan.create")[0].after["contract_no"] == contract.contract_no

    # kế hoạch thứ 2 → manager ít kế hoạch hơn
    other = make_contract(world["customer"].id)
    drain(Settings().test_job_queue_url)
    with SessionLocal() as db:
        plan2 = db.scalar(select(CtlContractPlan).where(CtlContractPlan.contract_id == other.id))
    assert plan2.account_manager_id == world["u"]["m2"].id


# --- EV-111 → bước từ quy trình mẫu + phân công tự động → EV-202 → Luồng 1 ------------------------------------


def test_ev111_generates_steps_and_auto_assigns_by_skill(client, world, make_contract, redis_url):
    profile(client, world, "s1", {"DOCS": 5})  # mức cao nhưng thiếu 2 năng lực → xếp sau người đủ năng lực
    profile(client, world, "s2", ALL_SKILLS)
    contract = make_contract(world["customer"].id)
    drain(redis_url)
    s = shipment(client, world, contract)
    assert s["assignee_id"] == world["u"]["creator"].id  # Luồng 1: người tạo, tới khi Luồng 2 phân công
    drain(redis_url)

    detail = client.get(f"{CTL}/shipment-plans/{s['id']}", headers=world["h"]["m1"]).json()
    assert (detail["process_code"], detail["process_version"]) == ("QT-NK-SEA-FCL", 1)
    tasks = detail["tasks"]
    assert [t["step_code"] for t in tasks] == [f"NK-{i:02d}" for i in range(1, 11)]
    s2 = world["u"]["s2"].id
    assert detail["assignee_id"] == s2 and all(t["assignee_id"] == s2 for t in tasks)
    # Điểm kiểm soát: người kiểm soát = account_manager (khác người thực hiện – BR-201)
    checkpoints = [t for t in tasks if t["is_checkpoint"]]
    assert [t["step_code"] for t in checkpoints] == ["NK-02", "NK-03", "NK-05", "NK-09"]
    assert {t["controller_id"] for t in checkpoints} == {world["u"]["m1"].id}
    # Thời gian kế hoạch cộng dồn giờ chuẩn
    starts = [datetime.fromisoformat(t["planned_start"].replace("Z", "+00:00")) for t in tasks]
    ends = [datetime.fromisoformat(t["planned_end"].replace("Z", "+00:00")) for t in tasks]
    assert (
        ends[0] - starts[0] == timedelta(hours=4)
        and starts[1] == ends[0]
        and ends[-1] - starts[0] == timedelta(hours=92)
    )

    [a] = detail["assignments"]
    assert (a["method"], a["assignee_id"], a["previous_assignee_id"], a["is_current"]) == ("auto", s2, None, True)
    assert a["score"]["required_skills"] == ["CUSTOMS_CLEARANCE", "DOCS", "ROAD_TRANSPORT"]
    assert a["score"]["candidates"][0]["user_id"] == s2 and a["score"]["candidates"][0]["skills_missing"] == []

    # EV-202 → Luồng 1 đã đổi người phụ trách lô
    assert _assignee_in_ops(s["id"]) == s2
    assert client.get(f"{SHIPMENTS}/{s['id']}", headers=world["h"]["m1"]).json()["assignee_id"] == s2
    [ops_log] = [log for log in _logs("shipment.assign") if log.after.get("source") == "EV-202"]
    assert (ops_log.before, ops_log.after["assignee_id"]) == ({"assignee_id": world["u"]["creator"].id}, s2)
    [change] = _logs("assignment.change")
    assert change.after["method"] == "auto" and change.after["assignee_id"] == s2
    codes = [(c.consumer, c.event_code, c.status) for c in _consumptions()]
    assert ("control", "EV-111", "processed") in codes and ("ops", "EV-202", "processed") in codes
    with SessionLocal() as db:
        [ev202] = db.scalars(select(SysEvent).where(SysEvent.event_code == "EV-202")).all()
    assert ev202.consumers == ["ops"] and ev202.status == "sent"


def test_workload_balances_and_respects_capacity(client, world, make_contract, redis_url):
    profile(client, world, "s1", ALL_SKILLS)
    profile(client, world, "s2", ALL_SKILLS)
    profile(client, world, "creator", ALL_SKILLS, max_load="10")  # 1 lô NK = 92 giờ → vượt sức chứa
    contract = make_contract(world["customer"].id)
    drain(redis_url)
    ids = [shipment(client, world, contract)["id"] for _ in range(3)]
    drain(redis_url)
    assignees = [_sp(i).assignee_id for i in ids]
    s1, s2 = world["u"]["s1"].id, world["u"]["s2"].id
    assert assignees[:2] == [s1, s2]  # cùng năng lực: lần lượt người tải thấp hơn
    assert assignees[2] in (s1, s2)  # creator luôn quá sức chứa
    load = {w["username"]: w for w in client.get(f"{CTL}/workload", headers=world["h"]["m1"]).json()}
    assert load["nv.cuong"]["open_tasks"] + load["nv.dung"]["open_tasks"] == 30
    assert load["nv.tao"]["open_tasks"] == 0 and load["nv.tao"]["max_load_hours"] == "10.00"


def test_unavailable_staff_skipped_and_none_available_means_unassigned(client, world, make_contract, redis_url):
    for who in ("s1", "s2", "creator"):
        profile(client, world, who, ALL_SKILLS, available=False)
    contract = make_contract(world["customer"].id)
    drain(redis_url)
    s = shipment(client, world, contract)
    drain(redis_url)
    sp = _sp(s["id"])
    assert sp.assignee_id is None and _assignee_in_ops(s["id"]) == world["u"]["creator"].id
    [log] = _logs("assignment.unassigned")
    assert "không có ops_staff phù hợp" in log.after["reason"]
    with SessionLocal() as db:
        assert db.scalars(select(SysEvent).where(SysEvent.event_code == "EV-202")).all() == []

    profile(client, world, "s1", ALL_SKILLS, available=True)
    r = client.post(f"{CTL}/assignments:auto", json={"shipment_id": s["id"]}, headers=world["h"]["m1"])
    assert r.status_code == 200 and r.json()["assignee_id"] == world["u"]["s1"].id
    drain(redis_url)
    assert _assignee_in_ops(s["id"]) == world["u"]["s1"].id


def test_template_selection_domestic_and_none(client, world, make_contract, redis_url):
    road = {"service_code": "ROAD_TRANSPORT", "params": {}}
    rate = {
        "rate_code": "VC-01",
        "service_code": "ROAD_TRANSPORT",
        "charge_name": "Cước vận chuyển",
        "basis": "per_shipment",
        "unit_price": "2000000",
        "currency": "VND",
        "tax_rate": "8",
    }
    contract = make_contract(world["customer"].id, service_scopes=[road], rates=[rate])
    drain(redis_url)
    dom = shipment(
        client,
        world,
        contract,
        direction="domestic",
        transport_mode="road",
        incoterm=None,
        shipper_id=world["customer"].id,
    )
    air = shipment(client, world, contract, transport_mode="air")
    drain(redis_url)
    assert (
        _sp(dom["id"]).process_code,
        len(client.get(f"{CTL}/shipment-plans/{dom['id']}", headers=world["h"]["m1"]).json()["tasks"]),
    ) == ("QT-VC-NOIDIA", 3)
    air_plan = client.get(f"{CTL}/shipment-plans/{air['id']}", headers=world["h"]["m1"]).json()
    assert air_plan["process_code"] is None and air_plan["tasks"] == []  # chưa có quy trình mẫu hàng không
    assert air_plan["assignee_id"] is not None  # vẫn phân công lô theo tải


# --- Không xử lý trùng (sys_event_consumptions) -----------------------------------------------------------------


def test_events_are_processed_once(client, world, make_contract, redis_url):
    contract = make_contract(world["customer"].id)
    shipment(client, world, contract)
    drain(redis_url)
    before = [(c.consumer, c.event_code, c.status) for c in _consumptions()]
    # Phát lại toàn bộ từ outbox + gửi lặp tin trong stream: không tạo thêm kế hoạch, phân công, EV-202
    assert set(consumer.replay("control").keys()) <= {"duplicate", "skipped"}
    with SessionLocal() as db:
        ev = db.scalar(select(SysEvent).where(SysEvent.event_code == "EV-111"))
    Redis.from_url(redis_url).xadd(
        "l3pl:events:control",
        {"event_id": str(ev.event_id), "event_code": "EV-111", "payload": __import__("json").dumps(ev.payload)},
    )
    assert consumer.consume("control") == {"duplicate": 1}
    assert [(c.consumer, c.event_code, c.status) for c in _consumptions()] == before
    with SessionLocal() as db:
        assert len(db.scalars(select(CtlContractPlan)).all()) == 1
        assert len(db.scalars(select(CtlShipmentPlan)).all()) == 1
        assert len(db.scalars(select(CtlAssignment)).all()) == 1
        assert len(db.scalars(select(SysEvent).where(SysEvent.event_code == "EV-202")).all()) == 1


def test_handler_error_is_retried_then_dead_without_blocking_others(client, world, make_contract, redis_url, caplog):
    calls = []
    real = control_service.on_contract_activated

    def flaky(db, envelope):
        calls.append(envelope["data"]["contract_id"])
        if len(calls) == 1:
            raise RuntimeError("CSDL bận")
        return real(db, envelope)

    with mock.patch.dict(consumer.HANDLERS["control"], {"EV-101": flaky}):
        contract = make_contract(world["customer"].id)
        drain(redis_url)
    [c] = _consumptions("control")
    assert (c.status, c.attempts) == ("processed", 1) and len(calls) == 2  # lần 2 (rq thử lại) thành công
    assert "CSDL bận" in caplog.text

    def broken(db, envelope):
        raise RuntimeError("lỗi cố định")

    with mock.patch.dict(consumer.HANDLERS["control"], {"EV-101": broken}):
        make_contract(world["customer"].id)
        drain(redis_url)
    dead = [c for c in _consumptions("control") if c.status == "dead"]
    assert len(dead) == 1 and dead[0].attempts == 3 and "lỗi cố định" in dead[0].last_error
    [log] = _logs("event.consume_failed")
    assert log.after["attempts"] == 3
    # sự kiện sau vẫn xử lý được
    shipment(client, world, contract)
    drain(redis_url)
    assert [c.status for c in _consumptions("control") if c.event_code == "EV-111"] == ["processed"]
    # xử lý lại sự kiện hết lượt
    assert consumer.replay("control", include_dead=True).get("processed") == 1


def test_replay_without_redis(client, world, make_contract, monkeypatch):
    monkeypatch.setenv("JOB_QUEUE_URL", "")
    get_settings.cache_clear()
    contract = make_contract(world["customer"].id)
    with SessionLocal() as db:
        assert db.scalar(select(CtlContractPlan)) is None  # chưa có hàng đợi: sự kiện chờ trong outbox
    from app.shared.events import cli

    assert cli.main(["replay", "control"]) == 0
    with SessionLocal() as db:
        assert db.scalar(select(CtlContractPlan)).contract_id == contract.id


# --- EV-112 → đóng việc -----------------------------------------------------------------------------------------


def test_cancelled_shipment_closes_tasks_and_frees_capacity(client, world, make_contract, redis_url):
    profile(client, world, "s1", ALL_SKILLS)
    contract = make_contract(world["customer"].id)
    drain(redis_url)
    s = shipment(client, world, contract)
    drain(redis_url)
    s1 = world["u"]["s1"].id
    assert _sp(s["id"]).assignee_id == s1
    r = client.post(f"{SHIPMENTS}/{s['id']}:cancel", json={"reason": "Khách hủy đơn"}, headers=world["h"]["s1"])
    assert r.status_code == 200, r.text
    drain(redis_url)
    sp = _sp(s["id"])
    assert (sp.status, sp.shipment_status) == ("closed", "cancelled")
    load = {w["user_id"]: w for w in client.get(f"{CTL}/workload", headers=world["h"]["m1"]).json()}
    assert load[s1]["open_tasks"] == 0
    assert (
        client.post(
            f"{CTL}/assignments",
            json={"shipment_id": s["id"], "assignee_id": world["u"]["s2"].id, "reason": "Điều chuyển"},
            headers=world["h"]["m1"],
        ).status_code
        == 409
    )


# --- Manager phân công tay (thay `:assign` của Luồng 1) ---------------------------------------------------------


def test_manager_reassign_publishes_ev202_and_keeps_history(client, world, make_contract, redis_url):
    profile(client, world, "s1", ALL_SKILLS)
    contract = make_contract(world["customer"].id)
    drain(redis_url)
    s = shipment(client, world, contract)
    drain(redis_url)
    s1, s2 = world["u"]["s1"].id, world["u"]["s2"].id
    body = {"shipment_id": s["id"], "assignee_id": s2, "reason": "Nhân viên cũ nghỉ phép"}
    for bad, status in (
        ({**body, "reason": ""}, 422),
        ({**body, "assignee_id": world["u"]["m2"].id}, 422),
        ({**body, "assignee_id": s1}, 409),
    ):
        assert client.post(f"{CTL}/assignments", json=bad, headers=world["h"]["m1"]).status_code == status
    for who in ("s1", "bm", "learner"):
        assert client.post(f"{CTL}/assignments", json=body, headers=world["h"][who]).status_code == 403
    r = client.post(f"{CTL}/assignments", json=body, headers=world["h"]["m1"])
    assert r.status_code == 201, r.text
    history = r.json()["assignments"]
    assert [(a["method"], a["assignee_id"], a["is_current"]) for a in history] == [
        ("auto", s1, False),
        ("manual", s2, True),
    ]
    assert history[1]["assigned_by"] == world["u"]["m1"].id and history[1]["previous_assignee_id"] == s1
    drain(redis_url)
    assert _assignee_in_ops(s["id"]) == s2
    manual = [log for log in _logs("assignment.change") if log.after["method"] == "manual"]
    assert manual[0].actor_id == world["u"]["m1"].id and manual[0].before == {"assignee_id": s1}


# --- Xem kế hoạch, phân quyền -----------------------------------------------------------------------------------


def test_views_and_rbac(client, world, make_contract, redis_url, make_user, auth_headers):
    profile(client, world, "s1", ALL_SKILLS)
    profile(client, world, "s2", {"DOCS": 1})
    contract = make_contract(world["customer"].id)
    other = make_contract(world["customer"].id)
    drain(redis_url)
    mine = shipment(client, world, contract)
    drain(redis_url)
    h = world["h"]
    # manager: mọi kế hoạch; ops_staff: kế hoạch của mình
    assert client.get(f"{CTL}/contract-plans", headers=h["m1"]).json()["total"] == 2
    assert client.get(f"{CTL}/contract-plans", headers=h["bm"]).json()["total"] == 2
    s1_plans = client.get(f"{CTL}/contract-plans", headers=h["s1"]).json()
    assert [p["contract_id"] for p in s1_plans["items"]] == [contract.id]
    assert client.get(f"{CTL}/contract-plans", headers=h["s2"]).json()["total"] == 0
    with SessionLocal() as db:
        other_plan = db.scalar(select(CtlContractPlan).where(CtlContractPlan.contract_id == other.id))
    assert client.get(f"{CTL}/contract-plans/{other_plan.id}", headers=h["s1"]).status_code == 404
    assert client.get(f"{CTL}/shipment-plans/{mine['id']}", headers=h["s1"]).status_code == 200
    assert client.get(f"{CTL}/shipment-plans/{mine['id']}", headers=h["s2"]).status_code == 404
    assert client.get(f"{CTL}/shipment-plans", headers=h["s2"]).json()["total"] == 0
    assert len(client.get(f"{CTL}/my-tasks", headers=h["s1"]).json()) == 10
    assert client.get(f"{CTL}/my-tasks", headers=h["s2"]).json() == []
    assert [a["assignee_id"] for a in client.get(f"{CTL}/assignments", headers=h["s1"]).json()] == [world["u"]["s1"].id]
    assert client.get(f"{CTL}/workload", headers=h["s1"]).status_code == 403
    assert client.put(f"{CTL}/staff-profiles/{world['u']['s1'].id}", json={}, headers=h["s1"]).status_code == 403
    for who in ("learner",):
        assert client.get(f"{CTL}/contract-plans", headers=h[who]).status_code == 403
    customer = make_user("kh.a", roles=["customer"], party_id=world["customer"].id, email="kh@a.vn")
    assert client.get(f"{CTL}/contract-plans", headers=auth_headers(customer.username)).status_code == 403
    assert client.get(f"{CTL}/contract-plans").status_code == 401
    assert client.get("/api/v2/md/process-templates", headers=h["s1"]).status_code == 200


def test_profile_validation_and_audit(client, world):
    uid = world["u"]["s1"].id
    for bad in ({"skills": {"DOCS": 6}}, {"skills": {"docs": 1}}, {"max_load_hours": "0"}):
        assert client.put(f"{CTL}/staff-profiles/{uid}", json=bad, headers=world["h"]["m1"]).status_code == 422
    assert (
        client.put(f"{CTL}/staff-profiles/{world['u']['learner'].id}", json={}, headers=world["h"]["m1"]).status_code
        == 404
    )
    profile(client, world, "s1", {"DOCS": 3}, max_load="35.5")
    [log] = _logs("staff_profile.update")
    assert log.after == {"skills": {"DOCS": 3}, "max_load_hours": "35.5", "is_available": True, "zalo_user_id": None}


def test_control_disabled_keeps_events_waiting(client, make_party, make_contract, monkeypatch, redis_url):
    """N6: tắt Luồng 2 – không có API `/control`, sự kiện cho Luồng 2 chờ trong outbox."""
    monkeypatch.setenv("ENABLED_FLOWS", "ops,learning,assessment")
    get_settings.cache_clear()
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        assert c.get(f"{CTL}/contract-plans").status_code == 404
    make_contract(make_party("Cty B", tax_code="0100000002").id)
    drain(redis_url)
    with SessionLocal() as db:
        ev = db.scalar(select(SysEvent).where(SysEvent.event_code == "EV-101"))
        assert db.scalar(select(CtlContractPlan)) is None
    assert ev.status == "pending" and ev.deliveries["control"]["reason"] == "consumer_disabled"


def test_database_guards(client, world, make_contract, redis_url):
    contract = make_contract(world["customer"].id)
    drain(redis_url)
    shipment(client, world, contract)
    drain(redis_url)
    with SessionLocal() as db:
        aid = db.scalar(select(CtlAssignment.id))
        cid = db.scalar(select(SysEventConsumption.id))
    for sql in (
        f"DELETE FROM ctl_assignments WHERE id = {aid}",
        f"UPDATE ctl_assignments SET assignee_id = NULL WHERE id = {aid}",
        f"DELETE FROM sys_event_consumptions WHERE id = {cid}",
    ):
        with SessionLocal() as db, pytest.raises(DBAPIError, match="RestrictViolation"):
            db.execute(text(sql))
            db.commit()
    with SessionLocal() as db:  # bỏ cờ is_current được
        db.execute(text(f"UPDATE ctl_assignments SET is_current = false WHERE id = {aid}"))
        db.commit()


def test_plan_members_added_by_manager_limit_auto_assignment(client, world, make_contract, redis_url):
    """BR-204: kế hoạch có thành viên thực hiện do manager thêm → chỉ phân công trong số đó (kể cả khi người khác giỏi
    hơn / rảnh hơn)."""
    from app.flows.control.models import CtlPlanMember

    profile(client, world, "s1", ALL_SKILLS)
    profile(client, world, "s2", {"DOCS": 1})
    contract = make_contract(world["customer"].id)
    drain(redis_url)
    with SessionLocal() as db:
        plan = db.scalar(select(CtlContractPlan).where(CtlContractPlan.contract_id == contract.id))
        db.add(
            CtlPlanMember(
                plan_id=plan.id,
                user_id=world["u"]["s2"].id,
                role_in_contract="customs_staff",
                added_by=world["u"]["m1"].id,
            )
        )
        db.commit()
    s = shipment(client, world, contract)
    drain(redis_url)
    sp = _sp(s["id"])
    assert sp.assignee_id == world["u"]["s2"].id
    with SessionLocal() as db:
        a = db.scalar(select(CtlAssignment).where(CtlAssignment.shipment_id == s["id"]))
    assert a.score["restricted_to_plan_members"] is True


def test_four_eyes_controller_differs_from_performer(client, world, make_contract, redis_url):
    """BR-201: account_manager kiêm ops_staff và được giao lô → người kiểm soát là manager khác."""
    from app.shared.identity.models import SysUserRole

    m1 = world["u"]["m1"].id
    with SessionLocal() as db:
        db.add(SysUserRole(user_id=m1, role="ops_staff"))
        db.commit()
    profile(client, world, "m1", ALL_SKILLS)
    for who in ("s1", "s2", "creator"):
        profile(client, world, who, ALL_SKILLS, available=False)
    contract = make_contract(world["customer"].id)
    drain(redis_url)
    s = shipment(client, world, contract)
    drain(redis_url)
    tasks = client.get(f"{CTL}/shipment-plans/{s['id']}", headers=world["h"]["m2"]).json()["tasks"]
    assert {t["assignee_id"] for t in tasks} == {m1}
    assert {t["controller_id"] for t in tasks if t["is_checkpoint"]} == {world["u"]["m2"].id}


def test_plan_handler_is_idempotent_per_contract(client, world):
    """Hai sự kiện EV-101 khác event_id cho cùng hợp đồng (phát lại thủ công) → một kế hoạch."""
    import uuid

    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_code": "EV-101",
        "mode": "production",
        "data": {
            "contract_id": 77,
            "contract_no": "HD-77",
            "customer_id": world["customer"].id,
            "service_scope": [],
            "sla": {},
            "valid_from": "2026-11-01",
            "valid_to": "2027-10-31",
        },
    }
    for _ in range(2):
        assert consumer.process("control", {**envelope, "event_id": str(uuid.uuid4())}) == "processed"
    with SessionLocal() as db:
        assert len(db.scalars(select(CtlContractPlan)).all()) == 1
    results = [c.result["created"] for c in _consumptions("control")]
    assert results == [True, False]
