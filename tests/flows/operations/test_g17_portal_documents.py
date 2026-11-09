# ruff: noqa: F401, F811  (fixture world nhập từ module test khác)
"""Giai đoạn 17 – khách hàng gửi chứng từ qua cổng, lô tự quay về docs_collecting, điểm đi / đến theo md_locations,
quy tắc chứng từ bắt buộc (PostgreSQL)."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import SessionLocal
from app.shared.audit.models import SysAuditLog
from app.shared.events.models import SysEvent
from tests.conftest import location_ids, pdf_bytes
from tests.flows.operations.test_shipments import URL, _act, _create, _error, _payload, world

OPS = "/api/v2/ops"
PORTAL = "/api/v2/portal/shipments"
REQ = f"{OPS}/document-requirements"
SEA_IMPORT = ("bill_of_lading", "commercial_invoice", "packing_list")


def _portal_upload(client, h, sid, doc_type="commercial_invoice", content=b"", name="ct.pdf"):
    return client.post(
        f"{PORTAL}/{sid}/documents",
        files={"file": (name, content or pdf_bytes(doc_type), "application/pdf")},
        data={"doc_type": doc_type, "note": "Bản gửi qua cổng"},
        headers=h,
    )


def _staff_upload(client, h, sid, doc_type):
    r = client.post(
        f"{OPS}/shipments/{sid}/documents",
        files={"file": (f"{doc_type}.pdf", pdf_bytes(doc_type), "application/pdf")},
        data={"doc_type": doc_type},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _events(code: str, shipment_id: int) -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(select(SysEvent).where(SysEvent.event_code == code).order_by(SysEvent.id))
        return [e.payload for e in rows if e.payload["data"].get("shipment_id") == shipment_id]


def _logs(action: str) -> list[SysAuditLog]:
    with SessionLocal() as db:
        return list(db.scalars(select(SysAuditLog).where(SysAuditLog.action == action).order_by(SysAuditLog.id)))


# --- 1. Cổng khách hàng gửi chứng từ ------------------------------------------------------------------------------


def test_customer_uploads_documents_for_own_shipments_only(client, world, make_user, auth_headers):
    s = _create(client, world)
    kh = world["h"]["customer"]
    _error(_portal_upload(client, kh, s["id"]), 404, "NOT_FOUND")  # lô nháp không có trên cổng
    assert _act(client, world, s["id"], "submit").status_code == 200
    r = _portal_upload(client, kh, s["id"])
    assert r.status_code == 201, r.text
    doc = r.json()
    assert (doc["doc_type"], doc["version_no"], doc["channel"]) == ("commercial_invoice", 1, "portal")
    assert not {"note", "uploaded_by", "file_id", "status"} & doc.keys()  # không lộ thông tin nội bộ
    ev = _events("EV-121", s["id"])
    assert len(ev) == 1 and ev[0]["actor_id"] == world["users"]["customer"].id
    checks = client.get(f"{OPS}/shipments/{s['id']}/document-checks", headers=world["h"]["ops_staff"]).json()
    assert [(c["run_no"], c["status"]) for c in checks] == [(1, "pending")]
    staff_view = client.get(f"{OPS}/shipments/{s['id']}/documents", headers=world["h"]["manager"]).json()
    assert (staff_view[0]["channel"], staff_view[0]["note"]) == ("portal", "Bản gửi qua cổng")
    listed = client.get(f"{PORTAL}/{s['id']}/documents", headers=kh).json()
    assert [(d["doc_type"], d["channel"]) for d in listed] == [("commercial_invoice", "portal")]
    # khách hàng khác, nhân viên dùng API cổng, file sai loại (nhận dạng theo nội dung, không theo tên)
    make_user("kh.b", roles=["customer"], party_id=world["b"].id)
    _error(_portal_upload(client, auth_headers("kh.b"), s["id"]), 404, "NOT_FOUND")
    _error(client.get(f"{PORTAL}/{s['id']}/documents", headers=auth_headers("kh.b")), 404, "NOT_FOUND")
    _error(_portal_upload(client, world["h"]["ops_staff"], s["id"]), 403, "FORBIDDEN")
    _error(
        _portal_upload(client, kh, s["id"], content=b"plain text, not a pdf", name="hd.pdf"),
        415,
        "UNSUPPORTED_FILE_TYPE",
    )
    _error(_portal_upload(client, kh, s["id"], doc_type="passport"), 422, "VALIDATION_ERROR")
    assert len(_events("EV-121", s["id"])) == 1
    # lô đã hủy: không gửi thêm
    assert _act(client, world, s["id"], "cancel", {"reason": "Khách hủy đơn"}).status_code == 200
    _error(_portal_upload(client, kh, s["id"]), 409, "INVALID_STATE_TRANSITION")


# --- 2. Chứng từ mới sau docs_verified → quay về docs_collecting -----------------------------------------------------


def test_new_document_after_verified_reopens_shipment(client, world):
    s = _create(client, world)
    sid, h = s["id"], world["h"]["ops_staff"]
    for step in ("submit", "verify-documents"):
        assert _act(client, world, sid, step).status_code == 200
    assert _portal_upload(client, world["h"]["customer"], sid, "certificate_of_origin").status_code == 201
    detail = client.get(f"{URL}/{sid}", headers=h).json()
    assert detail["status"] == "docs_collecting"
    assert detail["history"][-1]["note"].startswith("Có chứng từ mới (certificate_of_origin v1)")
    log = _logs("shipment.reopen_documents")[-1]
    assert (log.before, log.after["channel"], log.actor_id) == (
        {"status": "docs_verified"},
        "portal",
        world["users"]["customer"].id,
    )
    assert _events("EV-112", sid)[-1]["data"] == {"shipment_id": sid, "from": "docs_verified", "to": "docs_collecting"}
    checks = client.get(f"{OPS}/shipments/{sid}/document-checks", headers=h).json()
    assert [(c["run_no"], c["status"]) for c in checks] == [(1, "completed"), (2, "pending")]
    r = client.post(f"{URL}/{sid}:verify-documents", headers=h)
    assert "chưa ghi kết quả" in _error(r, 409, "INVALID_STATE_TRANSITION")["message"]
    items = [{"doc_type": t, "outcome": "ok"} for t in (*SEA_IMPORT, "certificate_of_origin")]
    done = client.post(f"{OPS}/document-checks/{checks[1]['id']}:complete", json={"items": items}, headers=h)
    assert done.json()["result"] == "passed"
    assert client.post(f"{URL}/{sid}:verify-documents", headers=h).json()["status"] == "docs_verified"
    # nhân viên tải chứng từ mới cũng đưa lô về docs_collecting
    _staff_upload(client, h, sid, "packing_list")
    assert client.get(f"{URL}/{sid}", headers=h).json()["status"] == "docs_collecting"
    assert [a.after["channel"] for a in _logs("shipment.reopen_documents")] == ["portal", "staff"]


def test_document_after_execution_started_keeps_status(client, world):
    s = _create(client, world)
    for step in ("submit", "verify-documents", "start"):
        assert _act(client, world, s["id"], step).status_code == 200
    assert _portal_upload(client, world["h"]["customer"], s["id"], "other").status_code == 201
    assert client.get(f"{URL}/{s['id']}", headers=world["h"]["ops_staff"]).json()["status"] == "in_execution"
    assert _logs("shipment.reopen_documents") == []


# --- 3. Điểm đi / đến theo md_locations ---------------------------------------------------------------------------


def test_shipment_locations_required_active_and_snapshotted(client, world):
    origin, dest = location_ids()
    s = _create(client, world)
    assert (s["origin_location_id"], s["destination_location_id"]) == (origin, dest)
    assert (s["origin_location"], s["destination_location"]) == ("CNSHA – Cảng Thượng Hải", "VNHPH – Cảng Hải Phòng")
    h = world["h"]["ops_staff"]
    body = _payload(world)
    missing = {k: v for k, v in body.items() if k != "destination_location_id"}
    _error(client.post(URL, json=missing, headers=h), 422, "VALIDATION_ERROR")
    _error(client.post(URL, json={**body, "destination_location_id": origin}, headers=h), 422, "VALIDATION_ERROR")
    (old_loc,) = location_ids("KHO-CU")
    with SessionLocal() as db:
        db.execute(
            text("UPDATE md_locations SET status = 'archived', archived_at = now() WHERE id = :i"), {"i": old_loc}
        )
        db.commit()
    err = _error(client.post(URL, json={**body, "origin_location_id": old_loc}, headers=h), 422, "VALIDATION_ERROR")
    assert err["details"][0]["field"] == "body.origin_location_id"
    (dad,) = location_ids("VNDAD")
    r = client.patch(f"{URL}/{s['id']}", json={"destination_location_id": dad}, headers=h)
    assert r.status_code == 200 and (r.json()["destination_location_id"], r.json()["destination_location"]) == (
        dad,
        "VNDAD – VNDAD",
    )
    _error(
        client.patch(f"{URL}/{s['id']}", json={"destination_location_id": origin}, headers=h), 422, "VALIDATION_ERROR"
    )
    _error(client.patch(f"{URL}/{s['id']}", json={"origin_location": "Tự do"}, headers=h), 422, "VALIDATION_ERROR")
    # CSDL: lô mới không có địa điểm bị chặn; lô cũ (NULL) vẫn sửa / chuyển trạng thái được
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO ops_shipments (shipment_no, contract_id, contract_version, mode, customer_id, direction, "
                "transport_mode, shipper_id, shipper_name, consignee_id, consignee_name, origin_location, "
                "destination_location, required_delivery_at) SELECT 'LH-CU-1', contract_id, contract_version, mode, "
                "customer_id, direction, transport_mode, shipper_id, shipper_name, consignee_id, consignee_name, "
                "'Hải Phòng', 'Hà Nội', required_delivery_at FROM ops_shipments WHERE id = :i"
            ),
            {"i": s["id"]},
        )
    with SessionLocal() as db:
        db.execute(
            text("UPDATE ops_shipments SET origin_location_id = NULL, destination_location_id = NULL WHERE id = :i"),
            {"i": s["id"]},
        )
        db.commit()
    assert _act(client, world, s["id"], "submit").status_code == 200
    legacy = client.get(f"{URL}/{s['id']}", headers=h).json()
    assert (legacy["origin_location_id"], legacy["origin_location"]) == (None, "CNSHA – Cảng Thượng Hải")


# --- 4. Quy tắc chứng từ bắt buộc (CSDL) --------------------------------------------------------------------------


def test_required_documents_marked_missing_automatically(client, world):
    s = _create(client, world)
    sid, h = s["id"], world["h"]["ops_staff"]
    _act(client, world, sid, "submit")
    rules = client.get(REQ, headers=h).json()
    assert len(rules) == 8
    sea = client.get(REQ, params={"transport_mode": "sea_fcl"}, headers=h).json()
    assert {(r["transport_mode"], r["direction"], r["doc_type"]) for r in sea} >= {
        ("sea_fcl", None, "bill_of_lading"),
        (None, "import", "commercial_invoice"),
        (None, "import", "packing_list"),
    }
    check = _staff_upload(client, h, sid, "bill_of_lading")["check"]
    assert [a["doc_type"] for a in check["auto_missing"]] == ["commercial_invoice", "packing_list"]
    r = client.post(
        f"{OPS}/document-checks/{check['id']}:complete",
        json={"items": [{"doc_type": "bill_of_lading", "outcome": "ok"}]},
        headers=h,
    )
    done = r.json()
    assert (done["result"], done["blocking_count"], done["auto_missing"]) == ("failed", 2, [])
    auto = [i for i in done["items"] if i["source"] == "auto"]
    assert [(i["doc_type"], i["outcome"], i["rule_id"] is not None) for i in auto] == [
        ("commercial_invoice", "missing", True),
        ("packing_list", "missing", True),
    ]
    assert "Thiếu chứng từ bắt buộc 'commercial_invoice'" in auto[0]["reason"]
    disc = _events("EV-122", sid)[-1]["data"]["discrepancies"]
    assert {(d["source"], d["rule_code"]) for d in disc} == {("auto", "DOC_REQUIRED_MISSING")}
    with pytest.raises(DBAPIError), SessionLocal() as db:  # 'auto' phải gắn quy tắc
        db.execute(
            text(
                "INSERT INTO ops_document_check_items (check_id, doc_type, outcome, reason, source) "
                "VALUES (:c, 'other', 'missing', 'x', 'auto')"
            ),
            {"c": check["id"]},
        )
    # tắt quy tắc P/L nhập khẩu → lần sau chỉ thiếu hóa đơn
    pl_rule = next(r for r in sea if (r["doc_type"], r["direction"]) == ("packing_list", "import"))
    _error(client.post(f"{REQ}/{pl_rule['id']}:deactivate", headers=h), 403, "FORBIDDEN")
    off = client.post(f"{REQ}/{pl_rule['id']}:deactivate", headers=world["h"]["manager"])
    assert off.status_code == 200 and off.json()["is_active"] is False
    _error(
        client.post(f"{REQ}/{pl_rule['id']}:deactivate", headers=world["h"]["admin"]), 409, "INVALID_STATE_TRANSITION"
    )
    c2 = _staff_upload(client, h, sid, "bill_of_lading")["check"]
    assert [a["doc_type"] for a in c2["auto_missing"]] == ["commercial_invoice"]
    # thêm quy tắc mới (C/O cho nhập khẩu đường biển); trùng → 409
    new = {"transport_mode": "sea_fcl", "direction": "import", "doc_type": "certificate_of_origin"}
    assert client.post(REQ, json=new, headers=world["h"]["admin"]).status_code == 201
    _error(client.post(REQ, json=new, headers=world["h"]["manager"]), 409, "DUPLICATE")
    _error(client.post(REQ, json=new, headers=h), 403, "FORBIDDEN")
    pending = client.get(f"{OPS}/document-checks/{c2['id']}", headers=h).json()
    assert [a["doc_type"] for a in pending["auto_missing"]] == ["certificate_of_origin", "commercial_invoice"]
    assert [a.action for a in _logs("document_requirement.create")] == ["document_requirement.create"]
    assert len(_logs("document_requirement.deactivate")) == 1


def test_rules_follow_transport_mode(client, world):
    s = _create(client, world, transport_mode="air")
    sid, h = s["id"], world["h"]["ops_staff"]
    _act(client, world, sid, "submit")
    check = _staff_upload(client, h, sid, "commercial_invoice")["check"]
    assert [a["doc_type"] for a in check["auto_missing"]] == ["air_waybill", "packing_list"]
    domestic = _create(client, world, direction="domestic", incoterm=None, transport_mode="road")
    _act(client, world, domestic["id"], "submit")
    c = _staff_upload(client, h, domestic["id"], "other")["check"]
    assert [a["doc_type"] for a in c["auto_missing"]] == ["delivery_order"]
