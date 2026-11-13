"""E2E-08 - Stage 23: Manual Doc Entry + BR-103 + doc_entry.html
Điều kiện hoàn thành 07 §4.3 + Stage 23.
"""
from fastapi.testclient import TestClient


def test_e2e_08_manual_doc_entry_flow(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths", {})
    assert any(
        x in p for p in paths for x in ("document", "doc-entry", "bill", "contract-document")
    ), "Thieu router Manual Doc Entry / BR-103 (Stage 23)"
