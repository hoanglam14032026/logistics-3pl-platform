# ruff: noqa: F811  (fixture world, redis_url nhập từ module test khác)
"""Giai đoạn 16 – chứng từ lô + kiểm tra chứng từ BR-103, điểm kiểm soát của lô; sự kiện tới Luồng 4 (KPI-01/02/05/06)
– PostgreSQL + Redis thật."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import SessionLocal
from app.core.timeutil import VN_TZ, today_vn
from app.flows.assessment.models import AsmFactCheckpoint, AsmFactCustoms, AsmFactDocCheck, AsmFactShipment
from app.flows.operations.models import OpsDocument
from app.shared.audit.models import SysAuditLog
from app.shared.events.models import SysEvent
from tests.conftest import pdf_bytes
from tests.flows.control.test_planning import redis_url  # noqa: F401
from tests.flows.control.test_sync_notify import drain_all
from tests.flows.operations.test_shipments import _act, _create, _error, world  # noqa: F401

OPS = "/api/v2/ops"


def _upload(client, h, sid, doc_type="bill_of_lading", content=b"", name="ct.pdf", note=None):
    data = {"doc_type": doc_type}
    if note:
        data["note"] = note
    return client.post(
        f"{OPS}/shipments/{sid}/documents",
        files={"file": (name, content or pdf_bytes(doc_type), "application/pdf")},
        data=data,
        headers=h,
    )


def _complete(client, h, check_id, items, note=None):
    body = {"items": items}
    if note:
        body["note"] = note
    return client.post(f"{OPS}/document-checks/{check_id}:complete", json=body, headers=h)


def _events(code: str) -> list[dict]:
    with SessionLocal() as db:
        return [
            e.payload for e in db.scalars(select(SysEvent).where(SysEvent.event_code == code).order_by(SysEvent.id))
        ]


def _iso(hours: float) -> str:
    return (datetime.now(VN_TZ) + timedelta(hours=hours)).replace(microsecond=0).isoformat()


# --- Chứng từ + BR-103 ----------------------------------------------------------------------------------------------


def test_upload_creates_versions_and_pending_check(client, world):
    s = _create(client, world)
    h = world["h"]["ops_staff"]
    r = _upload(client, h, s["id"], note="Bản scan B/L")
    assert r.status_code == 201, r.text
    doc, check = r.json()["document"], r.json()["check"]
    assert (doc["doc_type"], doc["version_no"], doc["status"], doc["note"]) == (
        "bill_of_lading",
        1,
        "current",
        "Bản scan B/L",
    )
    assert (check["run_no"], check["status"], check["result"], check["triggered_by_document_id"]) == (
        1,
        "pending",
        None,
        doc["id"],
    )
    r2 = _upload(client, h, s["id"], "commercial_invoice")
    assert r2.json()["check"]["id"] == check["id"]  # lần đang chờ bao cả chứng từ mới
    r3 = _upload(client, h, s["id"])  # tải lại cùng loại → phiên bản 2
    assert r3.json()["document"]["version_no"] == 2
    current = client.get(f"{OPS}/shipments/{s['id']}/documents", headers=world["h"]["manager"]).json()
    assert [(d["doc_type"], d["version_no"]) for d in current] == [("bill_of_lading", 2), ("commercial_invoice", 1)]
    every = client.get(f"{OPS}/shipments/{s['id']}/documents", params={"all": True}, headers=h).json()
    assert [(d["doc_type"], d["version_no"], d["status"]) for d in every] == [
        ("bill_of_lading", 1, "superseded"),
        ("bill_of_lading", 2, "current"),
        ("commercial_invoice", 1, "current"),
    ]
    ev = _events("EV-121")
    assert [(e["data"]["doc_type"], e["data"]["version_no"]) for e in ev] == [
        ("bill_of_lading", 1),
        ("commercial_invoice", 1),
        ("bill_of_lading", 2),
    ]
    assert ev[0]["data"]["shipment_id"] == s["id"] and ev[0]["actor_id"] == world["users"]["ops_staff"].id
    # file tải về được (nội bộ), customer không
    fid = doc["file_id"]
    assert client.get(f"/api/v2/files/{fid}", headers=world["h"]["manager"]).status_code == 200
    assert client.get(f"/api/v2/files/{fid}", headers=world["h"]["customer"]).status_code in (403, 404)
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("DELETE FROM ops_documents"))


def test_upload_permissions_and_file_type(client, world):
    s = _create(client, world)
    for who in ("ops_staff_2", "manager", "admin", "customer"):
        assert _upload(client, world["h"][who], s["id"]).status_code == 403, who
    bad = _upload(client, world["h"]["ops_staff"], s["id"], content=b"MZ\x90\x00 not a pdf", name="x.exe")
    _error(bad, 415, "UNSUPPORTED_FILE_TYPE")
    _error(_upload(client, world["h"]["ops_staff"], s["id"], doc_type="passport"), 422, "VALIDATION_ERROR")
    with SessionLocal() as db:
        assert db.scalar(select(OpsDocument)) is None
    _error(client.get(f"{OPS}/shipments/{s['id']}/documents", headers=world["h"]["customer"]), 403, "FORBIDDEN")


def test_check_outcomes_and_br103(client, world):
    s = _create(client, world)
    sid, h = s["id"], world["h"]["ops_staff"]
    assert _act(client, world, sid, "submit").status_code == 200
    # BR-103: chưa có lần kiểm tra
    r = client.post(f"{OPS}/shipments/{sid}:verify-documents", headers=h)
    assert "chưa có lần kiểm tra" in _error(r, 409, "INVALID_STATE_TRANSITION")["message"]
    check = _upload(client, h, sid).json()["check"]
    _upload(client, h, sid, "commercial_invoice")
    r = client.post(f"{OPS}/shipments/{sid}:verify-documents", headers=h)
    assert "chưa ghi kết quả" in _error(r, 409, "INVALID_STATE_TRANSITION")["message"]
    # thiếu kết quả cho chứng từ đã tải; 'missing' cho loại đã có; 'ok' cho loại chưa có; thiếu lý do
    ok_bl = {"doc_type": "bill_of_lading", "outcome": "ok"}
    _error(_complete(client, h, check["id"], [ok_bl]), 422, "VALIDATION_ERROR")
    bad = [ok_bl, {"doc_type": "commercial_invoice", "outcome": "missing", "reason": "x"}]
    _error(_complete(client, h, check["id"], bad), 422, "VALIDATION_ERROR")
    bad = [ok_bl, {"doc_type": "commercial_invoice", "outcome": "ok"}, {"doc_type": "packing_list", "outcome": "ok"}]
    _error(_complete(client, h, check["id"], bad), 422, "VALIDATION_ERROR")
    bad = [ok_bl, {"doc_type": "commercial_invoice", "outcome": "invalid"}]
    _error(_complete(client, h, check["id"], bad), 422, "VALIDATION_ERROR")
    _error(_complete(client, world["h"]["ops_staff_2"], check["id"], [ok_bl]), 403, "FORBIDDEN")
    items = [
        ok_bl,
        {"doc_type": "commercial_invoice", "outcome": "invalid", "reason": "Tổng tiền lệch với hợp đồng"},
        {"doc_type": "packing_list", "outcome": "missing", "reason": "Khách chưa gửi P/L"},
    ]
    r = _complete(client, h, check["id"], items, note="Lần 1")
    assert r.status_code == 200, r.text
    done = r.json()
    assert (done["status"], done["result"], done["blocking_count"], done["checked_by"]) == (
        "completed",
        "failed",
        2,
        world["users"]["ops_staff"].id,
    )
    assert [(i["doc_type"], i["outcome"], i["document_id"] is None) for i in done["items"]] == [
        ("bill_of_lading", "ok", False),
        ("commercial_invoice", "invalid", False),
        ("packing_list", "missing", True),
    ]
    assert set(done["document_versions"]) == {"bill_of_lading", "commercial_invoice"}
    _error(_complete(client, h, check["id"], items), 409, "INVALID_STATE_TRANSITION")  # đã ghi kết quả
    ev122 = _events("EV-122")[-1]
    assert (ev122["data"]["run_no"], ev122["data"]["blocking_count"]) == (1, 2)
    assert {d["rule_code"] for d in ev122["data"]["discrepancies"]} == {"DOC_INVALID", "DOC_MISSING"}
    assert ev122["data"]["discrepancies"][0]["note"] in ("Tổng tiền lệch với hợp đồng", "Khách chưa gửi P/L")
    r = client.post(f"{OPS}/shipments/{sid}:verify-documents", headers=h)
    assert "còn 2 lỗi" in _error(r, 409, "INVALID_STATE_TRANSITION")["message"]
    # bổ sung → lần 2
    c2 = _upload(client, h, sid, "commercial_invoice").json()["check"]
    _upload(client, h, sid, "packing_list")
    assert c2["run_no"] == 2
    all_ok = [{"doc_type": t, "outcome": "ok"} for t in ("bill_of_lading", "commercial_invoice", "packing_list")]
    assert _complete(client, h, c2["id"], all_ok).json()["result"] == "passed"
    r = client.post(f"{OPS}/shipments/{sid}:verify-documents", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "docs_verified"
    runs = client.get(f"{OPS}/shipments/{sid}/document-checks", headers=world["h"]["business_manager"]).json()
    assert [(c["run_no"], c["result"]) for c in runs] == [(1, "failed"), (2, "passed")]
    with SessionLocal() as db:
        log = db.scalars(select(SysAuditLog).where(SysAuditLog.action == "document_check.complete")).all()
    assert len(log) == 2 and log[0].after["items"][1]["reason"] == "Tổng tiền lệch với hợp đồng"
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("UPDATE ops_document_check_items SET outcome = 'ok'"))


# --- Điểm kiểm soát -----------------------------------------------------------------------------------------------


def test_checkpoints_create_and_evaluate(client, world, make_user, auth_headers):
    s = _create(client, world)
    h = world["h"]["ops_staff"]
    url = f"{OPS}/shipments/{s['id']}/checkpoints"
    admin = make_user("u.loc", roles=["admin"])
    ah = auth_headers(admin.username)
    loc = client.post(
        "/api/v2/md/locations", json={"code": "VNDAD", "name": "Cảng Đà Nẵng", "location_type": "seaport"}, headers=ah
    ).json()
    old = client.post(
        "/api/v2/md/locations", json={"code": "KHO-OLD", "name": "Kho cũ", "location_type": "warehouse"}, headers=ah
    ).json()
    client.post(f"/api/v2/md/locations/{old['id']}:archive", headers=ah)
    bad = {"items": [{"checkpoint_type": "cargo_receipt", "location_id": old["id"]}]}
    _error(client.post(url, json=bad, headers=h), 422, "VALIDATION_ERROR")
    _error(client.post(url, headers=world["h"]["manager"]), 403, "FORBIDDEN")
    first = {"items": [{"checkpoint_type": "customs_clearance", "planned_at": _iso(5), "location_id": loc["id"]}]}
    r = client.post(url, json=first, headers=h)
    assert r.status_code == 201 and r.json()[0]["seq"] == 2 and r.json()[0]["location_id"] == loc["id"]
    _error(client.post(url, json=first, headers=h), 409, "DUPLICATE")
    rest = client.post(
        url,
        json={"items": [{"checkpoint_type": t} for t in ("delivery", "cargo_receipt", "warehouse_release")]},
        headers=h,
    )
    assert rest.status_code == 201
    cps = {c["checkpoint_type"]: c for c in client.get(url, headers=world["h"]["instructor"]).json()}
    assert [c["checkpoint_type"] for c in client.get(url, headers=h).json()] == [
        "cargo_receipt",
        "customs_clearance",
        "warehouse_release",
        "delivery",
    ]
    ev = f"{OPS}/checkpoints/{cps['cargo_receipt']['id']}:evaluate"
    _error(client.post(ev, json={"result": "failed", "actual_at": _iso(-2)}, headers=h), 422, "VALIDATION_ERROR")
    _error(client.post(ev, json={"result": "passed", "actual_at": _iso(2)}, headers=h), 422, "VALIDATION_ERROR")
    _error(
        client.post(ev, json={"result": "passed", "actual_at": _iso(-1)}, headers=world["h"]["ops_staff_2"]),
        403,
        "FORBIDDEN",
    )
    customs_on_wrong = {
        "result": "passed",
        "actual_at": _iso(-1),
        "customs": {"declaration_no": "105123456789", "channel": "green", "submitted_at": _iso(-3)},
    }
    _error(client.post(ev, json=customs_on_wrong, headers=h), 422, "VALIDATION_ERROR")
    r = client.post(ev, json={"result": "failed", "actual_at": _iso(-3), "note": "Container hư niêm phong"}, headers=h)
    assert (r.json()["status"], r.json()["attempts"]) == ("failed", 1)
    r = client.post(ev, json={"result": "passed", "actual_at": _iso(-2)}, headers=h)
    body = r.json()
    assert (body["status"], body["attempts"], [e["result"] for e in body["evaluations"]]) == (
        "passed",
        2,
        ["failed", "passed"],
    )
    _error(
        client.post(ev, json={"result": "passed", "actual_at": _iso(-1)}, headers=h), 409, "INVALID_STATE_TRANSITION"
    )
    # thông quan: cần tờ khai; nộp phải trước thông quan
    cev = f"{OPS}/checkpoints/{cps['customs_clearance']['id']}:evaluate"
    _error(client.post(cev, json={"result": "passed", "actual_at": _iso(-1)}, headers=h), 422, "VALIDATION_ERROR")
    late = {**customs_on_wrong, "customs": {**customs_on_wrong["customs"], "submitted_at": _iso(-0.5)}}
    _error(client.post(cev, json=late, headers=h), 422, "VALIDATION_ERROR")
    r = client.post(cev, json=customs_on_wrong, headers=h)
    assert r.status_code == 200 and r.json()["evaluations"][0]["declaration_no"] == "105123456789"
    evaluated = _events("EV-143")
    assert [(e["data"]["checkpoint_type"], e["data"]["attempt_no"], e["data"]["result"]) for e in evaluated] == [
        ("cargo_receipt", 1, "failed"),
        ("cargo_receipt", 2, "passed"),
        ("customs_clearance", 1, "passed"),
    ]
    ev141 = _events("EV-141")
    assert len(ev141) == 1 and (ev141[0]["data"]["channel"], ev141[0]["data"]["declaration_no"]) == (
        "green",
        "105123456789",
    )
    with SessionLocal() as db:
        logs = db.scalars(select(SysAuditLog).where(SysAuditLog.action.like("checkpoint.%"))).all()
    assert [a.action for a in logs].count("checkpoint.create") == 4 and len(logs) == 7
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("DELETE FROM ops_checkpoint_evaluations"))
    # lô đã hủy: không xác nhận được
    assert _act(client, world, s["id"], "cancel", {"reason": "Khách hủy đơn"}).status_code == 200
    dev = f"{OPS}/checkpoints/{cps['delivery']['id']}:evaluate"
    _error(
        client.post(dev, json={"result": "passed", "actual_at": _iso(-1)}, headers=h), 409, "INVALID_STATE_TRANSITION"
    )


# --- Chuỗi thật tới Luồng 4 ---------------------------------------------------------------------------------------


def test_events_reach_assessment_kpis(client, world, redis_url, make_user, auth_headers):  # noqa: F811
    s = _create(client, world)
    sid, h = s["id"], world["h"]["ops_staff"]
    _act(client, world, sid, "submit")
    c1 = _upload(client, h, sid).json()["check"]
    _complete(
        client, h, c1["id"], [{"doc_type": "bill_of_lading", "outcome": "invalid", "reason": "Sai tên người nhận"}]
    )
    c2 = _upload(client, h, sid).json()["check"]
    for t in ("commercial_invoice", "packing_list"):  # bắt buộc (quy tắc Giai đoạn 17)
        _upload(client, h, sid, t)
    _complete(
        client,
        h,
        c2["id"],
        [{"doc_type": t, "outcome": "ok"} for t in ("bill_of_lading", "commercial_invoice", "packing_list")],
    )
    assert client.post(f"{OPS}/shipments/{sid}:verify-documents", headers=h).status_code == 200
    cps = {c["checkpoint_type"]: c for c in client.post(f"{OPS}/shipments/{sid}/checkpoints", headers=h).json()}
    client.post(
        f"{OPS}/checkpoints/{cps['cargo_receipt']['id']}:evaluate",
        json={"result": "passed", "actual_at": _iso(-3)},
        headers=h,
    )
    client.post(
        f"{OPS}/checkpoints/{cps['customs_clearance']['id']}:evaluate",
        json={
            "result": "passed",
            "actual_at": _iso(-1),
            "customs": {"declaration_no": "105000000001", "channel": "yellow", "submitted_at": _iso(-7)},
        },
        headers=h,
    )
    client.post(
        f"{OPS}/checkpoints/{cps['warehouse_release']['id']}:evaluate",
        json={"result": "failed", "actual_at": _iso(-0.5), "note": "Chưa có lệnh giao hàng"},
        headers=h,
    )
    drain_all(redis_url)
    with SessionLocal() as db:
        checks = db.scalars(select(AsmFactDocCheck).order_by(AsmFactDocCheck.run_no)).all()
        ship = db.get(AsmFactShipment, sid)
        facts = db.scalars(select(AsmFactCheckpoint).order_by(AsmFactCheckpoint.checkpoint_id)).all()
        customs = db.scalar(select(AsmFactCustoms))
    assert [(c.run_no, c.blocking_count, c.checked_by) for c in checks] == [
        (1, 3, world["users"]["ops_staff"].id),  # B/L sai + thiếu hóa đơn, P/L (tự động)
        (2, 0, world["users"]["ops_staff"].id),
    ]
    assert ship.first_doc_received_at is not None and ship.docs_verified_at is not None
    assert [(f.source, f.checkpoint_type, f.result) for f in facts] == [
        ("ops", "cargo_receipt", "passed"),
        ("ops", "customs_clearance", "passed"),
        ("ops", "warehouse_release", "failed"),
    ]
    assert (customs.channel, customs.declaration_no) == ("yellow", "105000000001")
    make_user("ql.kpi", roles=["manager"])
    mh = auth_headers("ql.kpi")
    start = (today_vn() - timedelta(days=1)).isoformat()
    period = {"from": start, "to": today_vn().isoformat()}
    k = client.get(f"/api/v2/assessment/kpi/shipments/{sid}", params=period, headers=mh).json()["metrics"]["kpis"]
    assert (k["KPI-01"]["value"], k["KPI-01"]["denominator"]) == (0.0, 1)  # lần 1 có lỗi
    assert k["KPI-02"]["denominator"] == 1 and k["KPI-02"]["value"] is not None
    assert (k["KPI-05"]["value"], k["KPI-05"]["numerator"], k["KPI-05"]["denominator"]) == (66.67, 2, 3)
    assert (k["KPI-06"]["value"], k["KPI-06"]["details"]) == (6.0, {"by_channel": {"yellow": 6.0}})
    staff = client.get(f"/api/v2/assessment/kpi/staff/{world['users']['ops_staff'].id}", params=period, headers=mh)
    sk = staff.json()["metrics"]["kpis"]
    assert sk["KPI-01"]["denominator"] == 1 and sk["KPI-05"]["denominator"] == 3
