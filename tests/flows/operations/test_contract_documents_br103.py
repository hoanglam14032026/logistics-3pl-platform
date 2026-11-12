# ruff: noqa: F811  (fixture world nhập từ module test khác)
"""Giai đoạn 23 (sửa) – giấy tờ cấp lô nhập tay đã xác nhận → chứng từ lô `ops_documents` (BR-103, EV-121)."""

import io

from sqlalchemy import select

from app.core.db import SessionLocal
from app.flows.operations.models import OpsDocument, OpsShipment
from app.shared.files.models import SysFile
from tests.flows.operations.test_contract_documents import BL, _declare, _png
from tests.flows.operations.test_documents_checkpoints import _act, _complete, _events
from tests.flows.operations.test_shipments import _create, world  # noqa: F401

OPS = "/api/v2/ops"


def _confirm_bl(client, h, doc_id, values=None):
    client.patch(f"{OPS}/contract-documents/{doc_id}", json={"values": values or BL}, headers=h)
    r = client.post(f"{OPS}/contract-documents/{doc_id}:confirm", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _bl_doc(client, world, sid, **kw):
    r = _declare(client, world["h"]["ops_staff"], world["contract_a"].id, "bill_of_lading", shipment_id=sid, **kw)
    assert r.status_code == 201, r.text
    return r.json()


def _shipment_docs(sid) -> list[OpsDocument]:
    with SessionLocal() as db:
        return list(db.scalars(select(OpsDocument).where(OpsDocument.shipment_id == sid).order_by(OpsDocument.id)))


def test_confirmed_bill_becomes_shipment_document_and_opens_check(client, world):
    h = world["h"]["ops_staff"]
    s = _create(client, world)
    d = _bl_doc(client, world, s["id"])
    before = len(_events("EV-121"))

    out = _confirm_bl(client, h, d["id"])
    sd = out["shipment_document"]
    assert sd["registered"] is True and sd["version_no"] == 1 and sd["check_id"]

    [doc] = _shipment_docs(s["id"])
    assert (doc.doc_type, doc.status, doc.channel, doc.source_contract_document_id) == (
        "bill_of_lading",
        "current",
        "staff",
        d["id"],
    )
    with SessionLocal() as db:  # bản sao file riêng của chứng từ lô, cùng nội dung
        copy, source = db.get(SysFile, doc.file_id), db.get(SysFile, d["file_id"])
        assert copy.id != source.id and copy.sha256 == source.sha256
        assert (copy.owner_type, copy.owner_id, copy.purpose) == ("shipment_document", doc.id, "shipment_document")
    ev = _events("EV-121")
    assert len(ev) == before + 1 and ev[-1]["data"]["doc_type"] == "bill_of_lading"

    # hiện trong danh sách chứng từ lô, tải được file; lần kiểm tra BR-103 chờ ghi kết quả
    listed = client.get(f"{OPS}/shipments/{s['id']}/documents", headers=h).json()
    assert [x["doc_type"] for x in listed] == ["bill_of_lading"]
    assert client.get(f"/api/v2/files/{doc.file_id}", headers=h).status_code == 200
    checks = client.get(f"{OPS}/shipments/{s['id']}/document-checks", headers=h).json()
    assert [(c["id"], c["status"]) for c in checks] == [(sd["check_id"], "pending")]


def test_br103_counts_entered_documents(client, world):
    """Nhập tay đủ bộ chứng từ → ghi kết quả kiểm tra → lô qua `docs_verified` (không cần tải lại file)."""
    h = world["h"]["ops_staff"]
    s = _create(client, world)
    sid = s["id"]
    assert _act(client, world, sid, "submit").status_code == 200
    _confirm_bl(client, h, _bl_doc(client, world, sid)["id"])
    for code, values in (
        ("commercial_invoice", None),
        ("packing_list", None),
    ):
        d = _declare(client, h, world["contract_a"].id, code, shipment_id=sid).json()
        form = client.get(f"{OPS}/contract-documents/{d['id']}", headers=h).json()["form"]
        filled = values or {
            f["key"]: {"date": "2026-11-10", "number": "1", "integer": "1"}.get(f["type"], "X")
            for f in form["fields"]
            if f["required"] and f["type"] not in ("array", "enum")
        }
        for f in form["fields"]:
            if f["required"] and f["type"] == "enum":
                filled[f["key"]] = f["values"][0]
        client.patch(f"{OPS}/contract-documents/{d['id']}", json={"values": filled}, headers=h)
        r = client.post(f"{OPS}/contract-documents/{d['id']}:confirm", headers=h)
        assert r.status_code == 200 and r.json()["shipment_document"]["registered"], r.text
    [check] = [
        c for c in client.get(f"{OPS}/shipments/{sid}/document-checks", headers=h).json() if c["status"] == "pending"
    ]
    types = sorted(d.doc_type for d in _shipment_docs(sid))
    assert types == ["bill_of_lading", "commercial_invoice", "packing_list"]
    r = _complete(client, h, check["id"], [{"doc_type": t, "outcome": "ok"} for t in types])
    assert r.status_code == 200 and r.json()["result"] == "passed", r.text
    r = client.post(f"{OPS}/shipments/{sid}:verify-documents", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "docs_verified", r.text


def test_reconfirm_same_file_is_not_duplicated_new_file_is_new_version(client, world):
    h = world["h"]["ops_staff"]
    s = _create(client, world)
    d = _bl_doc(client, world, s["id"])
    url = f"{OPS}/contract-documents/{d['id']}"
    _confirm_bl(client, h, d["id"])

    # mở lại, xác nhận lại cùng file → không thêm phiên bản
    etag = client.get(url, headers=h).headers["ETag"]
    assert (
        client.post(f"{url}:reopen", json={"reason": "Sửa số booking"}, headers={**h, "If-Match": etag}).status_code
        == 200
    )
    again = client.post(f"{url}:confirm", headers=h).json()["shipment_document"]
    assert (again["registered"], again["reason"]) == (False, "unchanged")
    assert len(_shipment_docs(s["id"])) == 1

    # mở lại, thay file khác → phiên bản 2, bản cũ superseded
    etag = client.get(url, headers=h).headers["ETag"]
    client.post(f"{url}:reopen", json={"reason": "Bản scan rõ hơn"}, headers={**h, "If-Match": etag})
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "gray").save(buf, format="PNG")
    etag = client.get(url, headers=h).headers["ETag"]
    r = client.put(
        f"{url}/file", files={"file": ("b.png", buf.getvalue(), "image/png")}, headers={**h, "If-Match": etag}
    )
    assert r.status_code == 200, r.text
    third = client.post(f"{url}:confirm", headers=h).json()["shipment_document"]
    assert third["registered"] is True and third["version_no"] == 2
    assert [(x.version_no, x.status) for x in _shipment_docs(s["id"])] == [(1, "superseded"), (2, "current")]


def test_not_registered_without_file_or_for_non_shipment_types(client, world):
    h = world["h"]["ops_staff"]
    s = _create(client, world)
    # không có file → xác nhận vẫn được, nêu lý do
    d = _bl_doc(client, world, s["id"], with_file=False)
    sd = _confirm_bl(client, h, d["id"])["shipment_document"]
    assert (sd["registered"], sd["reason"]) == (False, "no_file")
    # loại không thuộc chứng từ lô (booking) và hồ sơ cấp hợp đồng → không ghi, không có shipment_document
    booking = _declare(client, h, world["contract_a"].id, "booking_confirmation", shipment_id=s["id"]).json()
    form = client.get(f"{OPS}/contract-documents/{booking['id']}", headers=h).json()["form"]
    values = {f["key"]: "X" for f in form["fields"] if f["required"] and f["type"] in ("string", "text")}
    values.update({f["key"]: "2026-11-10" for f in form["fields"] if f["required"] and f["type"] == "date"})
    client.patch(f"{OPS}/contract-documents/{booking['id']}", json={"values": values}, headers=h)
    r = client.post(f"{OPS}/contract-documents/{booking['id']}:confirm", headers=h)
    assert r.status_code == 200 and r.json()["shipment_document"] is None, r.text
    assert _shipment_docs(s["id"]) == []


def test_confirm_after_docs_verified_reopens_shipment(client, world):
    h = world["h"]["ops_staff"]
    s = _create(client, world)
    sid = s["id"]
    assert _act(client, world, sid, "submit").status_code == 200
    first = _confirm_bl(client, h, _bl_doc(client, world, sid)["id"])["shipment_document"]
    with SessionLocal() as db:  # bỏ qua quy tắc chứng từ bắt buộc của lô mẫu: chỉ kiểm B/L
        from app.flows.operations.models import OpsDocumentRequirement

        for rule in db.scalars(select(OpsDocumentRequirement)):
            rule.is_active = False
        db.commit()
    assert _complete(client, h, first["check_id"], [{"doc_type": "bill_of_lading", "outcome": "ok"}]).status_code == 200
    assert client.post(f"{OPS}/shipments/{sid}:verify-documents", headers=h).json()["status"] == "docs_verified"
    # B/L sửa đổi (amended) nhập thành giấy tờ mới → lô quay về docs_collecting, chờ kiểm tra lại
    second = _bl_doc(client, world, sid)
    sd = _confirm_bl(client, h, second["id"], {**BL, "bl_no": "HLCU123-A"})["shipment_document"]
    assert sd["registered"] and sd["version_no"] == 2 and sd["shipment_status"] == "docs_collecting"
    with SessionLocal() as db:
        assert db.get(OpsShipment, sid).status == "docs_collecting"
    assert _png()  # helper dùng chung còn dùng được
