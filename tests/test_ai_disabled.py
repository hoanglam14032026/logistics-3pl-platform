"""AI_ENABLED=false (bản demo): endpoint AI/DocAI trả 503 tiếng Việt, không gọi máy chủ model."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

MESSAGE = "Bản demo tắt AI, vui lòng nhập tay"


@pytest.fixture
def demo_client(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "false")
    get_settings.cache_clear()

    def _no_network(*args, **kwargs):
        raise AssertionError("AI_ENABLED=false nhưng vẫn gọi mạng tới máy chủ model")

    monkeypatch.setattr(httpx, "post", _no_network)
    monkeypatch.setattr(httpx, "get", _no_network)
    yield TestClient(create_app())
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/ai/ask"),
        ("post", "/api/ai/embed"),
        ("get", "/api/ai/track/1"),
        ("get", "/api/v2/doc-jobs"),
        ("get", "/api/v2/doc-types"),
        ("get", "/api/v2/ai-models/active"),
        ("get", "/api/v2/admin/ai-models"),
    ],
)
def test_ai_endpoints_return_503(demo_client, method, path):
    kwargs = {"json": {"prompt": "xin chào"}} if method == "post" else {}
    r = getattr(demo_client, method)(path, **kwargs)
    assert r.status_code == 503, r.text
    assert MESSAGE in r.text


def test_non_ai_endpoints_still_work(demo_client):
    assert demo_client.get("/health").status_code == 200
