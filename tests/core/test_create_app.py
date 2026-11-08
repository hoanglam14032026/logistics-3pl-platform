"""Test create_app() (07 §1): /health, CORS, request_id, định dạng lỗi thống nhất (06 §2.2).

Không vào lifespan (không dùng `with TestClient`) nên không cần CSDL.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.main import create_app

ORIGIN = "http://localhost:5173"


class _Payload(BaseModel):
    title: str


def _boom() -> None:
    raise RuntimeError("chi tiết nội bộ không được lộ ra")


def _conflict() -> None:
    raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409)


def _echo(payload: _Payload) -> dict:
    return payload.model_dump()


@pytest.fixture()
def app() -> FastAPI:
    settings = Settings(_env_file=None, jwt_secret="s" * 32, cors_origins=(ORIGIN,))
    application = create_app(settings)
    application.add_api_route("/_test/boom", _boom)
    application.add_api_route("/_test/conflict", _conflict)
    application.add_api_route("/_test/echo", _echo, methods=["POST"])
    return application


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _assert_error(r, status: int, code: str) -> dict:
    assert r.status_code == status
    err = r.json()["error"]
    assert err["code"] == code
    assert err["message"]
    assert isinstance(err["details"], list)
    assert err["request_id"] == r.headers["X-Request-ID"]
    return err


def test_create_app_returns_new_instance_each_call(app):
    assert isinstance(app, FastAPI)
    assert create_app(Settings(_env_file=None, jwt_secret="s" * 32)) is not app


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy"}
    assert r.headers["X-Request-ID"]


def test_legacy_routers_mounted_inside_create_app(app):
    paths = app.openapi()["paths"]
    legacy = (
        "/api/v1/guide",
        "/api/v1/training/documents",
        "/api/v1/business-data/records",
        "/api/v1/video/resources",
    )
    for p in legacy:
        assert paths[p]["get"]["deprecated"] is True


def test_request_id_reused_when_valid_and_replaced_when_not(client):
    assert client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"] == "abc-123"
    replaced = client.get("/health", headers={"X-Request-ID": "x\ny"}).headers["X-Request-ID"]
    assert replaced != "x\ny" and len(replaced) == 32


def test_cors_allowed_origin(client):
    r = client.get("/health", headers={"Origin": ORIGIN})
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert "X-Request-ID" in r.headers["access-control-expose-headers"]


def test_cors_preflight(client):
    r = client.options(
        "/api/v1/guide",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in r.headers["access-control-allow-methods"]


def test_cors_rejects_unknown_origin(client):
    r = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_header_on_error_response(client):
    r = client.get("/khong-ton-tai", headers={"Origin": ORIGIN})
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_not_found_uses_unified_format(client):
    _assert_error(client.get("/khong-ton-tai"), 404, "NOT_FOUND")


def test_validation_error_uses_unified_format(client):
    err = _assert_error(client.post("/_test/echo", json={}), 422, "VALIDATION_ERROR")
    assert err["details"] == [{"field": "body.title", "issue": "missing", "message": "Field required"}]


def test_app_error_uses_its_code(client):
    _assert_error(client.get("/_test/conflict"), 409, "INVALID_STATE_TRANSITION")


def test_unhandled_exception_returns_internal_error_without_leaking(client):
    r = client.get("/_test/boom")
    err = _assert_error(r, 500, "INTERNAL_ERROR")
    assert "chi tiết nội bộ" not in r.text
    assert err["details"] == []
