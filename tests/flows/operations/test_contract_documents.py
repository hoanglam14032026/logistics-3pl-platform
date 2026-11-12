# ruff: noqa: F811  (fixture world nhập từ module test khác)
"""Giai đoạn 23 – khai báo giấy tờ từ danh mục, nhập tay, tự động lưu theo hợp đồng (PostgreSQL thật)."""

import io

from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.flows.operations.models import OpsContractDocument, OpsShipment
from app.shared.audit.models import SysAuditLog
from tests.flows.operations.test_shipments import _create, _error, world  # noqa: F401

OPS = "/api/v2/ops"


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(buf, format="PNG")
    return buf.getvalue()


def _declare(client, h, contract_id, code, with_file=True, **data):
    files = {"file": ("scan.png", _png(), "image/png")} if with_file else None
    return client.post(
        f"{OPS}/service-contracts/{contract_id}/documents",
        data={"catalog_code": code, **{k: str(v) for k, v in data.items()}},
        files=files,
        headers=h,
    )


BL = {
    "bl_no": "HLCU123",
    "shipper_name": "ABC Co",
    "consignee_name": "XYZ Ltd",
    "port_of_loading": "VNHPH",
    "port_of_discharge": "USLAX",
    "issue_date": "2026-11-10",
}


def test_catalog_lists_ready_made_documents(client, world):
    h = world["h"]["ops_staff"]
    items = client.get(f"{OPS}/document-catalog", headers=h).json()
    codes = {i["code"] for i in items}
    assert {"bill_of_lading", "commercial_invoice", "customer_business_license", "partner_invoice"} <= codes
    only = client.get(f"{OPS}/document-catalog", params={"source": "customer"}, headers=h).json()
    assert only and {i["source"] for i in only} == {"customer"}
    detail = client.get(f"{OPS}/document-catalog/bill_of_lading", headers=h).json()
    assert any(f["key"] == "bl_no" and f["required"] for f in detail["form"]["fields"])
    assert client.get(f"{OPS}/document-catalog/khong_co", headers=h).status_code == 404
    assert client.get(f"{OPS}/document-catalog", headers=world["h"]["customer"]).status_code == 403


def test_declare_contract_level_customer_document_saved_to_contract(client, world):
    cid = world["contract_a"].id
    r = _declare(
        client, world["h"]["ops_staff"], cid, "customer_business_license", received_date="2026-11-12", note="Bản sao y"
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert (d["contract_id"], d["shipment_id"], d["status"], d["source"]) == (cid, None, "draft", "customer")
    assert d["party_id"] == world["a"].id and d["party_name"] == world["a"].name  # khách hàng của hợp đồng
    assert d["editable"] is True and d["file_id"] is not None and d["contract_version"] >= 1
    listed = client.get(f"{OPS}/service-contracts/{cid}/documents", headers=world["h"]["manager"]).json()
    assert [x["id"] for x in listed] == [d["id"]]
    # hợp đồng khác không thấy
    assert (
        client.get(f"{OPS}/service-contracts/{world['contract_b'].id}/documents", headers=world["h"]["manager"]).json()
        == []
    )


def test_declare_rules(client, world):
    h = world["h"]["ops_staff"]
    cid = world["contract_a"].id
    _error(_declare(client, h, cid, "khong_co"), 404, "NOT_FOUND")
    _error(_declare(client, h, cid, "bill_of_lading"), 422, "VALIDATION_ERROR")  # cấp lô: thiếu shipment_id
    s = _create(client, world)
    _error(_declare(client, h, cid, "customer_business_license", shipment_id=s["id"]), 422, "VALIDATION_ERROR")
    other = _create(client, world, contract="contract_b")
    _error(
        _declare(client, h, cid, "bill_of_lading", shipment_id=other["id"]), 422, "VALIDATION_ERROR"
    )  # lô hợp đồng khác
    _error(
        _declare(client, h, cid, "bill_of_lading", shipment_id=s["id"], party_id=world["a"].id), 422, "VALIDATION_ERROR"
    )
    ok = _declare(client, h, cid, "bill_of_lading", with_file=False, shipment_id=s["id"], party_id=world["seller"].id)
    assert ok.status_code == 201 and ok.json()["file_id"] is None and ok.json()["party_name"] == world["seller"].name
    # quyền: khách hàng không khai; nhân viên khác không phụ trách lô không khai hồ sơ cấp lô
    assert _declare(client, world["h"]["customer"], cid, "customer_business_license").status_code == 403
    assert _declare(client, world["h"]["ops_staff_2"], cid, "bill_of_lading", shipment_id=s["id"]).status_code == 403
    assert _declare(client, world["h"]["ops_staff_2"], cid, "customer_business_license").status_code == 201


def test_autosave_every_step_confirm_reopen_void(client, world):
    h = world["h"]["ops_staff"]
    s = _create(client, world)
    d = _declare(
        client, h, world["contract_a"].id, "bill_of_lading", shipment_id=s["id"], party_id=world["seller"].id
    ).json()
    url = f"{OPS}/contract-documents/{d['id']}"
    etag = client.get(url, headers=h).headers["ETag"]

    # mỗi bước nhập = một lần lưu; ETag đổi sau mỗi lần
    seen = {etag}
    for step in ({"bl_no": " HLCU123 "}, {"shipper_name": "ABC Co"}, {"packages": "12"}):
        r = client.patch(url, json={"values": step}, headers={**h, "If-Match": etag})
        assert r.status_code == 200, r.text
        etag = r.headers["ETag"]
        assert etag not in seen
        seen.add(etag)
    stored = client.get(url, headers=h).json()
    assert stored["values"] == {"bl_no": "HLCU123", "shipper_name": "ABC Co", "packages": 12}
    with SessionLocal() as db:  # đã nằm trong CSDL của hợp đồng
        row = db.get(OpsContractDocument, d["id"])
        assert (row.contract_id, row.data["bl_no"]) == (world["contract_a"].id, "HLCU123")

    # ghi lại giống hệt: không đổi phiên bản
    same = client.patch(url, json={"values": {"bl_no": "HLCU123"}}, headers={**h, "If-Match": etag})
    assert same.headers["ETag"] == etag
    _error(
        client.patch(url, json={"values": {"bl_no": "X"}}, headers={**h, "If-Match": '"1"'}), 409, "VERSION_CONFLICT"
    )
    _error(client.patch(url, json={"values": {"packages": "abc"}}, headers=h), 422, "VALIDATION_ERROR")
    _error(client.patch(url, json={"values": {"khong_co": "1"}}, headers=h), 422, "VALIDATION_ERROR")
    r = client.patch(url, json={"received_date": "2026-11-12", "note": "Gốc nhận 9h"}, headers=h)
    assert (r.json()["received_date"], r.json()["note"]) == ("2026-11-12", "Gốc nhận 9h")

    err = _error(client.post(f"{url}:confirm", headers=h), 422, "VALIDATION_ERROR")
    assert {x["field"] for x in err["details"]} >= {"values.consignee_name", "values.issue_date"}
    client.patch(url, json={"values": BL}, headers=h)
    r = client.post(f"{url}:confirm", headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["editable"], r.json()["missing_required"]) == ("confirmed", False, [])
    _error(client.patch(url, json={"values": {"bl_no": "Y"}}, headers=h), 409, "INVALID_STATE_TRANSITION")
    _error(client.post(f"{url}:void", json={"reason": "nhầm"}, headers=h), 409, "INVALID_STATE_TRANSITION")

    r = client.post(f"{url}:reopen", json={"reason": "Nhập nhầm số B/L"}, headers=h)
    assert r.json()["status"] == "draft" and r.json()["editable"] is True
    r = client.post(f"{url}:void", json={"reason": "Khai báo nhầm lô"}, headers=h)
    assert (r.json()["status"], r.json()["void_reason"]) == ("void", "Khai báo nhầm lô")
    _error(client.patch(url, json={"values": {"bl_no": "Z"}}, headers=h), 409, "INVALID_STATE_TRANSITION")

    with SessionLocal() as db:
        actions = [
            a.action for a in db.scalars(select(SysAuditLog).where(SysAuditLog.entity_type == "contract_document"))
        ]
        assert actions.count("contract_document.save") >= 5
        assert {
            "contract_document.declare",
            "contract_document.confirm",
            "contract_document.reopen",
            "contract_document.void",
        } <= set(actions)
        try:
            db.execute(text("DELETE FROM ops_contract_documents"))
            raise AssertionError("xóa được giấy tờ")
        except Exception as exc:
            assert "không được xóa" in str(exc)


def test_confirm_bill_fills_only_empty_shipment_fields(client, world):
    h = world["h"]["ops_staff"]
    s = _create(client, world)
    d = _declare(client, h, world["contract_a"].id, "bill_of_lading", shipment_id=s["id"]).json()
    with SessionLocal() as db:
        ship = db.get(OpsShipment, s["id"])
        before = (ship.etd, ship.incoterm)
    values = {**BL, "etd": "2026-12-01T08:00:00+07:00", "incoterm": "FOB"}
    client.patch(f"{OPS}/contract-documents/{d['id']}", json={"values": values}, headers=h)
    r = client.post(f"{OPS}/contract-documents/{d['id']}:confirm", headers=h)
    assert r.status_code == 200, r.text
    update = r.json()["shipment_update"]
    with SessionLocal() as db:
        ship = db.get(OpsShipment, s["id"])
        if before[0] is None:
            assert "etd" in update["filled"] and ship.etd is not None
        else:
            assert "etd" not in update["filled"] and ship.etd == before[0]  # không ghi đè
        assert ship.incoterm == (before[1] or "FOB")


def test_view_pages_and_attach_file_later(client, world):
    h = world["h"]["ops_staff"]
    d = _declare(client, h, world["contract_a"].id, "power_of_attorney", with_file=False).json()
    url = f"{OPS}/contract-documents/{d['id']}"
    _error(client.get(f"{url}/pages", headers=h), 404, "NOT_FOUND")  # chưa có file
    r = client.put(
        f"{url}/file",
        files={"file": ("poa.png", _png(), "image/png")},
        headers={**h, "If-Match": client.get(url, headers=h).headers["ETag"]},
    )
    assert r.status_code == 200 and r.json()["file_id"]
    pages = client.get(f"{url}/pages", headers=world["h"]["manager"]).json()
    assert (pages["page_count"], pages["pages"][0]["kind"]) == (1, "image")
    img = client.get(f"{url}/pages/1/image", headers=h)
    assert img.status_code == 200 and img.content.startswith(b"\x89PNG")
    assert client.get(f"{url}/pages/2/image", headers=h).status_code == 404
    assert client.get(f"{url}/pages", headers=world["h"]["customer"]).status_code == 403
    # file tải về được qua API file (kiểm quyền theo giấy tờ), customer không
    fid = r.json()["file_id"]
    assert client.get(f"/api/v2/files/{fid}", headers=world["h"]["manager"]).status_code == 200
    assert client.get(f"/api/v2/files/{fid}", headers=world["h"]["customer"]).status_code in (403, 404)
    _error(
        client.put(f"{url}/file", files={"file": ("x.exe", b"MZ\x90\x00", "application/octet-stream")}, headers=h),
        415,
        "UNSUPPORTED_FILE_TYPE",
    )


def test_autosave_with_unchanged_meta_writes_no_audit_or_version(client, world):
    """UI gửi kèm received_date / note / party_id mọi lần lưu: không đổi thì không ghi nhật ký, không tăng phiên bản."""
    h = world["h"]["ops_staff"]
    d = _declare(client, h, world["contract_a"].id, "customer_business_license", received_date="2026-11-12").json()
    url = f"{OPS}/contract-documents/{d['id']}"
    etag = client.get(url, headers=h).headers["ETag"]

    def saves():
        with SessionLocal() as db:
            return len(
                list(
                    db.scalars(
                        select(SysAuditLog).where(
                            SysAuditLog.entity_type == "contract_document",
                            SysAuditLog.action == "contract_document.save",
                        )
                    )
                )
            )

    r = client.patch(url, json={"note": "Gốc nhận 9h"}, headers={**h, "If-Match": etag})
    etag, before = r.headers["ETag"], saves()
    same = {"values": {}, "received_date": "2026-11-12", "note": "Gốc nhận 9h", "party_id": None}
    r = client.patch(url, json=same, headers={**h, "If-Match": etag})
    assert r.status_code == 200, r.text
    assert r.headers["ETag"] == etag and saves() == before
    # xóa ngày tiếp nhận: ghi nhật ký giá trị rỗng, không phải chuỗi "None"
    r = client.patch(url, json={"received_date": None}, headers={**h, "If-Match": etag})
    assert r.json()["received_date"] is None
    with SessionLocal() as db:
        last = db.scalars(
            select(SysAuditLog)
            .where(SysAuditLog.entity_type == "contract_document", SysAuditLog.action == "contract_document.save")
            .order_by(SysAuditLog.id.desc())
        ).first()
        assert last.after["changed"]["received_date"] == {"from": "2026-11-12", "to": None}


def test_replacing_file_keeps_old_file_and_marks_it_in_audit(client, world):
    """Thay file không xóa file cũ; nhật ký `contract_document.file` giữ file_id cũ (đánh dấu để truy vết / dọn sau)."""
    from app.shared.files.models import SysFile

    h = world["h"]["ops_staff"]
    d = _declare(client, h, world["contract_a"].id, "power_of_attorney").json()
    url = f"{OPS}/contract-documents/{d['id']}"
    old_id = d["file_id"]
    r = client.put(
        f"{url}/file",
        files={"file": ("moi.png", _png(), "image/png")},
        headers={**h, "If-Match": client.get(url, headers=h).headers["ETag"]},
    )
    new_id = r.json()["file_id"]
    assert old_id and new_id != old_id
    with SessionLocal() as db:
        assert db.get(SysFile, old_id) is not None  # file cũ còn nguyên
        entry = db.scalars(
            select(SysAuditLog)
            .where(SysAuditLog.entity_type == "contract_document", SysAuditLog.action == "contract_document.file")
            .order_by(SysAuditLog.id.desc())
        ).first()
        assert entry.before == {"file_id": old_id} and entry.after["file_id"] == new_id
