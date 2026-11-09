"""Danh mục model AI local `/api/v2/admin/ai-models` (chỉ admin) và `/api/v2/ai-models/active` (Giai đoạn 9)."""

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.shared.audit.models import SysAuditLog

ADMIN = "/api/v2/admin/ai-models"
ACTIVE = "/api/v2/ai-models/active"
BODY = {
    "code": "Qwen3-VL-8B",
    "display_name": "Qwen3-VL 8B (nhanh)",
    "model_id": "qwen3-vl:8b",
    "model_type": "vision",
}


@pytest.fixture()
def h(client, make_user, auth_headers):
    roles = ("admin", "manager", "ops_staff", "learner", "business_manager")
    return {r: auth_headers(make_user(f"u.{r}", roles=[r]).username) for r in roles}


def _actions(model_id: int) -> list[str]:
    with SessionLocal() as db:
        stmt = select(SysAuditLog.action).where(
            SysAuditLog.entity_type == "ai_model", SysAuditLog.entity_id == model_id
        )
        return list(db.scalars(stmt.order_by(SysAuditLog.id)))


def test_admin_crud_and_soft_delete(client, h):
    r = client.post(ADMIN, json=BODY, headers=h["admin"])
    assert r.status_code == 201, r.text
    m = r.json()
    assert (m["code"], m["provider"], m["endpoint"], m["is_active"]) == ("qwen3-vl-8b", "ollama", None, True)
    assert m["effective_endpoint"] == "http://localhost:11434"  # endpoint NULL = OLLAMA_URL
    assert client.post(ADMIN, json=BODY, headers=h["admin"]).status_code == 409  # trùng mã

    mid = m["id"]
    r = client.patch(
        f"{ADMIN}/{mid}",
        json={"endpoint": "http://ollama:11434", "description": "GPU 2"},
        headers={**h["admin"], "If-Match": '"1"'},
    )
    assert r.status_code == 200 and r.json()["endpoint"] == "http://ollama:11434" and r.headers["ETag"] == '"2"'
    assert (
        client.patch(f"{ADMIN}/{mid}", json={"description": "x"}, headers={**h["admin"], "If-Match": '"1"'}).status_code
        == 409
    )
    assert client.patch(f"{ADMIN}/{mid}", json={"code": "doi-ma"}, headers=h["admin"]).status_code == 422

    assert client.post(f"{ADMIN}/{mid}:deactivate", headers=h["admin"]).json()["is_active"] is False
    assert client.post(f"{ADMIN}/{mid}:deactivate", headers=h["admin"]).status_code == 409
    assert [x["code"] for x in client.get(ACTIVE, headers=h["ops_staff"]).json()] == []
    assert client.post(f"{ADMIN}/{mid}:activate", headers=h["admin"]).json()["is_active"] is True

    deleted = client.delete(f"{ADMIN}/{mid}", headers=h["admin"]).json()
    assert deleted["deleted_at"] and deleted["is_active"] is False
    assert client.get(ADMIN, headers=h["admin"]).json()["total"] == 0
    assert client.get(ADMIN, params={"include_deleted": True}, headers=h["admin"]).json()["total"] == 1
    assert client.post(f"{ADMIN}/{mid}:activate", headers=h["admin"]).status_code == 409
    assert client.delete(f"{ADMIN}/{mid}", headers=h["admin"]).status_code == 409
    assert _actions(mid) == [
        "ai_model.create",
        "ai_model.update",
        "ai_model.deactivate",
        "ai_model.activate",
        "ai_model.delete",
    ]


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.openai.com/v1",
        "https://generativelanguage.googleapis.com",
        "http://8.8.8.8:11434",
        "http://169.254.169.254/latest",  # metadata máy ảo đám mây
        "http://user:pass@localhost:11434",
        "ftp://localhost",
        "http://localhost:11434/?key=abc",
    ],
)
def test_external_endpoint_rejected(client, h, endpoint):
    r = client.post(ADMIN, json={**BODY, "endpoint": endpoint}, headers=h["admin"])
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "body.endpoint"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434",
        "http://10.0.0.5:11434",
        "http://gpu-01:11434",
        "http://ai.local:11434",
        "https://[fd00::5]:11434",
        "http://192.168.1.20",
    ],
)
def test_internal_endpoint_accepted(client, h, endpoint):
    assert client.post(ADMIN, json={**BODY, "endpoint": endpoint}, headers=h["admin"]).status_code == 201


def test_validation(client, h):
    for body in (
        {**BODY, "model_type": "chat"},
        {**BODY, "provider": "openai"},
        {**BODY, "supports_pdf": False, "supports_image": False, "supports_docx": False},
        {**BODY, "code": "có dấu"},
        {**BODY, "model_id": "qwen 3"},
    ):
        assert client.post(ADMIN, json=body, headers=h["admin"]).status_code == 422, body


def test_only_admin_manages_catalog_and_users_see_list_without_endpoint(
    client, h, make_party, make_contract, make_user, auth_headers
):
    m = client.post(ADMIN, json={**BODY, "endpoint": "http://10.0.0.5:11434"}, headers=h["admin"]).json()
    for role in ("manager", "ops_staff", "learner", "business_manager"):
        assert client.get(ADMIN, headers=h[role]).status_code == 403
        assert client.post(ADMIN, json={**BODY, "code": "x-" + role}, headers=h[role]).status_code == 403
        assert client.post(f"{ADMIN}/{m['id']}:deactivate", headers=h[role]).status_code == 403
        assert client.delete(f"{ADMIN}/{m['id']}", headers=h[role]).status_code == 403
    party = make_party("Cty KH", tax_code="0100000009")
    make_contract(party.id)
    customer = auth_headers(make_user("kh.a", roles=["customer"], party_id=party.id, email="kh@a.vn").username)
    for headers in (customer, h["learner"], h["business_manager"]):
        assert client.get(ACTIVE, headers=headers).status_code == 403
    [option] = client.get(ACTIVE, headers=h["ops_staff"]).json()
    assert set(option) == {
        "id",
        "code",
        "display_name",
        "model_type",
        "supports_pdf",
        "supports_image",
        "supports_docx",
        "description",
        "suggested",
    }  # không có endpoint, model_id, provider
    assert option["suggested"] is False


def test_suggested_model_is_only_a_hint(client, h, monkeypatch):
    from app.core.config import get_settings

    client.post(ADMIN, json=BODY, headers=h["admin"])
    client.post(ADMIN, json={**BODY, "code": "qwen3.8-27b", "model_id": "qwen3.8:27b"}, headers=h["admin"])
    monkeypatch.setenv("AI_MODEL_DEFAULT", "qwen3.8-27b")
    get_settings.cache_clear()
    options = {o["code"]: o["suggested"] for o in client.get(ACTIVE, headers=h["manager"]).json()}
    assert options == {"qwen3-vl-8b": False, "qwen3.8-27b": True}


def test_probe_checks_model_is_pulled(client, h, monkeypatch):
    from tests.shared.docai.conftest import FakeOllama

    fake = FakeOllama()
    try:
        ok = client.post(ADMIN, json={**BODY, "endpoint": fake.url}, headers=h["admin"]).json()
        missing = client.post(
            ADMIN,
            json={**BODY, "code": "chua-pull", "model_id": "llama9:70b", "endpoint": fake.url},
            headers=h["admin"],
        ).json()
        assert client.post(f"{ADMIN}/{ok['id']}:probe", headers=h["admin"]).json() == {
            "reachable": True,
            "model_available": True,
            "message": "Máy chủ model sẵn sàng",
        }
        r = client.post(f"{ADMIN}/{missing['id']}:probe", headers=h["admin"]).json()
        assert r["reachable"] is True and r["model_available"] is False and "ollama pull llama9:70b" in r["message"]
    finally:
        fake.close()
    down = client.post(f"{ADMIN}/{ok['id']}:probe", headers=h["admin"]).json()
    assert down["reachable"] is False
    assert client.post(f"{ADMIN}/{ok['id']}:probe", headers=h["manager"]).status_code == 403
