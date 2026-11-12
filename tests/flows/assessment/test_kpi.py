"""Giai đoạn 14 – Luồng 4: dữ kiện từ sự kiện + KPI vận hành + API báo cáo (PostgreSQL + Redis thật).

Phần 1 dựng sự kiện bằng tay (đưa thẳng vào bộ nhận `assessment`) để kiểm số liệu chính xác; phần 2 chạy chuỗi thật
Luồng 1 → outbox → Redis Stream `l3pl:events:assessment` → Luồng 4.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.timeutil import today_vn
from app.flows.assessment.models import AsmFactAlert, AsmFactShipment, AsmFactTask, AsmFactTaskBlock
from app.shared.events import consumer
from app.shared.events.models import SysEvent, SysEventConsumption
from tests.flows.control.test_planning import ALL_SKILLS, CTL, profile, shipment
from tests.flows.control.test_sync_notify import drain_all

ASM = "/api/v2/assessment"
OPS = "/api/v2/ops"
OCT = {"from": "2026-10-01", "to": "2026-10-31"}
U1, U2 = 9001, 9002


def _ev(code: str, data: dict, at: str, mode: str = "production") -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_code": code,
        "event_name": code,
        "schema_version": 1,
        "occurred_at": at,
        "mode": mode,
        "actor_id": None,
        "data": data,
    }


def _process(*envelopes: dict) -> list[str]:
    return [consumer.process("assessment", e) for e in envelopes]


def _synced(task_id, shipment_id, revision, status, at, assignee=U1, planned_end=None, actual_end=None, **kw):
    data = {
        "task_id": task_id,
        "shipment_id": shipment_id,
        "step_code": f"S-{task_id}",
        "seq": 1,
        "name": "Bước",
        "responsible_role": "ops_staff",
        "depends_on": [],
        "is_checkpoint": False,
        "planned_start": "2026-10-01T00:00:00Z",
        "planned_end": planned_end or "2026-10-30T00:00:00Z",
        "assignee_id": assignee,
        "status": status,
        "progress_pct": 100 if status == "done" else 0,
        "actual_start": "2026-10-01T01:00:00Z" if status != "open" else None,
        "actual_end": actual_end,
        "blocked_reason": kw.pop("blocked_reason", None),
        "revision": revision,
        "origin": kw.pop("origin", "control"),
        "conflict": kw.pop("conflict", False),
    }
    return _ev("EV-215", data, at, **kw)


def _ops(code, task_id, shipment_id, base, origin, at, **data):
    return _ev(
        code,
        {
            "task_id": task_id + 10_000,
            "ctl_task_id": task_id,
            "shipment_id": shipment_id,
            "step_code": f"S-{task_id}",
            "base_revision": base,
            "origin": origin,
            **data,
        },
        at,
    )


def _ctl(code, task_id, shipment_id, revision, at, assignee=U2, **data):
    return _ev(
        code,
        {
            "shipment_id": shipment_id,
            "task_id": task_id,
            "step_code": f"S-{task_id}",
            "plan_id": None,
            "assignee_id": assignee,
            "planned_start": "2026-10-01T00:00:00Z",
            "planned_end": "2026-10-30T00:00:00Z",
            "revision": revision,
            **data,
        },
        at,
    )


def _contract(cid: int, sla: dict, mode: str = "production") -> dict:
    return _ev(
        "EV-101",
        {
            "contract_id": cid,
            "contract_no": f"HD-{cid}",
            "customer_id": 1,
            "service_scope": [],
            "sla": sla,
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
        },
        "2026-09-01T00:00:00Z",
        mode,
    )


def _shipment(sid: int, cid: int, mode: str = "production") -> dict:
    return _ev(
        "EV-111",
        {
            "shipment_id": sid,
            "shipment_no": f"LH-{sid}",
            "contract_id": cid,
            "customer_id": 1,
            "required_delivery_at": "2026-10-20T00:00:00Z",
        },
        "2026-10-02T00:00:00Z",
        mode,
    )


def _delivered(sid: int, delivered: str, due: str, mode: str = "production") -> dict:
    return _ev(
        "EV-142",
        {"shipment_id": sid, "delivered_at": delivered, "sla_due_at": due, "in_full": True, "damage_free": True},
        delivered,
        mode,
    )


def _alert(aid, sid, task_id, severity, at, mode="production") -> dict:
    data = {
        "alert_id": aid,
        "alert_type": "task_overdue",
        "severity": severity,
        "shipment_id": sid,
        "task_id": task_id,
        "plan_id": None,
        "recipient_id": None,
    }
    return _ev("EV-221", data, at, mode)


SLA_A = {
    "OTD-50": {"description": "Giao đúng hạn ≥ 50%", "target_value": "50", "unit": "%", "kpi_code": "KPI-07"},
    "STEP-80": {"description": "Bước đúng hạn ≥ 80%", "target_value": "80.000", "unit": "%", "kpi_code": "KPI-03"},
    "CLR-24H": {"description": "Thông quan ≤ 24 giờ", "target_value": "24", "unit": "hour", "kpi_code": "KPI-CLR"},
}


@pytest.fixture()
def data(client, make_user, auth_headers):
    """Bộ dữ kiện tháng 10/2026 – xem docstring từng phần trong test để biết kết quả mong đợi."""
    users = {r: make_user(f"u.{r}", roles=[r]) for r in ("manager", "business_manager", "admin")}
    outcomes = _process(
        _contract(501, SLA_A),
        _contract(502, {}),
        _shipment(601, 501),
        _shipment(602, 501),
        _shipment(603, 502),
        _shipment(604, 501, mode="training"),
        # lô: 601 đúng hạn; 602 trễ 5 giờ; 603 trễ 3 giờ; 604 training (không tính)
        _delivered(601, "2026-10-10T00:00:00Z", "2026-10-10T02:00:00Z"),
        _delivered(602, "2026-10-12T10:00:00Z", "2026-10-12T05:00:00Z"),
        _delivered(603, "2026-10-15T03:00:00Z", "2026-10-15T00:00:00Z"),
        _delivered(604, "2026-10-15T09:00:00Z", "2026-10-15T00:00:00Z", mode="training"),
        # bước xong: 701 U1 đúng hạn; 702 U1 trễ 4 giờ; 703 U2 trễ 2 giờ; 704 U1 đúng hạn;
        # 705 U2 xong 01/11 giờ VN (ngoài kỳ); 706 training
        _synced(701, 601, 3, "done", "2026-10-05T09:00:00Z", U1, "2026-10-05T10:00:00Z", "2026-10-05T09:00:00Z"),
        _synced(702, 601, 3, "done", "2026-10-06T14:00:00Z", U1, "2026-10-06T10:00:00Z", "2026-10-06T14:00:00Z"),
        _synced(703, 602, 3, "done", "2026-10-07T02:00:00Z", U2, "2026-10-07T00:00:00Z", "2026-10-07T02:00:00Z"),
        _synced(704, 603, 3, "done", "2026-10-08T00:00:00Z", U1, "2026-10-08T05:00:00Z", "2026-10-08T00:00:00Z"),
        _synced(705, 602, 3, "done", "2026-10-31T18:00:00Z", U2, "2026-10-31T00:00:00Z", "2026-10-31T18:00:00Z"),
        _synced(
            706,
            604,
            3,
            "done",
            "2026-10-09T00:00:00Z",
            U1,
            "2026-10-08T00:00:00Z",
            "2026-10-09T00:00:00Z",
            mode="training",
        ),
        # 707 (lô 602, U2) bị chặn 2 lần: lần 1 đến qua 3 sự kiện (ctl.task.blocked, synced, EV-133 chuyển tiếp)
        _synced(707, 602, 1, "in_progress", "2026-10-19T00:00:00Z", U2),
        _ctl("EV-213", 707, 602, 2, "2026-10-20T01:00:00Z", reason="Thiếu B/L"),
        _synced(707, 602, 2, "blocked", "2026-10-20T01:00:00Z", U2, blocked_reason="Thiếu B/L"),
        _ops("EV-133", 707, 602, 2, "control", "2026-10-20T01:00:01Z", reason_code="other", note="Thiếu B/L"),
        _synced(707, 602, 3, "in_progress", "2026-10-20T03:00:00Z", U2),
        # lần 2: Luồng 1 báo chặn (chưa xác nhận – bỏ qua), Luồng 2 xác nhận bằng synced revision 4
        _ops("EV-133", 707, 602, 3, "ops", "2026-10-21T00:00:00Z", reason_code="other", note="Hải quan giữ"),
        _synced(707, 602, 4, "blocked", "2026-10-21T00:00:05Z", U2, origin="ops"),
        _synced(707, 602, 1, "done", "2026-10-21T00:00:06Z", U2, actual_end="2026-10-21T00:00:00Z"),  # cũ – bỏ
        # 708: Luồng 1 báo chặn nhưng Luồng 2 từ chối (xung đột) → không tính
        _synced(708, 603, 1, "open", "2026-10-22T00:00:00Z", U1),
        _ops("EV-133", 708, 603, 1, "ops", "2026-10-22T01:00:00Z", reason_code="other", note="x"),
        _synced(708, 603, 1, "open", "2026-10-22T01:00:01Z", U1, conflict=True),
        # cảnh báo: 801 (lô 601, bước 702 – U1), 802 critical (lô 602, bước 707 – U2, leo thang), 803 critical (lô 603)
        _alert(801, 601, 702, "warning", "2026-10-06T11:00:00Z"),
        _alert(802, 602, 707, "critical", "2026-10-20T01:00:02Z"),
        _alert(803, 603, None, "critical", "2026-10-15T01:00:00Z"),
        _alert(804, 604, None, "critical", "2026-10-15T01:00:00Z", mode="training"),
        _ev(
            "EV-222",
            {
                "alert_id": 802,
                "alert_type": "task_overdue",
                "severity": "critical",
                "shipment_id": 602,
                "escalated_to_role": "business_manager",
                "unresolved_hours": 2.5,
            },
            "2026-10-20T04:00:00Z",
        ),
        _ev("EV-102", {"contract_id": 501}, "2026-10-30T00:00:00Z"),  # chưa có xử lý → skipped
    )
    assert outcomes == ["processed"] * (len(outcomes) - 1) + ["skipped"]
    return {"h": {r: auth_headers(f"u.{r}") for r in users}}


def _get(client, path, h, **params):
    r = client.get(f"{ASM}{path}", params={**OCT, **params}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_overall_kpi(client, data):
    body = _get(client, "/kpi", data["h"]["manager"])
    assert body["period"] == {"from": "2026-10-01", "to": "2026-10-31", "timezone": "Asia/Ho_Chi_Minh"}
    assert body["mode"] == "production"
    kpis = body["metrics"].pop("kpis")  # Giai đoạn 15: KPI-03, KPI-07 khớp chỉ số Giai đoạn 14
    assert (kpis["KPI-03"]["value"], kpis["KPI-03"]["numerator"], kpis["KPI-03"]["denominator"]) == (50.0, 2, 4)
    assert (kpis["KPI-07"]["value"], kpis["KPI-07"]["denominator"]) == (33.33, 3)
    assert body["metrics"] == {
        "shipments": {
            "delivered": 3,
            "on_time": 1,
            "late": 2,
            "on_time_rate": 33.33,
            "avg_late_hours": 4.0,
            "max_late_hours": 5.0,
        },
        "tasks": {
            "completed": 4,
            "on_time": 2,
            "late": 2,
            "on_time_rate": 50.0,
            "avg_delay_hours": 3.0,
            "max_delay_hours": 4.0,
        },
        "blocked": {"blocked_tasks": 1, "block_events": 2},
        "alerts": {"alerts": 3, "critical": 2, "escalated": 1},
    }
    # 01/11 giờ VN: bước 705 xong 31/10 18:00 UTC
    nov = client.get(f"{ASM}/kpi", params={"from": "2026-11-01", "to": "2026-11-01"}, headers=data["h"]["admin"])
    assert nov.json()["metrics"]["tasks"]["completed"] == 1
    # kỳ không có số liệu: mẫu số 0 → null
    empty = client.get(f"{ASM}/kpi", params={"from": "2025-01-01", "to": "2025-01-31"}, headers=data["h"]["admin"])
    m = empty.json()["metrics"]
    assert m["shipments"]["on_time_rate"] is None and m["tasks"]["on_time_rate"] is None
    assert m["shipments"]["delivered"] == 0 and m["alerts"]["alerts"] == 0


def test_by_contract_with_sla(client, data):
    body = _get(client, "/kpi/contracts", data["h"]["business_manager"])
    assert body["total"] == 2 and [c["contract_id"] for c in body["items"]] == [501, 502]
    a = body["items"][0]
    assert a["contract_no"] == "HD-501"
    assert a["metrics"]["shipments"]["delivered"] == 2 and a["metrics"]["shipments"]["on_time_rate"] == 50.0
    assert a["metrics"]["tasks"] == {
        "completed": 3,
        "on_time": 1,
        "late": 2,
        "on_time_rate": 33.33,
        "avg_delay_hours": 3.0,
        "max_delay_hours": 4.0,
    }
    assert a["metrics"]["blocked"] == {"blocked_tasks": 1, "block_events": 2}
    assert a["metrics"]["alerts"] == {"alerts": 2, "critical": 1, "escalated": 1}
    sla = {s["sla_code"]: s for s in a["sla"]}
    assert (sla["OTD-50"]["actual_value"], sla["OTD-50"]["met"], sla["OTD-50"]["computed"]) == (50.0, True, True)
    assert (sla["STEP-80"]["actual_value"], sla["STEP-80"]["met"], sla["STEP-80"]["target_value"]) == (
        33.33,
        False,
        "80.000",
    )
    assert (sla["CLR-24H"]["computed"], sla["CLR-24H"]["actual_value"], sla["CLR-24H"]["met"]) == (False, None, None)
    b = body["items"][1]
    assert b["metrics"]["shipments"]["on_time_rate"] == 0.0 and b["metrics"]["tasks"]["on_time_rate"] == 100.0
    assert b["sla"] == []
    one = _get(client, "/kpi/contracts/501", data["h"]["manager"])
    assert one["metrics"] == a["metrics"] and one["sla"] == a["sla"] and one["period"]["from"] == "2026-10-01"
    # hợp đồng có hiệu lực nhưng không có số liệu trong kỳ vẫn có trong danh sách (chỉ số rỗng)
    jan = _get(client, "/kpi/contracts", data["h"]["manager"], **{"from": "2026-01-01", "to": "2026-01-31"})
    assert [c["contract_id"] for c in jan["items"]] == [501, 502]
    assert jan["items"][0]["sla"][1]["met"] is None


def test_by_shipment(client, data):
    body = _get(client, "/kpi/shipments", data["h"]["manager"])
    assert body["total"] == 3 and [s["shipment_id"] for s in body["items"]] == [603, 602, 601]  # 604 training
    s601 = body["items"][2]
    assert (s601["shipment_no"], s601["contract_id"], s601["on_time"]) == ("LH-601", 501, True)
    assert s601["metrics"]["tasks"]["completed"] == 2 and s601["metrics"]["tasks"]["on_time"] == 1
    assert s601["metrics"]["alerts"]["alerts"] == 1
    s602 = _get(client, "/kpi/shipments/602", data["h"]["manager"])
    assert s602["on_time"] is False and s602["metrics"]["shipments"]["avg_late_hours"] == 5.0
    assert s602["metrics"]["blocked"] == {"blocked_tasks": 1, "block_events": 2}
    only_a = _get(client, "/kpi/shipments", data["h"]["manager"], contract_id=501, page_size=1, page=2)
    assert only_a["total"] == 2 and [s["shipment_id"] for s in only_a["items"]] == [601]


def test_by_staff(client, data):
    body = _get(client, "/kpi/staff", data["h"]["admin"])
    assert [s["user_id"] for s in body["items"]] == [U1, U2]
    u1, u2 = body["items"]
    assert u1["metrics"]["shipments"] is None
    assert u1["metrics"]["tasks"] == {
        "completed": 3,
        "on_time": 2,
        "late": 1,
        "on_time_rate": 66.67,
        "avg_delay_hours": 4.0,
        "max_delay_hours": 4.0,
    }
    assert u1["metrics"]["alerts"]["alerts"] == 1 and u1["metrics"]["blocked"]["block_events"] == 0
    assert u2["metrics"]["tasks"]["completed"] == 1 and u2["metrics"]["tasks"]["on_time_rate"] == 0.0
    assert u2["metrics"]["blocked"] == {"blocked_tasks": 1, "block_events": 2}
    assert u2["metrics"]["alerts"] == {"alerts": 1, "critical": 1, "escalated": 1}
    assert _get(client, f"/kpi/staff/{U2}", data["h"]["manager"])["metrics"] == u2["metrics"]


def test_facts_dedup_stale_and_duplicates(client, data):
    with SessionLocal() as db:
        t707 = db.get(AsmFactTask, 707)
        blocks = db.scalars(select(AsmFactTaskBlock).where(AsmFactTaskBlock.task_id == 707)).all()
        t708 = db.get(AsmFactTask, 708)
        a802 = db.get(AsmFactAlert, 802)
    # synced revision 1 "done" đến sau revision 4 → bỏ; EV-133 origin=ops không đổi trạng thái
    assert (t707.status, t707.revision, t707.block_count, t707.actual_end) == ("blocked", 4, 2, None)
    assert sorted(b.revision for b in blocks) == [2, 4] and blocks[0].reason == "Thiếu B/L"
    assert t707.ops_task_id == 10_707
    assert (t708.status, t708.block_count) == ("open", 0)  # xung đột – Luồng 2 từ chối
    assert a802.escalated_to_role == "business_manager" and a802.escalated_at is not None
    # gửi lại đúng sự kiện (cùng event_id) → không xử lý lại
    again = _synced(707, 602, 5, "blocked", "2026-10-25T00:00:00Z", U2)
    assert _process(again, again) == ["processed", "duplicate"]
    with SessionLocal() as db:
        assert db.get(AsmFactTask, 707).block_count == 2  # đang chặn sẵn – đổi revision không phải lần chặn mới


def test_legacy_and_out_of_order_events(client):
    old = _ctl("EV-211", 901, 611, None, "2026-10-03T00:00:00Z", actual_start="2026-10-03T00:00:00Z")
    old_block = _ctl("EV-213", 901, 611, None, "2026-10-03T01:00:00Z", reason="x")
    later = _ev("EV-112", {"shipment_id": 611, "from": "in_execution", "to": "delivered"}, "2026-10-05T00:00:00Z")
    earlier = _ev("EV-112", {"shipment_id": 611, "from": "draft", "to": "docs_collecting"}, "2026-10-04T00:00:00Z")
    esc = _ev(
        "EV-222",
        {
            "alert_id": 911,
            "alert_type": "task_blocked",
            "severity": "critical",
            "shipment_id": 611,
            "escalated_to_role": "business_manager",
            "unresolved_hours": 3,
        },
        "2026-10-06T05:00:00Z",
    )
    assert _process(old, old_block, later, earlier, esc) == ["processed"] * 5
    with SessionLocal() as db:
        t = db.get(AsmFactTask, 901)
        ship = db.get(AsmFactShipment, 611)
        alert = db.get(AsmFactAlert, 911)
        n_blocks = db.scalar(select(func.count()).select_from(AsmFactTaskBlock))
    # sự kiện trước Giai đoạn 14 (không revision): lấy thời điểm, không đổi trạng thái, không tính lần chặn
    assert (t.status, t.revision, t.actual_start.isoformat(), n_blocks) == (None, 0, "2026-10-03T00:00:00+00:00", 0)
    assert (ship.status, ship.contract_id) == ("delivered", None)  # EV-112 cũ hơn đến sau → bỏ
    assert alert.raised_at.isoformat() == "2026-10-06T02:00:00+00:00" and alert.escalated_at is not None


def test_permissions_and_validation(client, data, make_user, make_party, auth_headers):
    make_user("kh.lan", roles=["customer"], party_id=make_party("KH Lan").id)
    for role in ("ops_staff", "instructor", "learner"):
        make_user(f"x.{role}", roles=[role])
    denied = [auth_headers("kh.lan")] + [auth_headers(f"x.{r}") for r in ("ops_staff", "instructor", "learner")]
    paths = ["/kpi", "/kpi/contracts", "/kpi/contracts/501", "/kpi/shipments", "/kpi/shipments/601"]
    paths += ["/kpi/staff", f"/kpi/staff/{U1}"]
    for h in denied:
        for path in paths:
            r = client.get(f"{ASM}{path}", params=OCT, headers=h)
            assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN", path
    assert client.get(f"{ASM}/kpi").status_code == 401
    h = data["h"]["manager"]
    bad = client.get(f"{ASM}/kpi", params={"from": "2026-10-31", "to": "2026-10-01"}, headers=h)
    assert bad.status_code == 422 and bad.json()["error"]["details"][0]["field"] == "query.from"
    long = client.get(f"{ASM}/kpi", params={"from": "2025-01-01", "to": "2026-01-02"}, headers=h)
    assert long.status_code == 422 and long.json()["error"]["details"][0]["issue"] == "range_too_long"
    assert client.get(f"{ASM}/kpi", params={"from": "x"}, headers=h).status_code == 422
    assert client.get(f"{ASM}/kpi", headers=h).status_code == 200  # mặc định 30 ngày tới hôm nay
    for path in ("/kpi/contracts/999", "/kpi/shipments/604", "/kpi/staff/123456"):
        r = client.get(f"{ASM}{path}", params=OCT, headers=h)
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND", path


def test_router_off_when_flow_disabled(client, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("ENABLED_FLOWS", "ops,control")
    get_settings.cache_clear()
    try:
        paths = create_app().openapi()["paths"]
    finally:
        monkeypatch.delenv("ENABLED_FLOWS")
        get_settings.cache_clear()
    assert not [p for p in paths if p.startswith(ASM)]


# --- Chuỗi thật qua Redis Stream ---------------------------------------------------------------------------------


def _asm_consumptions() -> dict[str, int]:
    with SessionLocal() as db:
        rows = db.execute(
            select(SysEventConsumption.status, func.count())
            .where(SysEventConsumption.consumer == "assessment")
            .group_by(SysEventConsumption.status)
        ).all()
    return dict(rows)


def test_end_to_end_via_redis_stream(client, world, make_contract, redis_url, make_user, auth_headers):
    profile(client, world, "s1", ALL_SKILLS)
    sla = {
        "sla_code": "STEP-90",
        "description": "Bước đúng hạn ≥ 90%",
        "target_value": "90",
        "unit": "%",
        "kpi_code": "KPI-03",
        "measure_level": "per_period",
    }
    contract = make_contract(world["customer"].id, sla_terms=[sla])
    s = shipment(client, world, contract)
    drain_all(redis_url)
    h = world["h"]["s1"]
    with SessionLocal() as db:
        facts = {t.step_code: t for t in db.scalars(select(AsmFactTask).where(AsmFactTask.shipment_id == s["id"]))}
        fs = db.get(AsmFactShipment, s["id"])
    assert len(facts) == 10 and {t.revision for t in facts.values()} == {1}
    assert {t.assignee_id for t in facts.values()} == {world["u"]["s1"].id}
    assert (fs.contract_id, fs.shipment_no, fs.mode) == (contract.id, s["shipment_no"], "production")
    nk1 = facts["NK-01"].task_id
    # Luồng 2: bắt đầu, chặn, gỡ chặn, hoàn thành NK-01 (ctl.task.* + synced + EV-131/133/132 chuyển tiếp)
    for action, body in (
        ("start", None),
        ("block", {"reason": "Thiếu B/L gốc"}),
        ("unblock", None),
        ("complete", None),
    ):
        assert client.post(f"{CTL}/tasks/{nk1}:{action}", json=body, headers=h).status_code == 200
    drain_all(redis_url)
    # Luồng 1: bắt đầu rồi chặn NK-02 (EV-131/133 origin=ops → Luồng 2 xác nhận bằng synced)
    ops_tasks = {t["step_code"]: t for t in client.get(f"{OPS}/shipments/{s['id']}/tasks", headers=h).json()}
    nk2 = ops_tasks["NK-02"]["id"]
    assert client.post(f"{OPS}/tasks/{nk2}:start", headers=h).status_code == 200
    assert client.post(f"{OPS}/tasks/{nk2}:block", json={"reason": "Hải quan giữ hàng"}, headers=h).status_code == 200
    drain_all(redis_url)
    with SessionLocal() as db:
        t1, t2 = db.get(AsmFactTask, nk1), db.get(AsmFactTask, facts["NK-02"].task_id)
        n_blocks = db.scalar(select(func.count()).select_from(AsmFactTaskBlock))
    assert (t1.status, t1.revision, t1.block_count, t1.sequence_ok) == ("done", 5, 1, True)
    assert t1.actual_end is not None and t1.ops_task_id == ops_tasks["NK-01"]["id"]
    assert (t2.status, t2.block_count, t2.performer_id) == ("blocked", 1, world["u"]["s1"].id)
    assert n_blocks == 2
    with SessionLocal() as db:  # ctl.task.* mang revision (Giai đoạn 14) – lần chặn ghi từ sự kiện đến đầu tiên
        blocked_ev = db.scalar(select(SysEvent).where(SysEvent.event_code == "EV-213"))
        first = db.scalar(select(AsmFactTaskBlock).where(AsmFactTaskBlock.task_id == nk1))
    assert blocked_ev.payload["data"]["revision"] == 3 and first.revision == 3
    assert str(first.source_event_id) == str(blocked_ev.event_id)
    stats = _asm_consumptions()
    assert stats.get("failed", 0) == 0 and stats.get("dead", 0) == 0 and stats["processed"] > 20

    make_user("ql.kpi", roles=["manager"])
    mh = auth_headers("ql.kpi")
    today = today_vn().isoformat()
    period = {"from": today, "to": today}
    m = client.get(f"{ASM}/kpi", params=period, headers=mh).json()["metrics"]
    assert m["tasks"]["completed"] == 1 and m["tasks"]["on_time_rate"] == 100.0
    assert m["blocked"] == {"blocked_tasks": 2, "block_events": 2}
    assert m["alerts"]["alerts"] == 2 and m["alerts"]["critical"] == 2  # task_blocked mỗi lần chặn
    c = client.get(f"{ASM}/kpi/contracts/{contract.id}", params=period, headers=mh).json()
    assert c["sla"][0]["kpi_code"] == "KPI-03" and c["sla"][0]["met"] is True
    staff = client.get(f"{ASM}/kpi/staff/{world['u']['s1'].id}", params=period, headers=mh).json()
    assert staff["full_name"] == world["u"]["s1"].full_name and staff["metrics"]["blocked"]["block_events"] == 2
    # replay từ outbox: mọi sự kiện đã xử lý → trùng, số liệu không đổi
    replay = consumer.replay("assessment")
    assert set(replay) <= {"duplicate"}
    assert client.get(f"{ASM}/kpi", params=period, headers=mh).json()["metrics"] == m
