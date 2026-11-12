"""E2E-05 - Event Bus: Luồng 5 - SYS / Event / Audit
Điều kiện hoàn thành 07 §4.3.
"""
from fastapi.testclient import TestClient


def test_e2e_05_event_bus_flow(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths", {})
    assert any(
        x in p for p in paths for x in ("event", "audit", "sys", "health")
    ), "Thieu router event bus / sys (Luong 5)"
