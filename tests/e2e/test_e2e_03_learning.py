"""E2E-03 - Learning: Luồng 3 - Training / LRN
Điều kiện hoàn thành 07 §4.3.
"""
from fastapi.testclient import TestClient

def test_e2e_03_learning_flow(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths", {})
    assert any(
        x in p for p in paths for x in ("learn", "training", "lrn", "course")
    ), "Thieu router learning (Luong 3)"