"""Gateway gọi model local (Qwen27B qua llama-server, embedding qua Ollama) cho trợ lý hỏi đáp.

Giai đoạn 23: **không còn đọc bill bằng AI** – dữ liệu chứng từ do nhân viên nhập tay (document_entry_*).

Lỗi gọi model không bị nuốt thành chuỗi: ném `AppError` mã `INTERNAL_ERROR` với HTTP 504 (hết thời gian) hoặc 502
(model tắt, trả lỗi, hoặc trả dữ liệu sai dạng) để API trả đúng mã lỗi. Hàm đồng bộ (httpx.post) – gọi từ endpoint
`def` (FastAPI chạy trong thread pool), không chặn event loop.
"""

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode

LLAMA_TIMEOUT_SEC = 60
EMBED_TIMEOUT_SEC = 30


def _post(url: str, payload: dict[str, Any], timeout: int, what: str) -> Any:
    try:
        r = httpx.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except httpx.TimeoutException as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, 504, f"{what} không phản hồi kịp ({timeout}s)") from exc
    except httpx.HTTPStatusError as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, 502, f"{what} trả lỗi HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:  # kết nối bị từ chối, trả không phải JSON…
        raise AppError(ErrorCode.INTERNAL_ERROR, 502, f"Không gọi được {what}: kiểm tra dịch vụ đã chạy chưa") from exc


def ask_logistics(prompt: str) -> str:
    settings = get_settings()
    data = _post(
        f"{settings.llama_url}/v1/chat/completions",
        {"messages": [{"role": "user", "content": prompt}]},
        LLAMA_TIMEOUT_SEC,
        "Model Qwen27B (llama-server)",
    )
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, 502, "Model Qwen27B trả dữ liệu sai dạng") from exc


def embed_text(text: str) -> list[float]:
    settings = get_settings()
    data = _post(
        f"{settings.ollama_url}/api/embeddings",
        {"model": "nomic-embed-text", "prompt": text},
        EMBED_TIMEOUT_SEC,
        "Model embedding (Ollama)",
    )
    embedding = data.get("embedding") if isinstance(data, dict) else None
    if not isinstance(embedding, list) or not embedding:
        raise AppError(ErrorCode.INTERNAL_ERROR, 502, "Model embedding trả dữ liệu sai dạng")
    return embedding
