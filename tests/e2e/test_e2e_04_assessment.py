"""E2E-04 - Assessment: Luồng 4 - KPI / ASM
Điều kiện hoàn thành 07 §4.3.
"""
from fastapi.testclient import TestClient


def test_e2e_04_assessment_flow(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths", {})
    assert any(
        x in p for p in paths for x in ("assess", "kpi", "asm", "score")
    ), "Thieu router assessment (Luong 4)"
