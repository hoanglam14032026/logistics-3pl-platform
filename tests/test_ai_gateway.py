"""Giai đoạn 22 – gateway model local: thành công, hết thời gian, model tắt, dữ liệu sai dạng (không gọi mạng thật)."""

import httpx
import pytest

from app import ai_gateway
from app.core.errors import AppError, ErrorCode


def _response(status: int = 200, json=None, text: str | None = None) -> httpx.Response:
    request = httpx.Request("POST", "http://test")
    if text is not None:
        return httpx.Response(status, text=text, request=request)
    return httpx.Response(status, json=json, request=request)


def _patch_post(monkeypatch, result):
    def fake(url, json=None, timeout=None):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ai_gateway.httpx, "post", fake)


def test_ask_logistics_ok(monkeypatch):
    _patch_post(monkeypatch, _response(json={"choices": [{"message": {"content": "Xin chào"}}]}))
    assert ai_gateway.ask_logistics("test") == "Xin chào"


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (httpx.ReadTimeout("chậm"), 504),
        (httpx.ConnectError("từ chối kết nối"), 502),
        (_response(500, json={"error": "x"}), 502),
        (_response(200, text="không phải json"), 502),
        (_response(200, json={"choices": []}), 502),
        (_response(200, json={"khac": 1}), 502),
    ],
)
def test_ask_logistics_errors_are_typed(monkeypatch, result, status):
    _patch_post(monkeypatch, result)
    with pytest.raises(AppError) as exc:
        ai_gateway.ask_logistics("test")
    assert (exc.value.status_code, exc.value.code) == (status, ErrorCode.INTERNAL_ERROR)


def test_embed_text_ok_and_errors(monkeypatch):
    _patch_post(monkeypatch, _response(json={"embedding": [0.1, 0.2]}))
    assert ai_gateway.embed_text("x") == [0.1, 0.2]
    for bad in (_response(json={"embedding": []}), _response(json={}), httpx.ConnectError("tắt")):
        _patch_post(monkeypatch, bad)
        with pytest.raises(AppError) as exc:
            ai_gateway.embed_text("x")
        assert exc.value.status_code == 502
