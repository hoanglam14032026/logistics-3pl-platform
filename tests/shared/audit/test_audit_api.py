"""API đọc nhật ký /api/v2/audit-logs – chỉ admin (06 §4, §5; 06 §2.3), trên PostgreSQL."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert

from app.core.db import SessionLocal
from app.shared.audit.models import SysAuditLog

URL = "/api/v2/audit-logs"
T0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


@pytest.fixture()
def admin(client, make_user, auth_headers):
    make_user("quantri", roles=["admin"])
    return auth_headers("quantri")


@pytest.fixture()
def logs(admin):
    """Nhật ký mẫu cố định thời gian (ghi thẳng – trigger chỉ chặn UPDATE/DELETE)."""
    rows = [
        dict(at=T0, actor_id=7, action="party.create", entity_type="party", entity_id=1, request_id="r1"),
        dict(
            at=T0 + timedelta(hours=1),
            actor_id=7,
            action="party.update",
            entity_type="party",
            entity_id=1,
            before={"name": "A"},
            after={"name": "B"},
            request_id="r2",
        ),
        dict(at=T0 + timedelta(hours=2), actor_id=8, action="party.create", entity_type="party", entity_id=2),
        dict(
            at=T0 + timedelta(hours=3),
            actor_id=None,
            action="auth.login_failed",
            entity_type="user",
            entity_id=None,
            after={"username": "x", "reason": "unknown_user"},
            ip="10.0.0.9",
        ),
        dict(at=T0 + timedelta(days=1), actor_id=8, action="user.deactivate", entity_type="user", entity_id=5),
    ]
    with SessionLocal() as db:
        db.execute(insert(SysAuditLog), rows)
        db.commit()


def _actions(client, admin, **params) -> list[str]:
    r = client.get(URL, params=params, headers=admin)
    assert r.status_code == 200, r.text
    return [i["action"] for i in r.json()["items"] if i["at"] < "2026-11-08"]  # bỏ dòng do chính test sinh (đăng nhập…)


@pytest.mark.parametrize(
    "role", ["ops_staff", "manager", "business_manager", "learner", "instructor", "content_editor"]
)
def test_only_admin(client, make_user, auth_headers, role):
    make_user("nguoi.dung", roles=[role])
    _error(client.get(URL, headers=auth_headers("nguoi.dung")), 403, "FORBIDDEN")


def test_anonymous(client):
    _error(client.get(URL), 401, "UNAUTHENTICATED")


def test_default_newest_first_and_paging(client, admin, logs):
    r = client.get(URL, params={"to": "2026-11-08T00:00:00Z", "page_size": 2}, headers=admin).json()
    assert r["total"] == 5 and r["page_size"] == 2
    assert [i["action"] for i in r["items"]] == ["user.deactivate", "auth.login_failed"]
    item = r["items"][1]
    assert item["at"] == "2026-11-01T11:00:00Z" and item["ip"] == "10.0.0.9" and item["actor_id"] is None
    assert _actions(client, admin, sort="at")[:2] == ["party.create", "party.update"]


def test_filters(client, admin, logs):
    assert _actions(client, admin, action="party.create") == ["party.create", "party.create"]
    assert _actions(client, admin, action="party.") == ["party.create", "party.update", "party.create"]
    assert _actions(client, admin, entity_type="party", entity_id=1) == ["party.update", "party.create"]
    assert _actions(client, admin, actor_id=8) == ["user.deactivate", "party.create"]
    assert _actions(client, admin, request_id="r2") == ["party.update"]
    assert _actions(client, admin, **{"from": "2026-11-01T09:00:00Z", "to": "2026-11-01T11:00:00Z"}) == [
        "party.create",
        "party.update",
    ]  # from gồm, to không gồm
    assert _actions(client, admin, **{"from": "2026-11-01T18:00:00+07:00", "to": "2026-11-02T00:00:00Z"}) == [
        "auth.login_failed",
    ]  # 18:00 giờ VN = 11:00Z (đúng mốc dòng login_failed) … đến hết ngày 01/11 UTC
    assert _actions(client, admin, action="party.", actor_id=7, entity_type="party") == ["party.update", "party.create"]
    assert _actions(client, admin, action="khong.co") == []


def test_like_wildcards_are_not_special(client, admin, logs):
    assert _actions(client, admin, action="party_") == []  # `_` không phải ký tự đại diện


@pytest.mark.parametrize(
    "params",
    [
        {"entity_id": 1},  # thiếu entity_type
        {"from": "2026-11-01T00:00:00"},  # không có múi giờ
        {"from": "2026-11-02T00:00:00Z", "to": "2026-11-01T00:00:00Z"},
        {"action": "Party Create"},
        {"action": "x" * 65},
        {"actor_id": 0},
        {"sort": "action"},
        {"page_size": 101},
        {"request_id": "a b"},
    ],
)
def test_validation(client, admin, params):
    _error(client.get(URL, params=params, headers=admin), 422, "VALIDATION_ERROR")


def test_get_one_and_404(client, admin, logs):
    first = client.get(URL, params={"sort": "at"}, headers=admin).json()["items"][0]
    r = client.get(f"{URL}/{first['id']}", headers=admin)
    assert r.status_code == 200 and r.json() == first
    _error(client.get(f"{URL}/999999", headers=admin), 404, "NOT_FOUND")


def test_read_only_api(client, admin, logs):
    first = client.get(URL, headers=admin).json()["items"][0]
    assert client.post(URL, json={}, headers=admin).status_code == 405
    assert client.delete(f"{URL}/{first['id']}", headers=admin).status_code == 405
    assert client.patch(f"{URL}/{first['id']}", json={}, headers=admin).status_code == 405


def test_real_actions_are_queryable(client, admin, make_party):
    party = make_party()
    r = client.patch(f"/api/v2/md/parties/{party.id}", json={"note": "Ghi chú"}, headers=admin)
    rid = r.headers["X-Request-ID"]
    items = client.get(URL, params={"request_id": rid}, headers=admin).json()["items"]
    assert [(i["action"], i["entity_id"], i["after"]) for i in items] == [
        ("party.update", party.id, {"note": "Ghi chú"})
    ]
