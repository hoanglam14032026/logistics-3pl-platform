"""Giai đoạn 18 – Luồng 3 đào tạo (hợp đồng, buổi học, điểm danh) + Luồng 4 chấm điểm, KPI-11/12/13 (PostgreSQL +
Redis thật)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.timeutil import VN_TZ, today_vn
from app.shared.audit.models import SysAuditLog
from app.shared.events.models import SysEvent
from tests.flows.control.test_planning import redis_url  # noqa: F401
from tests.flows.control.test_sync_notify import drain_all

LRN = "/api/v2/learning"
ASM = "/api/v2/assessment"
PORTAL = "/api/v2/portal"


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


def _at(hours: float) -> str:
    return (datetime.now(VN_TZ) + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _events(code: str) -> list[dict]:
    with SessionLocal() as db:
        return [
            e.payload for e in db.scalars(select(SysEvent).where(SysEvent.event_code == code).order_by(SysEvent.id))
        ]


def _logs(action: str) -> list[SysAuditLog]:
    with SessionLocal() as db:
        return list(db.scalars(select(SysAuditLog).where(SysAuditLog.action == action).order_by(SysAuditLog.id)))


@pytest.fixture()
def world(client, make_user, make_party, auth_headers):
    a = make_party("Trường Cao đẳng Hàng hải", tax_code="0100000011")
    b = make_party("Công ty Bình Minh", tax_code="0100000012")
    seller = make_party("Shanghai Export", party_type="foreign_seller", country_code="CN")
    roles = {
        "admin": ["admin"],
        "manager": ["manager"],
        "bm": ["business_manager"],
        "ops": ["ops_staff"],
        "gv": ["instructor"],
        "gv2": ["instructor"],
        "hv1": ["learner"],
        "hv2": ["learner"],
        "hv3": ["learner"],
        "ql_gv": ["manager", "instructor"],  # quản lý kiêm giảng viên – không dạy hợp đồng trong test
    }
    users = {k: make_user(f"u.{k}", roles=r) for k, r in roles.items()}
    users["kh_a"] = make_user("kh.a", roles=["customer"], party_id=a.id)
    users["kh_b"] = make_user("kh.b", roles=["customer"], party_id=b.id)
    return {"a": a, "b": b, "seller": seller, "u": users, "h": {k: auth_headers(u.username) for k, u in users.items()}}


def _body(world, **over) -> dict:
    today = today_vn()
    return {
        "customer_id": world["a"].id,
        "title": "Nghiệp vụ giao nhận hàng nhập khẩu đường biển",
        "instructor_id": world["u"]["gv"].id,
        "learner_ids": [world["u"]["hv1"].id, world["u"]["hv2"].id],
        "start_date": (today - timedelta(days=30)).isoformat(),
        "end_date": (today + timedelta(days=60)).isoformat(),
        "schedule": "Tối thứ 2, 4 – 18:00–20:30",
        "tuition_fee": "12500000",
        **over,
    }


def _contract(client, world, **over) -> dict:
    r = client.post(f"{LRN}/training-contracts", json=_body(world, **over), headers=world["h"]["manager"])
    assert r.status_code == 201, r.text
    return r.json()


def _session(client, h, cid, starts: float, hours: float = 2, **over) -> dict:
    body = {"title": "Buổi", "starts_at": _at(starts), "ends_at": _at(starts + hours), **over}
    r = client.post(f"{LRN}/training-contracts/{cid}/sessions", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


# --- 1. Hợp đồng đào tạo ------------------------------------------------------------------------------------------


def test_create_contract_permissions_and_validation(client, world):
    h = world["h"]
    c = _contract(client, world)
    assert c["contract_no"].startswith(f"DT-{today_vn().year}-") and c["contract_type"] == "training"
    assert (c["status"], c["tuition_fee"], c["currency"]) == ("draft", "12500000.00", "VND")
    assert c["learner_ids"] == [world["u"]["hv1"].id, world["u"]["hv2"].id]
    assert client.post(f"{LRN}/training-contracts", json=_body(world), headers=h["admin"]).status_code == 201
    for who in ("bm", "gv", "ops", "hv1"):
        _error(client.post(f"{LRN}/training-contracts", json=_body(world), headers=h[who]), 403, "FORBIDDEN")
    _error(client.post(f"{LRN}/training-contracts", json=_body(world), headers=h["kh_a"]), 403, "FORBIDDEN")
    bad = [
        {"instructor_id": world["u"]["ops"].id},
        {"learner_ids": [world["u"]["gv2"].id]},
        {"customer_id": world["seller"].id},
        {"start_date": "2026-12-31", "end_date": "2026-01-01"},
        {"tuition_fee": "-1"},
        {"currency": "vnd"},
    ]
    for over in bad:
        r = client.post(f"{LRN}/training-contracts", json=_body(world, **over), headers=h["manager"])
        _error(r, 422, "VALIDATION_ERROR")
    url = f"{LRN}/training-contracts/{c['id']}"
    r = client.patch(url, json={"tuition_fee": "13000000.50"}, headers={**h["manager"], "If-Match": '"1"'})
    assert r.status_code == 200 and r.json()["tuition_fee"] == "13000000.50"
    _error(client.patch(url, json={"title": "X"}, headers={**h["manager"], "If-Match": '"1"'}), 409, "VERSION_CONFLICT")
    _error(client.patch(url, json={"title": None}, headers=h["manager"]), 422, "VALIDATION_ERROR")
    log = _logs("training_contract.update")[0]
    assert (log.before, log.after) == ({"tuition_fee": "12500000.00"}, {"tuition_fee": "13000000.50"})
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("DELETE FROM lrn_training_contracts"))


def test_visibility_by_role(client, world):
    h = world["h"]
    mine = _contract(client, world)
    other = _contract(
        client, world, instructor_id=world["u"]["gv2"].id, customer_id=world["b"].id, learner_ids=[world["u"]["hv3"].id]
    )
    ids = lambda who, path=f"{LRN}/training-contracts": [c["id"] for c in client.get(path, headers=h[who]).json()]  # noqa: E731
    assert sorted(ids("bm")) == sorted([mine["id"], other["id"]])
    assert ids("gv") == [mine["id"]]
    _error(client.get(f"{LRN}/training-contracts/{other['id']}", headers=h["gv"]), 404, "NOT_FOUND")
    _error(client.get(f"{LRN}/training-contracts", headers=h["ops"]), 403, "FORBIDDEN")
    # cổng: chưa kích hoạt → chưa thấy
    assert ids("kh_a", f"{PORTAL}/training-contracts") == [] and ids("hv1", f"{PORTAL}/training-contracts") == []
    _session(client, h["manager"], mine["id"], -48)
    for c in (mine, other):
        assert client.post(f"{LRN}/training-contracts/{c['id']}:activate", headers=h["manager"]).status_code == 200
    portal = client.get(f"{PORTAL}/training-contracts", headers=h["kh_a"]).json()
    assert [(p["id"], p["learner_count"]) for p in portal] == [(mine["id"], 2)]
    assert "note" not in portal[0] and "instructor_id" not in portal[0]
    assert ids("kh_b", f"{PORTAL}/training-contracts") == [other["id"]]
    assert ids("hv1", f"{PORTAL}/training-contracts") == [mine["id"]]
    assert ids("hv3", f"{PORTAL}/training-contracts") == [other["id"]]
    _error(client.get(f"{PORTAL}/training-contracts", headers=h["ops"]), 403, "FORBIDDEN")


# --- 2. Buổi học, điểm danh, hoàn thành ---------------------------------------------------------------------------


def test_sessions_attendance_completion_events(client, world):
    h = world["h"]
    c = _contract(client, world)
    url = f"{LRN}/training-contracts/{c['id']}"
    past = _session(client, h["gv"], c["id"], -3, title="Kiểm tra bộ chứng từ – thực hành")
    future = _session(client, h["manager"], c["id"], 48, is_required=False)
    assert (past["seq"], future["seq"]) == (1, 2)
    _error(
        client.post(
            f"{url}/sessions",
            json={"title": "X", "starts_at": _at(-24 * 40), "ends_at": _at(-24 * 40 + 1)},
            headers=h["gv"],
        ),
        422,
        "VALIDATION_ERROR",
    )  # ngoài thời hạn hợp đồng
    _error(
        client.post(f"{url}/sessions", json={"title": "X", "starts_at": _at(5), "ends_at": _at(4)}, headers=h["gv"]),
        422,
        "VALIDATION_ERROR",
    )
    _error(
        client.post(f"{url}/sessions", json={"title": "X", "starts_at": _at(5), "ends_at": _at(6)}, headers=h["gv2"]),
        404,
        "NOT_FOUND",
    )
    # điểm danh trước khi kích hoạt
    att = f"{LRN}/sessions/{past['id']}/attendance"
    item = {"learner_id": world["u"]["hv1"].id, "attendance": "present", "completion": "completed"}
    _error(client.put(att, json={"items": [item]}, headers=h["gv"]), 409, "INVALID_STATE_TRANSITION")
    assert _events("EV-302") == []
    r = client.post(f"{url}:activate", headers=h["admin"])
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert {e["data"]["learner_id"] for e in _events("EV-302")} == {world["u"]["hv1"].id, world["u"]["hv2"].id}
    assert (
        _events("EV-302")[0]["data"]["required_resource_ids"] == [past["id"]]
        and _events("EV-302")[0]["mode"] == "training"
    )
    assert [e["data"]["session_id"] for e in _events("lrn.session.scheduled")] == [past["id"], future["id"]]
    assert _events("lrn.contract.activated")[0]["data"]["instructor_id"] == world["u"]["gv"].id
    _error(client.patch(url, json={"title": "Y"}, headers=h["manager"]), 409, "INVALID_STATE_TRANSITION")
    # ghi danh sau khi kích hoạt → EV-302 ngay
    r = client.post(f"{url}/learners", json={"learner_ids": [world["u"]["hv3"].id]}, headers=h["manager"])
    assert r.status_code == 200 and len(r.json()["learner_ids"]) == 3 and len(_events("EV-302")) == 3
    _error(
        client.post(f"{url}/learners", json={"learner_ids": [world["u"]["hv3"].id]}, headers=h["manager"]),
        409,
        "DUPLICATE",
    )
    # quyền điểm danh: chỉ người hướng dẫn của hợp đồng
    _error(client.put(att, json={"items": [item]}, headers=h["manager"]), 403, "FORBIDDEN")
    _error(client.put(att, json={"items": [item]}, headers=h["gv2"]), 404, "NOT_FOUND")
    _error(client.put(att, json={"items": [item]}, headers=h["ql_gv"]), 403, "FORBIDDEN")  # thấy nhưng không dạy
    stranger = {**item, "learner_id": world["u"]["gv2"].id}
    _error(client.put(att, json={"items": [stranger]}, headers=h["gv"]), 422, "VALIDATION_ERROR")
    absent_done = {**item, "learner_id": world["u"]["hv2"].id, "attendance": "absent"}
    _error(client.put(att, json={"items": [absent_done]}, headers=h["gv"]), 422, "VALIDATION_ERROR")
    fut = f"{LRN}/sessions/{future['id']}/attendance"
    _error(client.put(fut, json={"items": [item]}, headers=h["gv"]), 409, "INVALID_STATE_TRANSITION")
    items = [
        item,
        {"learner_id": world["u"]["hv2"].id, "attendance": "absent", "note": "Ốm"},
        {"learner_id": world["u"]["hv3"].id, "attendance": "late", "completion": "not_completed"},
    ]
    r = client.put(att, json={"items": items}, headers=h["gv"])
    assert r.status_code == 200 and [(a["attendance"], a["completion"]) for a in r.json()] == [
        ("present", "completed"),
        ("absent", None),
        ("late", "not_completed"),
    ]
    assert client.put(att, json={"items": items}, headers=h["gv"]).status_code == 200  # không đổi → không ghi thêm
    assert len(_logs("training_session.attendance")) == 1
    ev = _events("EV-303")
    assert [(e["data"]["learner_id"], e["data"]["completed"], e["data"]["resource_id"]) for e in ev] == [
        (world["u"]["hv1"].id, True, past["id"])
    ]
    # sửa về chưa hoàn thành → EV-303 completed=false; nhật ký trước / sau
    client.put(att, json={"items": [{**item, "completion": "not_completed"}]}, headers=h["gv"])
    assert _events("EV-303")[-1]["data"]["completed"] is False
    log = _logs("training_session.attendance")[-1]
    assert log.before[str(world["u"]["hv1"].id)]["completion"] == "completed"
    # cổng người học: chỉ của mình
    mine = client.get(f"{PORTAL}/learning/sessions", headers=h["hv2"]).json()
    assert [(s["session_id"], s["attendance"]) for s in mine] == [(past["id"], "absent"), (future["id"], None)]
    _error(client.get(f"{PORTAL}/learning/sessions", headers=h["kh_a"]), 403, "FORBIDDEN")
    # hủy buổi: đã điểm danh → 409; buổi tương lai hủy được (lý do)
    _error(
        client.post(f"{LRN}/sessions/{past['id']}:cancel", json={"reason": "Nghỉ lễ"}, headers=h["gv"]),
        409,
        "INVALID_STATE_TRANSITION",
    )
    r = client.post(f"{LRN}/sessions/{future['id']}:cancel", json={"reason": "Nghỉ lễ"}, headers=h["gv"])
    assert r.json()["status"] == "cancelled" and _events("lrn.session.cancelled")[0]["data"]["reason"] == "Nghỉ lễ"
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("DELETE FROM lrn_attendance"))


# --- 3. Chấm điểm (Luồng 4) + KPI-11/12/13 qua chuỗi sự kiện thật ---------------------------------------------------


def test_scores_portal_and_learner_kpis(client, world, redis_url):  # noqa: F811
    h, u = world["h"], world["u"]
    c = _contract(client, world, learner_ids=[u["hv1"].id, u["hv2"].id])
    s1 = _session(client, h["gv"], c["id"], -26, title="Thực hành kiểm tra chứng từ")
    s2 = _session(client, h["gv"], c["id"], -3, title="Khai báo hải quan")
    s3 = _session(client, h["gv"], c["id"], 24, title="Buổi sau")
    client.post(f"{LRN}/training-contracts/{c['id']}:activate", headers=h["manager"])
    for sid, rows in (
        (s1["id"], [(u["hv1"].id, "present", "completed"), (u["hv2"].id, "present", "completed")]),
        (s2["id"], [(u["hv1"].id, "present", "completed"), (u["hv2"].id, "absent", None)]),
    ):
        items = [{"learner_id": lid, "attendance": a, "completion": comp} for lid, a, comp in rows]
        assert client.put(f"{LRN}/sessions/{sid}/attendance", json={"items": items}, headers=h["gv"]).status_code == 200
    drain_all(redis_url)
    url = f"{ASM}/sessions/{s1['id']}/scores"
    scores = {
        "items": [
            {
                "learner_id": u["hv1"].id,
                "score": "8.5",
                "comment": "Tốt",
                "seeded_discrepancies": 5,
                "detected_correct": 4,
                "false_reports": 1,
            },
            {
                "learner_id": u["hv2"].id,
                "score": "6",
                "seeded_discrepancies": 5,
                "detected_correct": 2,
                "false_reports": 2,
            },
        ]
    }
    _error(client.put(url, json=scores, headers=h["gv2"]), 404, "NOT_FOUND")
    _error(client.put(url, json=scores, headers=h["manager"]), 403, "FORBIDDEN")
    _error(client.put(url, json=scores, headers=h["ql_gv"]), 403, "FORBIDDEN")
    for bad in (
        {"learner_id": u["hv1"].id, "score": "11"},
        {"learner_id": u["hv1"].id, "score": "5", "seeded_discrepancies": 3},
        {"learner_id": u["hv1"].id, "score": "5", "seeded_discrepancies": 3, "detected_correct": 4, "false_reports": 0},
    ):
        _error(client.put(url, json={"items": [bad]}, headers=h["gv"]), 422, "VALIDATION_ERROR")
    _error(
        client.put(url, json={"items": [{"learner_id": u["hv3"].id, "score": "5"}]}, headers=h["gv"]),
        422,
        "VALIDATION_ERROR",
    )
    future = f"{ASM}/sessions/{s3['id']}/scores"
    _error(
        client.put(future, json={"items": [{"learner_id": u["hv1"].id, "score": "5"}]}, headers=h["gv"]),
        409,
        "INVALID_STATE_TRANSITION",
    )
    r = client.put(url, json=scores, headers=h["gv"])
    assert r.status_code == 200 and [s["score"] for s in r.json()] == ["8.50", "6.00"]
    client.put(
        f"{ASM}/sessions/{s2['id']}/scores",
        json={"items": [{"learner_id": u["hv1"].id, "score": "9.5"}]},
        headers=h["gv"],
    )
    # chấm lại – nhật ký trước / sau
    regrade = {"items": [{**scores["items"][1], "score": "6.5"}]}
    client.put(url, json=regrade, headers=h["gv"])
    n_logs = len(_logs("session_score.grade"))
    client.put(url, json=regrade, headers=h["gv"])  # chấm lại y nguyên → không ghi nhật ký
    assert len(_logs("session_score.grade")) == n_logs
    log = _logs("session_score.grade")[-1]
    assert (log.before[str(u["hv2"].id)]["score"], log.after[str(u["hv2"].id)]["score"]) == ("6.00", "6.50")
    assert client.get(url, headers=h["bm"]).status_code == 200
    # người học xem điểm của mình qua cổng
    mine = client.get(f"{PORTAL}/learning/scores", headers=h["hv1"]).json()
    assert [(m["session_title"], m["score"], m["comment"]) for m in mine] == [
        ("Thực hành kiểm tra chứng từ", "8.50", "Tốt"),
        ("Khai báo hải quan", "9.50", None),
    ]
    assert len(client.get(f"{PORTAL}/learning/scores", headers=h["hv2"]).json()) == 1
    _error(client.get(f"{PORTAL}/learning/scores", headers=h["gv"]), 403, "FORBIDDEN")
    # KPI người học
    period = {"from": (today_vn() - timedelta(days=3)).isoformat(), "to": (today_vn() + timedelta(days=3)).isoformat()}
    k = client.get(f"{ASM}/kpi/learners", params={**period, "contract_id": c["id"]}, headers=h["gv"]).json()
    by = {i["learner_id"]: i for i in k["items"]}
    one, two = by[u["hv1"].id], by[u["hv2"].id]
    assert (one["kpis"]["KPI-11"]["value"], one["kpis"]["KPI-12"]["value"], one["kpis"]["KPI-13"]["value"]) == (
        80.0,
        20.0,
        100.0,
    )
    assert (two["kpis"]["KPI-11"]["value"], two["kpis"]["KPI-12"]["value"]) == (40.0, 50.0)
    assert (two["kpis"]["KPI-13"]["numerator"], two["kpis"]["KPI-13"]["denominator"]) == (1, 2)  # buổi sau chưa tính
    assert (one["avg_score"], one["sessions_scored"], two["avg_score"]) == (9.0, 2, 6.5)
    assert client.get(f"{ASM}/kpi/learners", params=period, headers=h["gv2"]).json()["items"] == []
    assert client.get(f"{ASM}/kpi/learners/{u['hv1'].id}", params=period, headers=h["manager"]).status_code == 200
    _error(client.get(f"{ASM}/kpi/learners/{u['hv1'].id}", params=period, headers=h["gv2"]), 404, "NOT_FOUND")
    _error(client.get(f"{ASM}/kpi/learners", params=period, headers=h["ops"]), 403, "FORBIDDEN")
    # hủy hợp đồng → không chấm thêm
    client.post(f"{LRN}/training-contracts/{c['id']}:cancel", json={"reason": "Lớp giải thể"}, headers=h["manager"])
    drain_all(redis_url)
    assert _events("lrn.contract.closed")[0]["data"]["status"] == "cancelled"
    _error(client.put(url, json=regrade, headers=h["gv"]), 409, "INVALID_STATE_TRANSITION")
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("DELETE FROM asm_session_scores"))


def test_contract_lifecycle(client, world):
    h = world["h"]
    c = _contract(client, world, learner_ids=[])
    url = f"{LRN}/training-contracts/{c['id']}"
    _error(client.post(f"{url}:activate", headers=h["manager"]), 409, "INVALID_STATE_TRANSITION")  # chưa có người học
    _error(client.post(f"{url}:complete", headers=h["manager"]), 409, "INVALID_STATE_TRANSITION")
    client.post(f"{url}/learners", json={"learner_ids": [world["u"]["hv1"].id]}, headers=h["manager"])
    assert _events("EV-302") == []  # ghi danh lúc nháp: chưa phát
    client.post(f"{url}:activate", headers=h["manager"])
    assert len(_events("EV-302")) == 1
    r = client.post(f"{url}:complete", headers=h["admin"])
    assert r.json()["status"] == "completed"
    _error(client.post(f"{url}:cancel", json={"reason": "Muộn"}, headers=h["manager"]), 409, "INVALID_STATE_TRANSITION")
    _error(
        client.post(f"{url}/learners", json={"learner_ids": [world["u"]["hv2"].id]}, headers=h["manager"]),
        409,
        "INVALID_STATE_TRANSITION",
    )
    d = _contract(client, world)
    _error(
        client.post(f"{LRN}/training-contracts/{d['id']}:cancel", json={}, headers=h["manager"]),
        422,
        "VALIDATION_ERROR",
    )
    assert (
        client.post(
            f"{LRN}/training-contracts/{d['id']}:cancel", json={"reason": "Khách hủy"}, headers=h["manager"]
        ).json()["status_reason"]
        == "Khách hủy"
    )
    assert _events("lrn.contract.closed") == [
        e for e in _events("lrn.contract.closed") if e["data"]["contract_id"] == c["id"]
    ]  # hủy bản nháp không phát (Luồng 4 chưa biết)
    actions = {a.action for a in _logs("training_contract.complete") + _logs("training_contract.cancel")}
    assert actions == {"training_contract.complete", "training_contract.cancel"}


def test_learning_routes_off_when_flow_disabled(client, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("ENABLED_FLOWS", "ops,control,assessment")
    get_settings.cache_clear()
    try:
        paths = create_app().openapi()["paths"]
    finally:
        monkeypatch.delenv("ENABLED_FLOWS")
        get_settings.cache_clear()
    assert not [p for p in paths if p.startswith(LRN) or p.startswith(f"{PORTAL}/training-contracts")]
