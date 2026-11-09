"""Giai đoạn 15 – Luồng 4: danh mục KPI + mục tiêu từ kế hoạch duyệt (EV-201), KPI-01…14, tính định kỳ, cảnh báo EV-403
(PostgreSQL + Redis thật)."""

import uuid
from datetime import date, datetime

import pytest
from redis import Redis
from rq.registry import ScheduledJobRegistry
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core import jobs
from app.core.db import SessionLocal
from app.core.timeutil import VN_TZ
from app.flows.assessment import calc, formulas
from app.flows.assessment.models import AsmCalcRun, AsmFactKpiMeasurement, AsmKpiAlert, AsmKpiTarget
from app.flows.control.models import CtlAlert, CtlAlertNotification, CtlContractPlan
from app.shared.audit.models import SysAuditLog
from app.shared.events import consumer
from app.shared.events.models import SysEvent
from app.shared.notifications import mail
from tests.flows.assessment.test_kpi import _contract, _delivered, _shipment, _synced
from tests.flows.control.test_planning import CTL
from tests.flows.control.test_sync_notify import drain_all

ASM = "/api/v2/assessment"
OCT = {"from": "2026-10-01", "to": "2026-10-31"}
S1, S2 = 9001, 9002
A, B = 501, 502


def _ev(code: str, data: dict, at: str, actor: int | None = None, mode: str = "production") -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_code": code,
        "event_name": code,
        "schema_version": 1,
        "occurred_at": at,
        "mode": mode,
        "actor_id": actor,
        "data": data,
    }


def _process(*envelopes: dict, consumer_name: str = "assessment") -> list[str]:
    return [consumer.process(consumer_name, e) for e in envelopes]


def _plan(contract_id: int, plan_id: int, version: int, targets: list[dict], at: str) -> dict:
    return _ev(
        "EV-201",
        {
            "plan_id": plan_id,
            "contract_id": contract_id,
            "plan_version": version,
            "approved_by": 1,
            "kpi_targets": targets,
            "milestones": [],
        },
        at,
    )


def _incident(iid, sid, kind, at):
    data = {"incident_id": iid, "contract_id": A, "shipment_id": sid, "incident_type": kind, "severity": "major"}
    return _ev("EV-205", data, at)


def _cp(cid, attempt, sid, result, performer, at):
    data = {
        "checkpoint_id": cid,
        "shipment_id": sid,
        "task_id": 1,
        "step_code": "NK-03",
        "result": result,
        "attempt_no": attempt,
        "performer_id": performer,
    }
    return _ev("EV-204", data, at)


def _check(cid, sid, run, blocking, actor, at):
    data = {"check_id": cid, "shipment_id": sid, "run_no": run, "blocking_count": blocking, "warning_count": 0}
    return _ev("EV-122", data, at, actor)


def _ev132(task_id, sid, base, sequence_ok, evidence_ok, at):
    data = {
        "task_id": task_id + 10_000,
        "ctl_task_id": task_id,
        "shipment_id": sid,
        "step_code": f"S-{task_id}",
        "base_revision": base,
        "origin": "control",
        "performer_id": S1,
        "planned_end": "2026-10-30T00:00:00Z",
        "actual_end": at,
        "sequence_ok": sequence_ok,
        "evidence_ok": evidence_ok,
    }
    return _ev("EV-132", data, at)


def _customs(sid, channel, submitted, cleared):
    data = {
        "shipment_id": sid,
        "declaration_no": f"TK-{sid}",
        "channel": channel,
        "submitted_at": submitted,
        "cleared_at": cleared,
    }
    return _ev("EV-141", data, cleared)


def _request(rid, cid, submitted, due, decided=None, by=None):
    out = [
        _ev(
            "EV-113",
            {
                "request_id": rid,
                "contract_id": cid,
                "channel": "portal",
                "submitted_at": submitted,
                "response_due_at": due,
            },
            submitted,
        )
    ]
    if decided:
        data = {"request_id": rid, "decision": "accepted", "decided_at": decided, "decided_by": by}
        out.append(_ev("EV-114", data, decided))
    return out


@pytest.fixture()
def facts(client, make_user, auth_headers):
    """Dữ kiện tháng 10/2026 cho KPI-01…14 – kết quả mong đợi ghi trong từng test."""
    users = {r: make_user(f"u.{r}", roles=[r], email=f"{r}@cty.vn") for r in ("manager", "business_manager", "admin")}
    task_done = "2026-10-10T00:00:00Z"
    outcomes = _process(
        _contract(A, {}),
        _contract(B, {}),
        *[_shipment(s, A) for s in (601, 602, 605)],
        _shipment(603, B),
        # KPI-02: 601 nhận chứng từ đầu 01/10 22:00 (sự kiện đến sau) → verified 02/10 10:00 = 12 giờ (S1);
        #         602: 02/10 00:00 → 03/10 06:00 = 30 giờ (S2)
        _ev(
            "EV-121", {"document_id": 1, "shipment_id": 601, "doc_type": "BL", "version_no": 1}, "2026-10-02T00:00:00Z"
        ),
        _ev(
            "EV-121", {"document_id": 2, "shipment_id": 602, "doc_type": "BL", "version_no": 1}, "2026-10-02T00:00:00Z"
        ),
        _ev(
            "EV-112", {"shipment_id": 601, "from": "docs_collecting", "to": "docs_verified"}, "2026-10-02T10:00:00Z", S1
        ),
        _ev(
            "EV-112", {"shipment_id": 602, "from": "docs_collecting", "to": "docs_verified"}, "2026-10-03T06:00:00Z", S2
        ),
        _ev(
            "EV-121", {"document_id": 3, "shipment_id": 601, "doc_type": "CI", "version_no": 1}, "2026-10-01T22:00:00Z"
        ),
        _ev(
            "EV-121", {"document_id": 4, "shipment_id": 601, "doc_type": "PL", "version_no": 1}, "2026-10-02T05:00:00Z"
        ),
        # 603 chuyển docs_verified nhưng chưa nhận sự kiện chứng từ nào → không tính KPI-02
        _ev(
            "EV-112", {"shipment_id": 603, "from": "docs_collecting", "to": "docs_verified"}, "2026-10-04T00:00:00Z", S1
        ),
        # KPI-01: 601 lần 1 sạch (S1); 602 lần 1 có lỗi chặn (S2), lần 2 sạch (không tính); 603 lần 1 sạch (S1)
        _check(1, 601, 1, 0, S1, "2026-10-03T00:00:00Z"),
        _check(2, 602, 1, 2, S2, "2026-10-03T00:00:00Z"),
        _check(3, 602, 2, 0, S2, "2026-10-04T00:00:00Z"),
        _check(4, 603, 1, 0, S1, "2026-10-04T00:00:00Z"),
        # KPI-04: 701 tuân thủ (evidence chưa kiểm chứng); 702 sai thứ tự; 703 thiếu minh chứng; 704 không có EV-132
        *[
            _synced(t, s, 3, "done", task_done, S1, actual_end=task_done)
            for t, s in ((701, 601), (702, 602), (703, 603))
        ],
        _synced(704, 605, 3, "done", task_done, S1, actual_end=task_done),
        _ev132(701, 601, 3, True, None, task_done),
        _ev132(702, 602, 3, False, None, task_done),
        _ev132(703, 603, 3, True, False, task_done),
        # KPI-05: điểm kiểm soát 1 đạt lần đầu (S1, lô 601); 2 trượt lần đầu (S2, 602) rồi đạt; 3 đạt (S1, 603)
        _cp(1, 1, 601, "passed", S1, "2026-10-05T00:00:00Z"),
        _cp(2, 1, 602, "failed", S2, "2026-10-05T00:00:00Z"),
        _cp(2, 2, 602, "passed", S2, "2026-10-06T00:00:00Z"),
        _cp(3, 1, 603, "passed", S1, "2026-10-06T00:00:00Z"),
        # KPI-06: xanh 4 giờ (601), đỏ 20 giờ (602), vàng 8 giờ (603)
        _customs(601, "green", "2026-10-07T00:00:00Z", "2026-10-07T04:00:00Z"),
        _customs(602, "red", "2026-10-07T00:00:00Z", "2026-10-07T20:00:00Z"),
        _customs(603, "yellow", "2026-10-07T00:00:00Z", "2026-10-07T08:00:00Z"),
        # KPI-07/08/09: 601 hoàn hảo; 602 trễ; 603 đúng hạn nhưng hư hỏng; 605 đúng hạn, đủ nhưng có sự cố chứng từ
        _delivered(601, "2026-10-10T00:00:00Z", "2026-10-10T02:00:00Z"),
        _delivered(602, "2026-10-12T10:00:00Z", "2026-10-12T05:00:00Z"),
        _ev(
            "EV-142",
            {
                "shipment_id": 603,
                "delivered_at": "2026-10-15T00:00:00Z",
                "sla_due_at": "2026-10-15T05:00:00Z",
                "in_full": True,
                "damage_free": False,
            },
            "2026-10-15T00:00:00Z",
        ),
        _delivered(605, "2026-10-16T00:00:00Z", "2026-10-16T05:00:00Z"),
        # KPI-10: sự cố ở 605 (DOCUMENT_ERROR), 602 (DAMAGE, đã đóng), 601 (DELAY_NOTICE)
        _incident(1, 605, "DOCUMENT_ERROR", "2026-10-14T00:00:00Z"),
        _incident(2, 602, "DAMAGE", "2026-10-11T00:00:00Z"),
        _incident(3, 601, "DELAY_NOTICE", "2026-10-09T00:00:00Z"),  # sự cố khác DOCUMENT_ERROR – 601 vẫn hoàn hảo
        _ev(
            "EV-206",
            {
                "incident_id": 2,
                "contract_id": A,
                "shipment_id": 602,
                "root_cause_code": "HANDLING",
                "liability": "company",
                "closed_at": "2026-10-13T00:00:00Z",
            },
            "2026-10-13T00:00:00Z",
        ),
        # KPI-14: r1 đúng hạn sau 4 giờ (S1); r2 trễ, 12 giờ (S2); r3 chưa quyết định
        *_request(1, A, "2026-10-05T00:00:00Z", "2026-10-05T08:00:00Z", "2026-10-05T04:00:00Z", S1),
        *_request(2, A, "2026-10-06T00:00:00Z", "2026-10-06T08:00:00Z", "2026-10-06T12:00:00Z", S2),
        *_request(3, B, "2026-10-07T00:00:00Z", "2026-10-07T08:00:00Z"),
    )
    assert set(outcomes) == {"processed"}, outcomes
    return {"h": {r: auth_headers(f"u.{r}") for r in users}, "u": users}


def _kpis(client, h, path="/kpi", **params) -> dict:
    r = client.get(f"{ASM}{path}", params={**OCT, **params}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["metrics"]["kpis"]


def _v(k: dict) -> tuple:
    return (k["value"], k["numerator"], k["denominator"])


def test_all_kpis_overall(client, facts):
    k = _kpis(client, facts["h"]["manager"])
    assert sorted(k) == [f"KPI-{i:02d}" for i in range(1, 15)]
    assert _v(k["KPI-01"]) == (66.67, 2, 3)
    assert _v(k["KPI-02"]) == (21.0, 42.0, 2) and k["KPI-02"]["unit"] == "hour"
    assert _v(k["KPI-03"])[2] == 4
    assert _v(k["KPI-04"]) == (33.33, 1, 3)
    assert _v(k["KPI-05"]) == (66.67, 2, 3)
    assert _v(k["KPI-06"]) == (8.0, 32.0, 3)
    assert k["KPI-06"]["details"] == {"by_channel": {"green": 4.0, "red": 20.0, "yellow": 8.0}}
    assert _v(k["KPI-07"]) == (75.0, 3, 4)
    assert _v(k["KPI-08"]) == (75.0, 3, 4)
    assert _v(k["KPI-09"]) == (25.0, 1, 4)
    assert _v(k["KPI-10"]) == (75.0, 3, 4) and k["KPI-10"]["direction"] == "lower_better"
    assert _v(k["KPI-14"]) == (50.0, 1, 2) and k["KPI-14"]["details"] == {"median_response_hours": 8.0}
    for code in ("KPI-11", "KPI-12", "KPI-13"):  # chỉ training
        assert (k[code]["applicable"], k[code]["value"], k[code]["denominator"]) == (False, None, 0)
    # kỳ không có số liệu: mẫu số 0 → null
    empty = client.get(f"{ASM}/kpi", params={"from": "2025-01-01", "to": "2025-01-31"}, headers=facts["h"]["admin"])
    for code, item in empty.json()["metrics"]["kpis"].items():
        assert (item["value"], item["denominator"]) == (None, 0), code


def test_kpis_by_contract_and_staff(client, facts):
    a = _kpis(client, facts["h"]["manager"], f"/kpi/contracts/{A}")
    assert _v(a["KPI-01"]) == (50.0, 1, 2)
    assert _v(a["KPI-06"]) == (12.0, 24.0, 2)
    assert a["KPI-06"]["details"] == {"by_channel": {"green": 4.0, "red": 20.0}}
    assert _v(a["KPI-07"]) == (66.67, 2, 3)
    assert _v(a["KPI-10"]) == (100.0, 3, 3)
    assert _v(a["KPI-14"]) == (50.0, 1, 2)
    b = _kpis(client, facts["h"]["manager"], f"/kpi/contracts/{B}")
    assert _v(b["KPI-01"]) == (100.0, 1, 1) and _v(b["KPI-10"]) == (0.0, 0, 1)
    assert (b["KPI-14"]["value"], b["KPI-14"]["applicable"]) == (None, True)  # r3 chưa quyết định
    s1 = _kpis(client, facts["h"]["business_manager"], f"/kpi/staff/{S1}")
    assert _v(s1["KPI-01"]) == (100.0, 2, 2) and _v(s1["KPI-02"]) == (12.0, 12.0, 1)
    assert _v(s1["KPI-05"]) == (100.0, 2, 2) and _v(s1["KPI-14"]) == (100.0, 1, 1)
    for code in ("KPI-06", "KPI-07", "KPI-08", "KPI-09", "KPI-10"):  # không đo theo nhân viên
        assert (s1[code]["applicable"], s1[code]["value"]) == (False, None), code
    s2 = _kpis(client, facts["h"]["admin"], f"/kpi/staff/{S2}")
    assert _v(s2["KPI-01"]) == (0.0, 0, 1) and _v(s2["KPI-02"]) == (30.0, 30.0, 1) and _v(s2["KPI-05"]) == (0.0, 0, 1)
    ship = _kpis(client, facts["h"]["manager"], "/kpi/shipments/605")
    assert _v(ship["KPI-09"]) == (0.0, 0, 1) and _v(ship["KPI-08"]) == (100.0, 1, 1)


def test_definitions_have_no_hardcoded_targets(client, facts):
    r = client.get(f"{ASM}/kpi/definitions", headers=facts["h"]["manager"])
    assert r.status_code == 200
    defs = r.json()
    assert [d["kpi_code"] for d in defs] == [f"KPI-{i:02d}" for i in range(1, 15)]
    assert not [k for d in defs for k in d if "target" in k or "floor" in k]
    assert {d["kpi_code"] for d in defs if d["modes"] == ["training"]} == {"KPI-11", "KPI-12", "KPI-13"}
    assert {d["formula_key"] for d in defs} == set(formulas.FORMULAS)


# --- Mục tiêu từ EV-201, tính định kỳ, cảnh báo EV-403 -----------------------------------------------------------

TARGETS_V1 = [{"kpi_code": "KPI-07", "target": "50", "warning_threshold": None, "unit": "%"}]
TARGETS_V2 = [
    {"kpi_code": "KPI-07", "target": "90", "warning_threshold": "60", "unit": "%"},  # 66.67 → warning
    {"kpi_code": "KPI-01", "target": "80", "warning_threshold": "60", "unit": "%"},  # 50 → critical
    {"kpi_code": "KPI-02", "target": "24", "warning_threshold": "48", "unit": "hour"},  # 21 giờ – thấp là tốt → đạt
    {"kpi_code": "KPI-CLR", "target": "24", "warning_threshold": None, "unit": "hour"},  # ngoài danh mục
]


def test_targets_from_approved_plan(client, facts):
    assert _process(
        _plan(A, 11, 1, TARGETS_V1, "2026-09-01T00:00:00Z"), _plan(A, 11, 2, TARGETS_V2, "2026-09-15T00:00:00Z")
    ) == ["processed", "processed"]
    h = facts["h"]["manager"]
    rows = client.get(f"{ASM}/kpi/targets", params={"contract_id": A}, headers=h).json()
    assert len(rows) == 5
    current = {(t["kpi_code"], t["target_value"], t["plan_version"]) for t in rows if t["current"]}
    assert current == {
        ("KPI-07", "90.000", 2),
        ("KPI-01", "80.000", 2),
        ("KPI-02", "24.000", 2),
        ("KPI-CLR", "24.000", 2),
    }
    detail = client.get(f"{ASM}/kpi/contracts/{A}", params=OCT, headers=h).json()
    status = {t["kpi_code"]: (t["actual_value"], t["status"]) for t in detail["targets"]}
    assert status == {
        "KPI-01": (50.0, "critical"),
        "KPI-02": (21.0, "ok"),
        "KPI-07": (66.67, "warning"),
        "KPI-CLR": (None, "not_computed"),
    }


def _count(model) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_calc_run_measurements_alerts_and_control_alerts(client, facts, redis_url):
    _process(_plan(A, 11, 2, TARGETS_V2, "2026-09-15T00:00:00Z"))
    mail.OUTBOX.clear()
    run = calc.run_calc("manual", date(2026, 10, 31))
    assert run.status == "succeeded" and run.stats["alerts"] == 4 and run.stats["breached"] == 4
    with SessionLocal() as db:
        ms = db.scalars(select(AsmFactKpiMeasurement).where(AsmFactKpiMeasurement.calc_run_id == run.id)).all()
        alerts = db.scalars(select(AsmKpiAlert).order_by(AsmKpiAlert.id)).all()
        events = db.scalars(select(SysEvent).where(SysEvent.event_code == "EV-403").order_by(SysEvent.id)).all()
        audits = db.scalars(select(SysAuditLog).where(SysAuditLog.action == "kpi.alert")).all()
    month = {(m.kpi_code, m.subject_type, m.subject_id): m for m in ms if m.period_type == "month"}
    assert float(month[("KPI-07", "contract", A)].value) == pytest.approx(66.67)
    assert float(month[("KPI-01", "all", 0)].value) == pytest.approx(66.67)
    assert month[("KPI-05", "staff", S1)].value == 100
    assert ("KPI-11", "all", 0) not in month  # chỉ training
    # tháng và quý cùng bắt đầu 01/10; tuần 26–31/10 không có dữ kiện → không có số đo tuần
    assert {(m.period_type, m.period_start) for m in ms} == {
        ("month", date(2026, 10, 1)),
        ("quarter", date(2026, 10, 1)),
    }
    assert {(a.kpi_code, a.period_type, a.severity) for a in alerts} == {
        ("KPI-07", "month", "warning"),
        ("KPI-07", "quarter", "warning"),
        ("KPI-01", "month", "critical"),
        ("KPI-01", "quarter", "critical"),
    }
    assert [str(a.event_id) for a in alerts] == [str(e.event_id) for e in events]
    critical = next(e for e in events if e.payload["data"]["severity"] == "critical")
    assert critical.payload["data"]["threshold"] == "60.000" and critical.consumers == ["control"]
    assert len(audits) == 4 and audits[0].after["calc_run_id"] == run.id
    # chạy lại: số đo mới (không sửa bản cũ), không cảnh báo lặp trong cùng kỳ
    again = calc.run_calc("manual", date(2026, 10, 31))
    assert again.stats["alerts"] == 0 and again.stats["breached"] == 4
    assert _count(AsmKpiAlert) == 4 and _count(AsmFactKpiMeasurement) == 2 * len(ms)
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("UPDATE asm_fact_kpi_measurements SET value = 0"))
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("DELETE FROM asm_kpi_alerts"))
    # EV-403 → Luồng 2: cảnh báo kpi_below_target + email cho manager, business_manager
    drain_all(redis_url)
    with SessionLocal() as db:
        ctl = db.scalars(select(CtlAlert).where(CtlAlert.alert_type == "kpi_below_target")).all()
        notes = db.scalars(select(CtlAlertNotification).where(CtlAlertNotification.purpose == "kpi")).all()
    assert len(ctl) == 4 and {a.contract_id for a in ctl} == {A} and {a.shipment_id for a in ctl} == {None}
    assert {(n.address, n.status) for n in notes} == {("manager@cty.vn", "sent"), ("business_manager@cty.vn", "sent")}
    assert len(notes) == 8 and any("[KPI]" in m.subject and "KPI-01" in m.subject for m in mail.OUTBOX)
    # business_manager thấy + xử lý cảnh báo KPI (chưa leo thang)
    bm = facts["h"]["business_manager"]
    mine = client.get(f"{CTL}/alerts", params={"mine": True, "alert_type": "kpi_below_target"}, headers=bm).json()
    assert len(mine) == 4 and mine[0]["contract_id"] == A and mine[0]["shipment_id"] is None
    r = client.post(f"{CTL}/alerts/{mine[0]['id']}:acknowledge", headers=bm)
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    # BR-205 không leo thang cảnh báo KPI
    from app.flows.control import tracking

    with SessionLocal() as db:
        assert tracking.escalate(db, datetime(2027, 1, 1, tzinfo=VN_TZ)) == 0
    # API
    h = facts["h"]["manager"]
    listed = client.get(f"{ASM}/kpi/alerts", params={"contract_id": A}, headers=h).json()
    assert len(listed) == 4
    latest = client.get(
        f"{ASM}/kpi/measurements",
        params={**OCT, "kpi_code": "KPI-07", "subject_type": "contract", "subject_id": A},
        headers=h,
    ).json()
    assert sorted((m["period_type"], m["calc_run_id"]) for m in latest) == [("month", again.id), ("quarter", again.id)]
    hist = client.get(
        f"{ASM}/kpi/measurements",
        params={**OCT, "kpi_code": "KPI-07", "subject_type": "contract", "subject_id": A, "history": True},
        headers=h,
    ).json()
    assert len(hist) == 4
    runs = client.get(f"{ASM}/kpi/calc-runs", headers=h).json()
    assert [r["id"] for r in runs] == [again.id, run.id]


def test_ev403_training_and_duplicates_ignored_by_control(client, facts):
    data = {
        "alert_id": 1,
        "subject_type": "contract",
        "subject_id": A,
        "subject_label": None,
        "kpi_code": "KPI-07",
        "kpi_name": "OTD",
        "unit": "%",
        "direction": "higher_better",
        "value": "10",
        "target": "90",
        "threshold": "90",
        "severity": "warning",
        "period": "month",
        "period_start": "2026-10-01",
        "period_end": "2026-10-31",
    }
    ev = _ev("EV-403", data, "2026-11-01T00:00:00Z")
    again = _ev("EV-403", data, "2026-11-02T00:00:00Z")
    training = _ev("EV-403", data, "2026-11-01T00:00:00Z", mode="training")
    assert _process(ev, again, training, consumer_name="control") == ["processed"] * 3
    with SessionLocal() as db:
        rows = db.scalars(select(CtlAlert)).all()
        results = [
            r.result
            for r in db.execute(
                text("SELECT result FROM sys_event_consumptions WHERE consumer = 'control' ORDER BY id")
            ).all()
        ]
    assert len(rows) == 1 and "KPI-07" in rows[0].message and "dưới mục tiêu 90" in rows[0].message
    assert [r["reason"] for r in results[1:]] == ["already_open", "training"]


def test_calc_failure_recorded(client, facts, monkeypatch):
    def boom(*a, **k):
        raise ValueError("hỏng")

    monkeypatch.setattr(formulas, "compute", boom)
    with pytest.raises(RuntimeError):
        calc.run_calc("manual", date(2026, 10, 31))
    with SessionLocal() as db:
        run = db.scalar(select(AsmCalcRun))
        log = db.scalar(select(SysAuditLog).where(SysAuditLog.action == "kpi.calc_failed"))
    assert (run.status, run.error) == ("failed", "ValueError: hỏng") and str(log.entity_id) == str(run.id)
    assert _count(AsmFactKpiMeasurement) == 0


def test_daily_schedule(client, redis_url, monkeypatch):
    # kỳ tính: tuần từ thứ Hai, tháng, quý – tới ngày as_of
    got = [(t, p.date_from, p.date_to) for t, p in calc.periods(date(2026, 11, 12))]
    assert got == [
        ("week", date(2026, 11, 9), date(2026, 11, 12)),
        ("month", date(2026, 11, 1), date(2026, 11, 12)),
        ("quarter", date(2026, 10, 1), date(2026, 11, 12)),
    ]
    at = calc.next_run_at(datetime(2026, 11, 9, 0, 30, tzinfo=VN_TZ))
    assert at == datetime(2026, 11, 9, 1, 0, tzinfo=VN_TZ)
    assert calc.next_run_at(datetime(2026, 11, 9, 2, 0, tzinfo=VN_TZ)) == datetime(2026, 11, 10, 1, 0, tzinfo=VN_TZ)
    monkeypatch.setenv("ASSESSMENT_KPI_CALC_HOUR", "23")
    from app.core.config import get_settings

    get_settings.cache_clear()
    assert calc.next_run_at(datetime(2026, 11, 9, 2, 0, tzinfo=VN_TZ)).hour == 23
    assert calc.schedule_daily() and calc.schedule_daily()  # xếp lại cùng ngày: không trùng
    registry = ScheduledJobRegistry(jobs.QUEUE_NAME, connection=Redis.from_url(redis_url))
    ids = registry.get_job_ids()
    assert len(ids) == 1 and ids[0].startswith("assessment-kpi-calc-")
    out = calc.scheduled_calc()  # tác vụ: tính cho hôm qua + xếp lần sau
    with SessionLocal() as db:
        run = db.get(AsmCalcRun, out["run_id"])
    assert run.trigger == "schedule" and run.status == "succeeded"
    monkeypatch.setenv("ASSESSMENT_KPI_CALC_ENABLED", "false")
    get_settings.cache_clear()
    assert calc.schedule_daily() is False


def test_calc_run_api_permissions(client, facts):
    h = facts["h"]
    assert client.post(f"{ASM}/kpi/calc-runs", json={"as_of": "2026-10-31"}, headers=h["manager"]).status_code == 403
    future = client.post(f"{ASM}/kpi/calc-runs", json={"as_of": "2999-01-01"}, headers=h["admin"])
    assert future.status_code == 422
    r = client.post(f"{ASM}/kpi/calc-runs", json={"as_of": "2026-10-31"}, headers=h["admin"])
    assert (
        r.status_code == 201
        and r.json()["status"] == "succeeded"
        and r.json()["requested_by"] == facts["u"]["admin"].id
    )
    for path in ("/kpi/definitions", "/kpi/targets", "/kpi/measurements", "/kpi/calc-runs", "/kpi/alerts"):
        assert client.get(f"{ASM}{path}", headers=h["business_manager"]).status_code == 200, path


# --- Duyệt kế hoạch ở Luồng 2 → EV-201 → Luồng 4 (chuỗi thật qua Redis) -------------------------------------------


def test_plan_approval_publishes_ev201(client, world, make_contract, redis_url):
    sla = {
        "sla_code": "OTD-95",
        "description": "Giao đúng hạn ≥ 95%",
        "target_value": "95",
        "unit": "%",
        "kpi_code": "KPI-07",
        "measure_level": "per_period",
    }
    contract = make_contract(world["customer"].id, sla_terms=[sla])
    drain_all(redis_url)
    with SessionLocal() as db:
        plan = db.scalar(select(CtlContractPlan).where(CtlContractPlan.contract_id == contract.id))
    m, bm, s1 = world["h"]["m1"], world["h"]["bm"], world["h"]["s1"]
    url = f"{CTL}/contract-plans/{plan.id}"
    body = {
        "kpi_targets": [
            {"kpi_code": "KPI-07", "target_value": "95", "warning_threshold": "80", "unit": "%"},
            {"kpi_code": "KPI-03", "target_value": "90", "unit": "%"},
        ]
    }
    assert client.put(f"{url}/kpi-targets", json=body, headers=bm).status_code == 403
    assert client.put(f"{url}/kpi-targets", json=body, headers=s1).status_code == 403
    dup = {"kpi_targets": [body["kpi_targets"][0], body["kpi_targets"][0]]}
    assert client.put(f"{url}/kpi-targets", json=dup, headers=m).status_code == 422
    stale = client.put(f"{url}/kpi-targets", json=body, headers={**m, "If-Match": '"99"'})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    r = client.put(f"{url}/kpi-targets", json=body, headers=m)
    assert r.status_code == 200, r.text
    targets = {t["kpi_code"]: t for t in r.json()["kpi_targets"]}
    assert targets["KPI-07"]["source"] == "contract_sla" and targets["KPI-03"]["source"] == "internal"
    assert client.post(f"{url}:approve", headers=bm).status_code == 409  # chưa gửi duyệt
    assert client.post(f"{url}:submit", headers=m).json()["status"] == "pending_approval"
    assert client.put(f"{url}/kpi-targets", json=body, headers=m).status_code == 409  # đang chờ duyệt
    assert client.post(f"{url}:approve", headers=m).status_code == 403  # manager không tự duyệt
    rej = client.post(f"{url}:reject", json={"reason": "Mục tiêu KPI-03 quá thấp"}, headers=bm)
    assert rej.json()["status"] == "draft"
    client.post(f"{url}:submit", headers=m)
    ok = client.post(f"{url}:approve", headers=bm)
    assert ok.status_code == 200 and ok.json()["status"] == "approved" and ok.headers["ETag"]
    drain_all(redis_url)
    with SessionLocal() as db:
        ev = db.scalar(select(SysEvent).where(SysEvent.event_code == "EV-201"))
        rows = db.scalars(select(AsmKpiTarget).where(AsmKpiTarget.subject_id == contract.id)).all()
        actions = [a.action for a in db.scalars(select(SysAuditLog).where(SysAuditLog.action.like("plan.%")))]
    assert ev.consumers == ["ops", "assessment"] and ev.payload["data"]["approved_by"] == world["u"]["bm"].id
    assert {(t.kpi_code, str(t.target_value), str(t.warning_threshold)) for t in rows} == {
        ("KPI-07", "95.000", "80.000"),
        ("KPI-03", "90.000", "None"),
    }
    assert actions[1:] == ["plan.kpi_targets", "plan.submit", "plan.reject", "plan.submit", "plan.approve"]
    # sửa sau khi duyệt: phiên bản mới, duyệt lại → bộ mục tiêu mới là hiện hành
    rev = client.post(f"{url}:revise", headers=m)
    assert (rev.json()["status"], rev.json()["plan_version"]) == ("revised", 2)
    body["kpi_targets"][0]["target_value"] = "97"
    client.put(f"{url}/kpi-targets", json=body, headers=m)
    client.post(f"{url}:submit", headers=m)
    client.post(f"{url}:approve", headers=bm)
    drain_all(redis_url)
    mh = world["h"]["m1"]
    cur = [
        t
        for t in client.get(f"{ASM}/kpi/targets", params={"contract_id": contract.id}, headers=mh).json()
        if t["current"]
    ]
    assert {(t["kpi_code"], t["target_value"], t["plan_version"]) for t in cur} == {
        ("KPI-07", "97.000", 2),
        ("KPI-03", "90.000", 2),
    }
    empty = client.put(f"{url}/kpi-targets", json={"kpi_targets": []}, headers=m)
    assert empty.status_code == 409  # đã duyệt – phải :revise trước
