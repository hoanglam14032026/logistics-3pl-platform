"""
Gọi máy chủ model local Ollama (Giai đoạn 9) – **chỉ** địa chỉ localhost / mạng nội bộ, không API cloud nào.

- Địa chỉ: `endpoint` của model trong `ai_models`, NULL → `OLLAMA_URL`. Mỗi lần gọi: kiểm URL (netguard), phân giải DNS,
  mọi địa chỉ phải nội bộ, kết nối thẳng tới IP đã kiểm (https: kiểm chứng chỉ theo tên máy chủ).
- Dùng `http.client` trực tiếp: không đọc `HTTP(S)_PROXY` (không thể bị chuyển qua proxy ra ngoài), không theo
  chuyển hướng (3xx = lỗi), giới hạn dung lượng phản hồi.
- Không ghi nội dung gửi/nhận (văn bản chứng từ) vào log; lỗi chỉ ghi mã HTTP / loại lỗi.
"""

import base64
import http.client
import json
import socket
import ssl
from typing import Any

from app.core.config import get_settings
from app.core.netguard import ExternalAddressError, Target, check_internal_url, resolve_internal

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class OllamaError(Exception):
    """Lỗi gọi model. `transient`: lỗi kết nối/tạm thời – tác vụ nền thử lại; ngược lại không thử lại."""

    def __init__(self, message: str, transient: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.status = status


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Kết nối tới IP đã kiểm nhưng kiểm chứng chỉ TLS theo tên máy chủ gốc."""

    def __init__(self, ip: str, port: int, server_hostname: str, timeout: float) -> None:
        super().__init__(ip, port, timeout=timeout, context=ssl.create_default_context())
        self._server_hostname = server_hostname

    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._server_hostname)


def base_url(endpoint: str | None) -> str:
    return (endpoint or get_settings().ollama_url).rstrip("/")


def check_endpoint(url: str) -> Target:
    return check_internal_url(url, get_settings().ai_allowed_hosts)


def _connection(target: Target, timeout: float) -> http.client.HTTPConnection:
    """Kết nối tới địa chỉ nội bộ đầu tiên nhận kết nối (vd. localhost → 127.0.0.1 rồi ::1)."""
    last: OSError | None = None
    for ip in resolve_internal(target.host, target.port):
        if target.scheme == "https":
            conn = _PinnedHTTPSConnection(ip, target.port, target.host, timeout)
        else:
            conn = http.client.HTTPConnection(ip, target.port, timeout=timeout)
        try:
            conn.connect()
            return conn
        except OSError as exc:
            conn.close()
            last = exc
    raise OllamaError(f"Không kết nối được máy chủ model ({type(last).__name__})", transient=True) from last


def request(endpoint: str | None, method: str, path: str, payload: dict | None, timeout: float) -> dict[str, Any]:
    url = base_url(endpoint)
    try:
        target = check_endpoint(url)
        conn = _connection(target, timeout)
    except ExternalAddressError as exc:
        raise OllamaError(str(exc), transient=False) from exc
    body = json.dumps(payload).encode() if payload is not None else None
    host_header = target.host if ":" not in target.host else f"[{target.host}]"
    headers = {"Host": f"{host_header}:{target.port}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, target.base_path + path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, http.client.HTTPException) as exc:
        raise OllamaError(f"Không kết nối được máy chủ model ({type(exc).__name__})", transient=True) from exc
    finally:
        conn.close()
    if len(raw) > MAX_RESPONSE_BYTES:
        raise OllamaError("Phản hồi của máy chủ model quá lớn", transient=False)
    if resp.status != 200:
        detail = ""
        try:
            detail = str(json.loads(raw).get("error", ""))[:200]
        except (ValueError, AttributeError):
            pass
        transient = resp.status in (408, 429, 500, 502, 503, 504)
        raise OllamaError(f"Máy chủ model trả HTTP {resp.status} {detail}".strip(), transient, resp.status)
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise OllamaError("Phản hồi của máy chủ model không phải JSON", transient=False) from exc


def list_models(endpoint: str | None, timeout: float = 10) -> list[str]:
    data = request(endpoint, "GET", "/api/tags", None, timeout)
    return [m.get("name") or m.get("model") for m in data.get("models", [])]


def _b64(images: list[bytes]) -> list[str]:
    return [base64.b64encode(img).decode("ascii") for img in images]


def chat_json(
    endpoint: str | None,
    model: str,
    system: str,
    user: str,
    schema: dict,
    images: list[bytes] | None = None,
    timeout: float | None = None,
) -> str:
    """Gọi /api/chat với `format` = JSON Schema (đầu ra có cấu trúc), nhiệt độ 0. Trả chuỗi JSON của model.

    Máy chủ/runner không hỗ trợ đầu ra có cấu trúc (vd. runner MLX của Ollama trả `501 structured output is
    unavailable`): gọi lại một lần không có `format`, đưa JSON Schema vào lời nhắc – đầu ra vẫn được đọc, kiểm kiểu và
    chuẩn hóa từng trường như thường (app/shared/docai/extract.py)."""
    message: dict[str, Any] = {"role": "user", "content": user}
    if images:
        message["images"] = _b64(images)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, message],
        "format": schema,
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
    }
    timeout = timeout or get_settings().docai_llm_timeout_sec
    try:
        data = request(endpoint, "POST", "/api/chat", payload, timeout)
    except OllamaError as exc:
        unsupported = exc.status == 501 or (exc.status == 400 and "structured output" in str(exc).lower())
        if not unsupported:
            raise
        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        payload.pop("format")
        payload["messages"][0] = {
            "role": "system",
            "content": f"{system}\nTrả về đúng JSON theo JSON Schema sau, không kèm chữ nào khác:\n{schema_text}",
        }
        data = request(endpoint, "POST", "/api/chat", payload, timeout)
    content = (data.get("message") or {}).get("content")
    if not isinstance(content, str):
        raise OllamaError("Phản hồi của model thiếu message.content", transient=False)
    return content


def transcribe(endpoint: str | None, model: str, image: bytes, timeout: float | None = None) -> str:
    """OCR bằng model loại `ocr` qua Ollama: chép lại văn bản trên ảnh."""
    payload = {
        "model": model,
        "prompt": "Chép lại chính xác toàn bộ văn bản trong ảnh, giữ xuống dòng. Không giải thích.",
        "images": _b64([image]),
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
    }
    data = request(endpoint, "POST", "/api/generate", payload, timeout or get_settings().docai_llm_timeout_sec)
    text = data.get("response")
    if not isinstance(text, str):
        raise OllamaError("Phản hồi của model OCR thiếu response", transient=False)
    return text
