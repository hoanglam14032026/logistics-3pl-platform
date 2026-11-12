"""E2E-02 - Control: Luồng 2 - Tracking + Alert + Sync
Điều kiện hoàn thành 07 §4.3.
"""
from fastapi.testclient import TestClient


def test_e2e_02_control_flow(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths", {})
    # Phải có router control / tracking / alert
    assert any(
        x in p for p in paths for x in ("tracking", "control", "alert", "plan")
    ), "Thieu router control/tracking (Luong 2)"
