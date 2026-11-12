"""E2E-06 - Master Data: MD / Party / Location / Process
Điều kiện hoàn thành 07 §4.3.
"""
from fastapi.testclient import TestClient

def test_e2e_06_master_data_flow(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths", {})
    assert any(
        x in p for p in paths for x in ("master", "party", "location", "process", "md")
    ), "Thieu router master data (MD)"