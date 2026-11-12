"""E2E-07 - AI Gateway: Stage 22 - AI / DocAI / Doc Entry
Điều kiện hoàn thành 07 §4.3 + Stage 22.
"""
from fastapi.testclient import TestClient


def test_e2e_07_ai_gateway_flow(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths", {})
    assert any(
        x in p for p in paths for x in ("ai", "doc", "gateway", "extract")
    ), "Thieu router AI Gateway / Doc Entry (Stage 22)"
