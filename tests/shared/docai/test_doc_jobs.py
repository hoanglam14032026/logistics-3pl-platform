"""Job trích xuất chứng từ AI local `/api/v2/doc-jobs` (Giai đoạn 9), trên PostgreSQL – model giả lập qua HTTP local.

Quy trình chuẩn: Upload → OCR → AI trích xuất → tự điền form → người duyệt xem, sửa → duyệt / từ chối; chưa duyệt không
tạo được bản ghi nghiệp vụ (`source_doc_job_id`).
"""

import io

import pytest
from docx import Document
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import SessionLocal
from app.shared.docai.models import SysDocExtraction, SysDocJob
from app.shared.files.models import SysFile
from app.shared.masterdata.models import MdParty
from tests.conftest import location_ids
from tests.shared.docai.conftest import JOBS, PARTY_ANSWER, PARTY_TEXT, answer, logs, upload


@pytest.fixture()
def model(fake_ollama, make_model):
    return make_model()


def _job(job_id: int) -> SysDocJob:
    with SessionLocal() as db:
        return db.get(SysDocJob, job_id)


def _done(client, headers, model_id, **kw) -> dict:
    r = upload(client, headers, model_id, **kw)
    assert r.status_code == 202, r.text
    job = client.get(f"{JOBS}/{r.json()['id']}", headers=headers)
    assert job.status_code == 200
    return job.json()


def _files(root) -> set:
    return {p for p in root.rglob("*") if p.is_file()} if root.exists() else set()


def _draft(job: dict) -> dict[str, dict]:
    return {f["key"]: f for f in job["draft"]}


# --- Upload → OCR → AI → bản nháp --------------------------------------------------------------------------------


def test_full_pipeline_creates_draft_with_confidence(client, users, model, fake_ollama, upload_dir):
    r = upload(client, {**users["h"]["ops"], "X-Request-ID": "req-docai-1"}, model.id)
    assert r.status_code == 202, r.text
    created = r.json()
    assert (
        created["status"] == "queued" and created["model_code"] == "qwen3.8-27b" and created["ai_model_id"] == model.id
    )
    assert r.headers["Location"] == f"{JOBS}/{created['id']}"
    # TestClient chạy tác vụ nền (không có JOB_QUEUE_URL) trước khi trả về – job đã trích xuất xong
    job = client.get(f"{JOBS}/{created['id']}", headers=users["h"]["ops"]).json()
    assert job["status"] == "needs_review" and job["queued_via"] == "background"
    draft = _draft(job)
    assert draft["name"]["value"] == "CÔNG TY TNHH XUẤT NHẬP KHẨU MINH AN" and draft["name"]["confidence"] == 0.97
    assert draft["name"]["grounded"] is True and draft["name"]["source"] == "ai"
    # "VN" không có trong văn bản → độ tin cậy bị hạ, cần người duyệt
    assert draft["country_code"]["grounded"] is False and draft["country_code"]["confidence"] == 0.5
    assert job["review_reasons"] == {"low_confidence": ["country_code"], "missing_required": []}
    assert job["form"] == {
        "party_type": "customer",
        "name": "CÔNG TY TNHH XUẤT NHẬP KHẨU MINH AN",
        "tax_code": "0101234567",
        "country_code": "VN",
        "address": "12 Trần Hưng Đạo, Hoàn Kiếm, Hà Nội, Việt Nam",
        "contact_name": "Nguyễn Thị Lan",
        "phone": "024 3826 1234",
        "email": "xnk@minhan.com",
    }
    assert job["create_hint"] is None  # chưa duyệt
    assert job["file"]["mime_type"] == "text/plain" and job["file"]["original_filename"] == "dkkd.txt"
    with SessionLocal() as db:
        assert db.scalar(select(MdParty)) is None  # tự điền form, KHÔNG tự lưu bảng nghiệp vụ

    # Model được gọi với schema JSON (format) + văn bản chứng từ; model text/vision đúng tên model đã chọn
    [chat] = fake_ollama.chats()
    assert chat["model"] == "qwen3.8:27b" and chat["format"]["required"][0] == "party_type"
    assert "0101234567" in chat["messages"][1]["content"] and chat["options"] == {"temperature": 0}

    # Lưu text + trang (txt: 1 trang) + đầu ra thô
    [run] = client.get(f"{JOBS}/{job['id']}/extractions", headers=users["h"]["ops"]).json()
    assert run["run_no"] == 1 and run["pages"][0]["source"] == "txt" and "MINH AN" in run["pages"][0]["text"]
    assert run["result"]["tax_code"] == {"value": "0101234567", "confidence": 0.98}

    # Nhật ký: ai upload, chọn model nào, AI trích ra gì – kèm request_id, ip của request tải lên
    [uploaded] = logs("file.uploaded", job["file_id"])
    [create] = logs("doc_job.create", job["id"])
    [extracted] = logs("doc_job.extracted", job["id"])
    assert uploaded.after["sha256"] == job["file"]["sha256"] and uploaded.actor_id == users["user"]["ops"].id
    assert create.after["model_code"] == "qwen3.8-27b" and create.after["model_ref"] == "qwen3.8:27b"
    assert extracted.after["fields"]["tax_code"] == {"value": "0101234567", "confidence": 0.98}
    for entry in (uploaded, create, extracted):
        assert entry.request_id == "req-docai-1" and entry.ip == "testclient"
    assert extracted.actor_id is None  # tác vụ nền

    # Tải lại file gốc: người tạo được, ops_staff khác không
    assert client.get(f"/api/v2/files/{job['file_id']}", headers=users["h"]["ops"]).content == PARTY_TEXT.encode()
    assert client.get(f"/api/v2/files/{job['file_id']}", headers=users["h"]["ops2"]).status_code == 404


def test_all_confident_is_ready_for_review_not_auto_approved(client, users, model, fake_ollama):
    fake_ollama.reply = {**PARTY_ANSWER, "country_code": {"value": None, "confidence": 0}}
    job = _done(client, users["h"]["ops"], model.id)
    assert job["status"] == "ready_for_review" and job["review_reasons"]["low_confidence"] == []
    assert job["approved_at"] is None and job["create_hint"] is None


def test_missing_required_field_needs_review(client, users, model, fake_ollama):
    fake_ollama.reply = {**PARTY_ANSWER, "name": {"value": None, "confidence": 0}}
    job = _done(client, users["h"]["ops"], model.id)
    assert job["status"] == "needs_review" and job["review_reasons"]["missing_required"] == ["name"]


def test_invalid_ai_values_are_dropped_with_issue(client, users, model, fake_ollama):
    fake_ollama.reply = {**PARTY_ANSWER, "party_type": {"value": "khach_quen", "confidence": 0.99}}
    draft = _draft(_done(client, users["h"]["ops"], model.id))
    assert draft["party_type"]["value"] is None and draft["party_type"]["confidence"] == 0
    assert "sai kiểu" in draft["party_type"]["issue"]


def test_docx_with_table_and_vision_model_receives_images(client, users, model, fake_ollama):
    doc = Document()
    doc.add_paragraph("CÔNG TY TNHH XUẤT NHẬP KHẨU MINH AN")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Mã số thuế", "0101234567"
    img = Image.new("RGB", (900, 120), "white")
    ImageDraw.Draw(img).text((10, 30), "Dien thoai 024 3826 1234", fill="black", font=ImageFont.load_default(size=40))
    pic = io.BytesIO()
    img.save(pic, format="PNG")
    doc.add_picture(io.BytesIO(pic.getvalue()))
    buf = io.BytesIO()
    doc.save(buf)
    job = _done(client, users["h"]["ops"], model.id, content=buf.getvalue(), filename="hd.docx")
    [run] = client.get(f"{JOBS}/{job['id']}/extractions", headers=users["h"]["ops"]).json()
    assert [p["source"] for p in run["pages"]] == ["docx_text", "docx_image"]
    assert "Mã số thuế | 0101234567" in run["pages"][0]["text"]
    assert run["pages"][1]["words"] and run["pages"][1]["words"][0]["b"]  # Tesseract: bbox từng từ
    [chat] = fake_ollama.chats()
    assert len(chat["messages"][1]["images"]) == 1  # model vision nhận ảnh trang


def test_scanned_image_is_ocred_with_tesseract_vietnamese(client, users, fake_ollama, make_model):
    text_model = make_model("qwen-text", "qwen3:8b", "text")
    img = Image.new("RGB", (1400, 300), "white")
    font = ImageFont.load_default(size=44)
    ImageDraw.Draw(img).text((40, 40), "CÔNG TY TNHH XUẤT NHẬP KHẨU MINH AN", fill="black", font=font)
    ImageDraw.Draw(img).text((40, 150), "Mã số thuế: 0101234567", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    job = _done(client, users["h"]["ops"], text_model.id, content=buf.getvalue(), filename="scan.tiff")
    [run] = client.get(f"{JOBS}/{job['id']}/extractions", headers=users["h"]["ops"]).json()
    page = run["pages"][0]
    assert page["source"] == "image" and "0101234567" in page["text"] and page["mean_confidence"] > 0.5
    assert all(len(w["b"]) == 4 for w in page["words"])
    [chat] = fake_ollama.chats()
    assert "images" not in chat["messages"][1]  # model text: chỉ văn bản OCR
    assert _draft(job)["tax_code"]["grounded"] is True


def test_ocr_model_from_catalog_replaces_tesseract(client, users, model, fake_ollama, make_model):
    ocr = make_model("glm-ocr", "glm-ocr:latest", "ocr")
    fake_ollama.ocr_text = PARTY_TEXT
    img = Image.new("RGB", (200, 80), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    job = _done(client, users["h"]["ops"], model.id, content=buf.getvalue(), filename="a.png", ocr_model_id=str(ocr.id))
    assert job["ocr_engine"] == "ollama" and job["ocr_model_id"] == ocr.id
    generate = [b for p, b in fake_ollama.requests if p == "/api/generate"]
    assert generate[0]["model"] == "glm-ocr:latest" and generate[0]["images"]
    assert _draft(job)["tax_code"]["grounded"] is True


def test_structured_output_unavailable_falls_back_to_schema_in_prompt(client, users, model, fake_ollama):
    """Runner MLX của Ollama: 501 structured output is unavailable → gọi lại không có `format`."""
    fake_ollama.structured_supported = False
    job = _done(client, users["h"]["ops"], model.id)
    assert job["status"] == "needs_review"
    first, second = fake_ollama.chats()
    assert "format" in first and "format" not in second and "JSON Schema" in second["messages"][0]["content"]


def test_model_output_in_code_fence_is_parsed(client, users, model, fake_ollama):
    import json

    fake_ollama.reply = lambda body: (
        200,
        {"message": {"content": "```json\n" + json.dumps(PARTY_ANSWER, ensure_ascii=False) + "\n```"}},
    )
    assert _draft(_done(client, users["h"]["ops"], model.id))["tax_code"]["value"] == "0101234567"


# --- Bắt buộc chọn model; model tắt / xóa ------------------------------------------------------------------------


def test_model_id_is_required_no_implicit_default(client, users, model, fake_ollama):
    r = upload(client, users["h"]["ops"], None)
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "body.model_id"
    assert fake_ollama.chats() == []


@pytest.mark.parametrize("state", ["inactive", "deleted", "ocr_type", "missing"])
def test_unusable_model_rejected_and_file_not_kept(client, users, fake_ollama, make_model, upload_dir, state):
    before = _files(upload_dir)
    m = make_model("m1", "qwen3.8:27b", "ocr" if state == "ocr_type" else "vision")
    admin = users["h"]["admin"]
    if state == "inactive":
        assert client.post(f"/api/v2/admin/ai-models/{m.id}:deactivate", headers=admin).status_code == 200
    if state == "deleted":
        assert client.delete(f"/api/v2/admin/ai-models/{m.id}", headers=admin).status_code == 200
    r = upload(client, users["h"]["ops"], 9999 if state == "missing" else m.id)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"][0]["field"] == "body.model_id"
    with SessionLocal() as db:
        assert db.scalar(select(SysFile)) is None and db.scalar(select(SysDocJob)) is None
    assert _files(upload_dir) == before


def test_capability_mismatch_rejected(client, users, fake_ollama, make_model, upload_dir):
    before = _files(upload_dir)
    m = make_model("text-only", "qwen3:8b", "text", supports_image=False, supports_pdf=False)
    img = io.BytesIO()
    Image.new("RGB", (50, 50)).save(img, format="PNG")
    r = upload(client, users["h"]["ops"], m.id, content=img.getvalue(), filename="a.png")
    assert r.status_code == 422 and r.json()["error"]["details"][0]["issue"] == "capability"
    assert _files(upload_dir) == before  # file vừa ghi bị xóa


def test_job_keeps_model_after_model_disabled_or_deleted(client, users, model, fake_ollama):
    job = _done(client, users["h"]["ops"], model.id)
    admin = users["h"]["admin"]
    assert (
        client.patch(f"/api/v2/admin/ai-models/{model.id}", json={"model_id": "qwen3.8:32b"}, headers=admin).status_code
        == 200
    )
    assert client.delete(f"/api/v2/admin/ai-models/{model.id}", headers=admin).status_code == 200
    again = client.get(f"{JOBS}/{job['id']}", headers=users["h"]["ops"]).json()
    assert (again["ai_model_id"], again["model_code"]) == (model.id, "qwen3.8-27b")
    assert _job(job["id"]).model_ref == "qwen3.8:27b"  # ảnh chụp lúc tạo, không theo bản sửa
    # model đã xóa: job cũ vẫn duyệt được (truy vết), job mới không chọn được
    assert (
        client.post(
            f"{JOBS}/{job['id']}/approve", json={"confirmed_fields": ["country_code"]}, headers=users["h"]["ops"]
        ).status_code
        == 200
    )
    assert upload(client, users["h"]["ops"], model.id).status_code == 422


# --- File: dùng lại shared/files ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "filename", "status"),
    [
        (b"", "rong.txt", 422),
        (b"MZ\x90\x00\x03\x00\x00\x00 chuong trinh", "hoadon.pdf", 415),
        (b"<html><script>alert(1)</script></html>", "ghichu.txt", 415),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, "hopdong.doc", 415),
    ],
)
def test_file_checks(client, users, model, content, filename, status):
    r = upload(client, users["h"]["ops"], model.id, content=content, filename=filename)
    assert r.status_code == status, r.text
    if filename.endswith(".doc"):
        assert "docx" in r.json()["error"]["message"]


def test_file_too_large_is_413(client, users, model, monkeypatch):
    monkeypatch.setenv("FILE_LIMITS_JSON", '{"doc_ai_source": {"max_mb": 0.001}}')
    from app.core.config import get_settings

    get_settings.cache_clear()
    r = upload(client, users["h"]["ops"], model.id, content=b"a" * 2000)
    assert r.status_code == 413


def test_unknown_doc_type(client, users, model):
    r = upload(client, users["h"]["ops"], model.id, doc_type="hoa_don_la")
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "body.doc_type"


# --- Người duyệt xem, sửa → duyệt / từ chối ----------------------------------------------------------------------


def test_review_edits_are_audited_with_ai_value(client, users, model):
    job = _done(client, users["h"]["ops"], model.id)
    h = {**users["h"]["manager"], "If-Match": f'"{job["row_version"]}"'}
    r = client.patch(f"{JOBS}/{job['id']}/review", json={"fields": {"country_code": "vn", "email": None}}, headers=h)
    assert r.status_code == 200, r.text
    after = r.json()
    draft = _draft(after)
    assert (draft["country_code"]["value"], draft["country_code"]["source"]) == ("vn", "human")
    assert draft["country_code"]["ai_value"] == "VN" and draft["email"]["value"] is None
    assert after["status"] == "ready_for_review" and after["review_reasons"]["low_confidence"] == []
    [entry] = logs("doc_job.review", job["id"])
    assert entry.actor_id == users["user"]["manager"].id
    assert entry.after["fields"]["email"] == {"ai": "xnk@minhan.com", "before": "xnk@minhan.com", "after": None}
    assert entry.after["fields"]["country_code"] == {"ai": "VN", "before": "VN", "after": "vn"}
    # If-Match cũ → 409; sai kiểu → 422; trường lạ → 422
    assert client.patch(f"{JOBS}/{job['id']}/review", json={"fields": {"name": "X"}}, headers=h).status_code == 409
    bad = client.patch(f"{JOBS}/{job['id']}/review", json={"fields": {"party_type": "abc"}}, headers=users["h"]["ops"])
    assert bad.status_code == 422 and bad.json()["error"]["details"][0]["field"] == "body.fields.party_type"
    unknown = client.patch(f"{JOBS}/{job['id']}/review", json={"fields": {"so_tk": 1}}, headers=users["h"]["ops"])
    assert unknown.status_code == 422


def test_needs_review_requires_confirming_low_confidence_fields(client, users, model):
    job = _done(client, users["h"]["ops"], model.id)
    r = client.post(f"{JOBS}/{job['id']}/approve", headers=users["h"]["ops"])
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "body.confirmed_fields.country_code"
    r = client.post(
        f"{JOBS}/{job['id']}/approve",
        json={"confirmed_fields": ["country_code"]},
        headers={**users["h"]["ops"], "If-Match": f'"{job["row_version"]}"'},
    )
    assert r.status_code == 200, r.text
    approved = r.json()
    assert approved["status"] == "approved" and approved["approved_by"] == users["user"]["ops"].id
    assert approved["approved_data"]["tax_code"] == "0101234567"
    assert approved["create_hint"] == {"method": "POST", "url": f"/api/v2/md/parties?source_doc_job_id={job['id']}"}
    [entry] = logs("doc_job.approve", job["id"])
    assert entry.after["confirmed_fields"] == ["country_code"] and entry.after["approved_data"]["name"]
    # đã duyệt: không sửa, duyệt lại, từ chối được
    jid = job["id"]
    assert client.post(f"{JOBS}/{jid}/approve", headers=users["h"]["ops"]).status_code == 409
    assert (
        client.patch(f"{JOBS}/{jid}/review", json={"fields": {"name": "B"}}, headers=users["h"]["ops"]).status_code
        == 409
    )
    assert client.post(f"{JOBS}/{jid}/reject", json={"reason": "Sai"}, headers=users["h"]["ops"]).status_code == 409


def test_missing_required_blocks_approval_until_filled(client, users, model, fake_ollama):
    fake_ollama.reply = {**PARTY_ANSWER, "name": {"value": None, "confidence": 0}}
    job = _done(client, users["h"]["ops"], model.id)
    r = client.post(
        f"{JOBS}/{job['id']}/approve", json={"confirmed_fields": ["country_code"]}, headers=users["h"]["ops"]
    )
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "body.fields.name"
    client.patch(f"{JOBS}/{job['id']}/review", json={"fields": {"name": "Cty Minh An"}}, headers=users["h"]["ops"])
    r = client.post(
        f"{JOBS}/{job['id']}/approve", json={"confirmed_fields": ["country_code"]}, headers=users["h"]["ops"]
    )
    assert r.status_code == 200


def test_reject_with_reason(client, users, model):
    job = _done(client, users["h"]["ops"], model.id)
    assert client.post(f"{JOBS}/{job['id']}/reject", json={"reason": ""}, headers=users["h"]["ops"]).status_code == 422
    r = client.post(
        f"{JOBS}/{job['id']}/reject", json={"reason": "Chứng từ mờ, chụp lại"}, headers=users["h"]["manager"]
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    [entry] = logs("doc_job.reject", job["id"])
    assert entry.after == {"status": "rejected", "reason": "Chứng từ mờ, chụp lại"}
    assert entry.actor_id == users["user"]["manager"].id


# --- Chưa duyệt → không tạo bản ghi nghiệp vụ; đã duyệt → nối source_doc_job_id ----------------------------------


def test_party_from_approved_job_only(client, users, model):
    job = _done(client, users["h"]["ops"], model.id)
    form = job["form"]
    url = f"/api/v2/md/parties?source_doc_job_id={job['id']}"
    r = client.post(url, json=form, headers=users["h"]["manager"])  # chưa duyệt
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "query.source_doc_job_id"
    with SessionLocal() as db:
        assert db.scalar(select(MdParty)) is None

    client.post(f"{JOBS}/{job['id']}/approve", json={"confirmed_fields": ["country_code"]}, headers=users["h"]["ops"])
    r = client.post(url, json={**form, "contact_name": "Trần Văn Ba"}, headers=users["h"]["manager"])
    assert r.status_code == 201, r.text
    party = r.json()
    assert party["source_doc_job_id"] == job["id"] and party["tax_code"] == "0101234567"
    linked = client.get(f"{JOBS}/{job['id']}", headers=users["h"]["manager"]).json()
    assert (linked["linked_entity_type"], linked["linked_entity_id"]) == ("party", party["id"])
    assert linked["create_hint"] is None
    [entry] = logs("doc_job.linked", job["id"])
    assert entry.after["linked_entity_id"] == party["id"] and entry.actor_id == users["user"]["manager"].id
    assert entry.after["overrides"] == {"contact_name": {"approved": "Nguyễn Thị Lan", "saved": "Trần Văn Ba"}}
    assert entry.request_id
    assert [e.action for e in logs("party.create")] == ["party.create"]

    # dùng lại job → 409, không tạo party thứ hai
    again = client.post(url, json={**form, "tax_code": "0109999999"}, headers=users["h"]["manager"])
    assert again.status_code == 409
    with SessionLocal() as db:
        assert len(db.scalars(select(MdParty)).all()) == 1


def test_party_creation_failure_rolls_back_link(client, users, model, make_party):
    make_party("Cty khác", tax_code="0101234567")  # trùng MST → tạo party lỗi 409
    job = _done(client, users["h"]["ops"], model.id)
    client.post(f"{JOBS}/{job['id']}/approve", json={"confirmed_fields": ["country_code"]}, headers=users["h"]["ops"])
    r = client.post(
        f"/api/v2/md/parties?source_doc_job_id={job['id']}", json=job["form"], headers=users["h"]["manager"]
    )
    assert r.status_code == 409
    assert _job(job["id"]).linked_entity_id is None and logs("doc_job.linked") == []


def test_wrong_target_and_invisible_job(client, users, model, make_party, make_contract):
    job = _done(client, users["h"]["ops"], model.id)
    client.post(f"{JOBS}/{job['id']}/approve", json={"confirmed_fields": ["country_code"]}, headers=users["h"]["ops"])
    party = make_party("Cty Minh An", tax_code="0100000001")
    body = {"customer_id": party.id, "valid_from": "2026-11-01", "valid_to": "2027-10-31"}
    r = client.post(
        f"/api/v2/ops/service-contracts?source_doc_job_id={job['id']}", json=body, headers=users["h"]["ops"]
    )
    assert r.status_code == 422 and "không dùng để tạo" in r.json()["error"]["message"]
    # ops_staff khác không thấy job → 404
    r = client.post(
        f"/api/v2/ops/service-contracts?source_doc_job_id={job['id']}", json=body, headers=users["h"]["ops2"]
    )
    assert r.status_code == 404


def test_contract_and_shipment_link(client, users, model, fake_ollama, make_party, make_contract):
    customer = make_party("Cty Minh An", tax_code="0100000001")
    fake_ollama.reply = answer(
        customer_name=("CÔNG TY TNHH MINH AN", 0.95),
        customer_contract_ref=("MA-2026/HD-07", 0.96),
        signed_date=("2026-11-01", 0.95),
        valid_from=("2026-11-01", 0.95),
        valid_to=("2027-10-31", 0.95),
        payment_due_days=(30, 0.92),
    )
    text = (
        "HỢP ĐỒNG DỊCH VỤ số MA-2026/HD-07 ký ngày 01/11/2026 với CÔNG TY TNHH MINH AN. "
        "Hiệu lực từ 01/11/2026 đến 31/10/2027. Thanh toán trong 30 ngày."
    )
    job = _done(client, users["h"]["ops"], model.id, content=text.encode(), doc_type="service_contract")
    assert job["status"] == "ready_for_review", job["review_reasons"]
    assert job["form"] == {
        "customer_contract_ref": "MA-2026/HD-07",
        "signed_date": "2026-11-01",
        "valid_from": "2026-11-01",
        "valid_to": "2027-10-31",
        "payment_due_days": 30,
    }
    first = client.post(f"{JOBS}/{job['id']}/approve", headers=users["h"]["ops"]).json()
    assert first["status"] == "pending_second_approval"  # hợp đồng: luôn duyệt 2 người (Giai đoạn 10)
    assert client.post(f"{JOBS}/{job['id']}/approve", headers=users["h"]["manager"]).json()["status"] == "approved"
    r = client.post(
        f"/api/v2/ops/service-contracts?source_doc_job_id={job['id']}",
        json={**job["form"], "customer_id": customer.id},
        headers=users["h"]["ops"],
    )
    assert r.status_code == 201, r.text
    assert r.json()["source_doc_job_id"] == job["id"]
    assert logs("doc_job.linked", job["id"])[0].after["overrides"] == {}

    contract = make_contract(customer.id)
    seller = make_party("Shanghai Export", party_type="foreign_seller", country_code="CN")
    fake_ollama.reply = answer(
        transport_doc_no=("MAEU123456789", 0.97),
        direction=("import", 0.95),
        transport_mode=("sea_fcl", 0.95),
        incoterm=("CIF", 0.95),
        shipper_name=("Shanghai Export", 0.95),
        consignee_name=("Minh An", 0.95),
        origin_location=("Shanghai", 0.95),
        destination_location=("Hai Phong", 0.95),
        etd=("2026-11-20", 0.95),
        gross_weight_kg=("18,500.50", 0.95),
    )
    bl = (
        "B/L MAEU123456789 Shipper Shanghai Export Consignee Minh An CIF Port of loading Shanghai "
        "discharge Hai Phong ETD 20/11/2026 GW 18,500.50 KGS"
    )
    sj = _done(client, users["h"]["ops"], model.id, content=bl.encode(), doc_type="shipment")
    d = _draft(sj)
    assert d["etd"]["value"] == "2026-11-20T00:00:00+07:00" and d["gross_weight_kg"]["value"] == "18500.50"
    assert sj["status"] == "ready_for_review", sj["review_reasons"]
    client.post(f"{JOBS}/{sj['id']}/approve", headers=users["h"]["ops"])
    body = {
        **sj["form"],
        "contract_id": contract.id,
        "shipper_id": seller.id,
        "consignee_id": customer.id,
        "required_delivery_at": "2026-12-31T00:00:00+07:00",
        **dict(zip(("origin_location_id", "destination_location_id"), location_ids(), strict=True)),
    }
    r = client.post(f"/api/v2/ops/shipments?source_doc_job_id={sj['id']}", json=body, headers=users["h"]["ops"])
    assert r.status_code == 201, r.text
    assert r.json()["source_doc_job_id"] == sj["id"]
    assert logs("doc_job.linked", sj["id"])[0].after["overrides"] == {}


# --- Phân quyền --------------------------------------------------------------------------------------------------


def test_rbac(client, users, model, make_user, make_party, auth_headers, make_contract):
    party = make_party("Cty KH", tax_code="0100000009")
    make_contract(party.id)
    customer = make_user("kh.a", roles=["customer"], party_id=party.id, email="kh@a.vn")
    hc = auth_headers(customer.username)
    job = _done(client, users["h"]["ops"], model.id)
    for h in (hc, users["h"]["learner"], users["h"]["admin"]):
        assert upload(client, h, model.id).status_code == 403
        assert client.get(f"{JOBS}/{job['id']}", headers=h).status_code == 403
        assert client.get(JOBS, headers=h).status_code == 403
        assert client.get("/api/v2/doc-types", headers=h).status_code in (403,)
    assert client.get(JOBS).status_code == 401
    # ops_staff: chỉ job của mình; manager: mọi job
    assert client.get(f"{JOBS}/{job['id']}", headers=users["h"]["ops2"]).status_code == 404
    assert client.get(JOBS, headers=users["h"]["ops2"]).json()["total"] == 0
    assert client.get(JOBS, headers=users["h"]["manager"]).json()["total"] == 1
    assert (
        client.post(f"{JOBS}/{job['id']}/reject", json={"reason": "abc"}, headers=users["h"]["ops2"]).status_code == 404
    )
    assert client.get(f"{JOBS}/{job['id']}/extractions", headers=users["h"]["ops2"]).status_code == 404


def test_doc_types_list(client, users):
    r = client.get("/api/v2/doc-types", headers=users["h"]["ops"])
    codes = {t["code"]: t for t in r.json()}
    assert {"party", "service_contract", "shipment", "commercial_invoice"} <= set(codes)
    assert codes["party"]["target"] == "party" and codes["party"]["fields"][0]["key"] == "party_type"
    assert client.get("/api/v2/doc-types/khong_co", headers=users["h"]["ops"]).status_code == 404


# --- Lỗi: model không kết nối được → failed + doc.failed, request gốc không lỗi ----------------------------------


def test_model_server_down_fails_job_without_failing_request(client, users, make_model, monkeypatch):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # cổng không có ai nghe
    monkeypatch.setenv("OLLAMA_URL", f"http://127.0.0.1:{port}")
    from app.core.config import get_settings

    get_settings.cache_clear()
    m = make_model()
    r = upload(client, users["h"]["ops"], m.id)
    assert r.status_code == 202
    job = client.get(f"{JOBS}/{r.json()['id']}", headers=users["h"]["ops"]).json()
    assert job["status"] == "failed" and "kết nối" in job["last_error"] and job["attempts"] == 1
    [failed] = logs("doc.failed", job["id"])
    assert failed.after["attempts"] == 1 and failed.after["in_worker"] is False
    assert client.post(f"{JOBS}/{job['id']}/approve", headers=users["h"]["ops"]).status_code == 409


def test_bad_model_output_fails_permanently(client, users, model, fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": "Xin lỗi, tôi không đọc được."}})
    job = _done(client, users["h"]["ops"], model.id)
    assert job["status"] == "failed" and "JSON" in job["last_error"]


# --- Bảo mật nội bộ ----------------------------------------------------------------------------------------------


def test_proxy_environment_is_ignored(client, users, model, fake_ollama, monkeypatch):
    """HTTP(S)_PROXY trỏ ra ngoài không làm dữ liệu đi qua proxy: client kết nối thẳng địa chỉ nội bộ."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://proxy.example.com:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    job = _done(client, users["h"]["ops"], model.id)
    assert job["status"] == "needs_review" and len(fake_ollama.chats()) == 1


def test_model_endpoint_resolving_outside_is_blocked_at_call_time(client, users, make_model, fake_ollama, monkeypatch):
    """Endpoint tên nội bộ nhưng DNS trả địa chỉ công khai → chặn lúc gọi, job failed, không gửi dữ liệu."""
    import socket as socket_module

    from app.core import netguard

    m = make_model(endpoint="http://ollama.internal:11434")
    real = socket_module.getaddrinfo

    def fake_getaddrinfo(host, *args, **kwargs):
        if host == "ollama.internal":
            return [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", ("93.184.216.34", 11434))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(netguard.socket, "getaddrinfo", fake_getaddrinfo)
    job = _done(client, users["h"]["ops"], m.id)
    assert job["status"] == "failed" and "ngoài mạng nội bộ" in job["last_error"]
    assert fake_ollama.chats() == []


def test_database_guards(client, users, model):
    job = _done(client, users["h"]["ops"], model.id)
    client.post(f"{JOBS}/{job['id']}/reject", json={"reason": "Sai loại"}, headers=users["h"]["ops"])
    with SessionLocal() as db:
        ext = db.scalar(select(SysDocExtraction.id))
    statements = [
        f"DELETE FROM sys_doc_jobs WHERE id = {job['id']}",
        f"UPDATE sys_doc_jobs SET model_ref = 'khac' WHERE id = {job['id']}",
        f"UPDATE sys_doc_jobs SET status = 'needs_review', rejected_at = NULL, reject_reason = NULL "
        f"WHERE id = {job['id']}",
        f"UPDATE sys_doc_extractions SET raw_output = 'sua' WHERE id = {ext}",
        f"DELETE FROM sys_doc_extractions WHERE id = {ext}",
        f"DELETE FROM ai_models WHERE id = {model.id}",
    ]
    for sql in statements:
        with SessionLocal() as db, pytest.raises(DBAPIError, match="RestrictViolation"):
            db.execute(text(sql))
            db.commit()
