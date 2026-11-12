# ruff: noqa: F811  (fixture world nhập từ module test khác)
"""Giai đoạn 22 – /api/ai: chỉ ops_staff, lỗi model trả 502/504, không còn endpoint đọc bill bằng AI."""

import httpx

from app import ai_gateway
from tests.flows.operations.test_shipments import _create, world  # noqa: F401


def _llama_ok(monkeypatch, answer="Trả lời thử"):
    request = httpx.Request("POST", "http://test")
    resp = httpx.Response(200, json={"choices": [{"message": {"content": answer}}]}, request=request)
    monkeypatch.setattr(ai_gateway.httpx, "post", lambda *a, **k: resp)


def test_ask_requires_ops_staff(client, world, monkeypatch):
    _llama_ok(monkeypatch)
    assert client.post("/api/ai/ask", json={"prompt": "hi"}).status_code == 401
    for role in ("customer", "manager"):
        assert client.post("/api/ai/ask", json={"prompt": "hi"}, headers=world["h"][role]).status_code == 403
    r = client.post("/api/ai/ask", json={"prompt": "hi"}, headers=world["h"]["ops_staff"])
    assert (r.status_code, r.json()) == (200, {"answer": "Trả lời thử"})


def test_ask_validates_prompt(client, world):
    h = world["h"]["ops_staff"]
    assert client.post("/api/ai/ask", json={"prompt": ""}, headers=h).status_code == 422
    assert client.post("/api/ai/ask", json={"prompt": "x" * 4001}, headers=h).status_code == 422


def test_model_down_returns_typed_error(client, world, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("tắt")

    monkeypatch.setattr(ai_gateway.httpx, "post", boom)
    r = client.post("/api/ai/ask", json={"prompt": "hi"}, headers=world["h"]["ops_staff"])
    assert r.status_code == 502 and r.json()["error"]["code"] == "INTERNAL_ERROR"

    def slow(*a, **k):
        raise httpx.ReadTimeout("chậm")

    monkeypatch.setattr(ai_gateway.httpx, "post", slow)
    r = client.post("/api/ai/ask", json={"prompt": "hi"}, headers=world["h"]["ops_staff"])
    assert r.status_code == 504


def test_track_returns_real_shipment_data(client, world):
    s = _create(client, world)
    h = world["h"]["ops_staff"]
    r = client.get(f"/api/ai/track/{s['id']}", headers=h)
    assert r.status_code == 200
    assert r.json()["shipment_id"] == s["id"] and r.json()["status"] == s["status"]
    assert client.get("/api/ai/track/999999", headers=h).status_code == 404


def test_bill_reading_endpoints_removed(client, world):
    h = world["h"]["ops_staff"]
    for path in ("/api/ai/read-bill", "/api/ai/read-bill-upload"):
        assert client.post(path, json={}, headers=h).status_code in (404, 405)
