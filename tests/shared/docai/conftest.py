"""Fixture cho test AI local trích xuất chứng từ (Giai đoạn 9).

`fake_ollama`: máy chủ HTTP giả lập API Ollama chạy trên 127.0.0.1 (cổng ngẫu nhiên) – ứng dụng gọi qua client HTTP thật
(kiểm địa chỉ nội bộ, không proxy). `OLLAMA_URL` trỏ vào máy chủ này trong suốt test. Không cần model thật; test chạy
với model thật: `DOCAI_LIVE_OLLAMA=1` (tests/shared/docai/test_live_ollama.py).
"""

import io
import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.shared.ai_models import service as ai_service
from app.shared.ai_models.schemas import AiModelCreate
from app.shared.audit.models import SysAuditLog

JOBS = "/api/v2/doc-jobs"

PARTY_TEXT = (
    "GIẤY CHỨNG NHẬN ĐĂNG KÝ DOANH NGHIỆP\n"
    "Tên công ty: CÔNG TY TNHH XUẤT NHẬP KHẨU MINH AN\n"
    "Mã số doanh nghiệp: 0101234567\n"
    "Địa chỉ trụ sở: 12 Trần Hưng Đạo, Hoàn Kiếm, Hà Nội, Việt Nam\n"
    "Điện thoại: 024 3826 1234   Email: xnk@minhan.com\n"
    "Người đại diện: Nguyễn Thị Lan\n"
)


def answer(**fields) -> dict:
    """Đầu ra model: {key: {"value": v, "confidence": c}} từ key=(v, c)."""
    return {k: {"value": v, "confidence": c} for k, (v, c) in fields.items()}


PARTY_ANSWER = answer(
    party_type=("customer", 0.95),
    name=("CÔNG TY TNHH XUẤT NHẬP KHẨU MINH AN", 0.97),
    tax_code=("0101234567", 0.98),
    country_code=("VN", 0.95),  # "VN" không có trong văn bản (chỉ "Việt Nam") → hạ độ tin cậy
    address=("12 Trần Hưng Đạo, Hoàn Kiếm, Hà Nội, Việt Nam", 0.95),
    contact_name=("Nguyễn Thị Lan", 0.93),
    phone=("024 3826 1234", 0.96),
    email=("xnk@minhan.com", 0.97),
)


class FakeOllama:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.models = ["qwen3.8:27b", "qwen3-vl:8b", "glm-ocr:latest"]
        self.reply: dict | Callable[[dict], tuple[int, dict]] = PARTY_ANSWER
        self.structured_supported = True
        self.ocr_text = "VĂN BẢN OCR TỪ MODEL"
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def chats(self) -> list[dict]:
        return [body for path, body in self.requests if path == "/api/chat"]

    def _respond(self, path: str, body: dict) -> tuple[int, dict]:
        if path == "/api/tags":
            return 200, {"models": [{"name": n, "model": n} for n in self.models]}
        if path == "/api/generate":
            return 200, {"response": self.ocr_text, "done": True}
        if path == "/api/chat":
            if "format" in body and not self.structured_supported:
                return 501, {"error": "structured output is unavailable"}
            if callable(self.reply):
                return self.reply(body)
            return 200, {"message": {"role": "assistant", "content": json.dumps(self.reply, ensure_ascii=False)}}
        return 404, {"error": "not found"}

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # im lặng
                pass

            def _send(self, status: int, payload: dict) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.requests.append((self.path, {}))
                self._send(*fake._respond(self.path, {}))

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.requests.append((self.path, body))
                self._send(*fake._respond(self.path, body))

        return Handler


@pytest.fixture()
def fake_ollama(monkeypatch):
    fake = FakeOllama()
    monkeypatch.setenv("OLLAMA_URL", fake.url)
    get_settings.cache_clear()
    yield fake
    fake.close()


@pytest.fixture()
def make_model(client):
    def _make(code: str = "qwen3.8-27b", model_id: str = "qwen3.8:27b", model_type: str = "vision", **fields):
        data = AiModelCreate(
            code=code, display_name=fields.pop("display_name", code), model_id=model_id, model_type=model_type, **fields
        )
        with SessionLocal() as db:
            return ai_service.create_model(db, data, actor_id=0)

    return _make


@pytest.fixture()
def users(client, make_user, auth_headers):
    """Người dùng theo vai trò + header đăng nhập."""
    made = {
        "ops": make_user("nv.an", roles=["ops_staff"]),
        "ops2": make_user("nv.binh", roles=["ops_staff"]),
        "manager": make_user("ql.cuong", roles=["manager"]),
        "admin": make_user("qt.dung", roles=["admin"]),
        "learner": make_user("hv.em", roles=["learner"]),
    }
    return {"user": made, "h": {k: auth_headers(u.username) for k, u in made.items()}}


def upload(
    client, headers, model_id, content: bytes = PARTY_TEXT.encode(), filename="dkkd.txt", doc_type="party", **form
):
    data = {"doc_type": doc_type, **({"model_id": str(model_id)} if model_id is not None else {}), **form}
    return client.post(JOBS, data=data, files={"file": (filename, io.BytesIO(content))}, headers=headers)


def logs(action: str, entity_id: int | None = None) -> list[SysAuditLog]:
    with SessionLocal() as db:
        stmt = select(SysAuditLog).where(SysAuditLog.action == action)
        if entity_id is not None:
            stmt = stmt.where(SysAuditLog.entity_id == entity_id)
        return list(db.scalars(stmt.order_by(SysAuditLog.id)))
