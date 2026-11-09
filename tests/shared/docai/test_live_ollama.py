"""Chạy thật với Ollama + model local (Giai đoạn 9) – bỏ qua mặc định (chậm, cần GPU/RAM, cần model đã pull).

    DOCAI_LIVE_OLLAMA=1 [DOCAI_LIVE_MODEL=qwen3.8:27b] [OLLAMA_URL=http://localhost:11434] \\
        pytest tests/shared/docai/test_live_ollama.py

Ảnh scan giấy ĐKKD tiếng Việt → Tesseract vie+eng → model local → bản nháp party → duyệt → tạo md_parties nối job.
"""

import io
import os

import pytest
from PIL import Image, ImageDraw, ImageFont

from tests.shared.docai.conftest import JOBS, upload

pytestmark = pytest.mark.skipif(os.environ.get("DOCAI_LIVE_OLLAMA") != "1", reason="DOCAI_LIVE_OLLAMA=1 để chạy")

LINES = [
    "GIẤY CHỨNG NHẬN ĐĂNG KÝ DOANH NGHIỆP",
    "Tên công ty: CÔNG TY TNHH XUẤT NHẬP KHẨU MINH AN",
    "Mã số doanh nghiệp: 0101234567",
    "Địa chỉ: 12 Trần Hưng Đạo, Hoàn Kiếm, Hà Nội",
    "Điện thoại: 024 3826 1234",
    "Người đại diện theo pháp luật: Nguyễn Thị Lan",
]


def _scan() -> bytes:
    img = Image.new("RGB", (1600, 120 + 90 * len(LINES)), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default(size=40)
    for i, line in enumerate(LINES):
        draw.text((50, 50 + 90 * i), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_real_model_end_to_end(client, users, make_model):
    model = make_model(model_id=os.environ.get("DOCAI_LIVE_MODEL", "qwen3.8:27b"))
    r = upload(client, users["h"]["ops"], model.id, content=_scan(), filename="dkkd.jpg")
    assert r.status_code == 202, r.text
    job = client.get(f"{JOBS}/{r.json()['id']}", headers=users["h"]["ops"]).json()
    assert job["status"] in ("ready_for_review", "needs_review"), job["last_error"]
    values = {f["key"]: f["value"] for f in job["draft"]}
    assert values["tax_code"] == "0101234567"
    assert "MINH AN" in (values["name"] or "").upper()
    low = job["review_reasons"]["low_confidence"]
    r = client.post(f"{JOBS}/{job['id']}/approve", json={"confirmed_fields": low}, headers=users["h"]["ops"])
    assert r.status_code == 200, r.text
    form = {**r.json()["form"], "party_type": "customer"}
    r = client.post(f"/api/v2/md/parties?source_doc_job_id={job['id']}", json=form, headers=users["h"]["manager"])
    assert r.status_code == 201, r.text
    print("\nBản nháp:", {f["key"]: (f["value"], f["confidence"]) for f in job["draft"]})
