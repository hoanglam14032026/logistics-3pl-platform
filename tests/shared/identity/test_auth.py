"""Test tích hợp /api/v2/auth trên PostgreSQL (06 §3, bước 0.6): đăng nhập, khóa tạm, refresh token, đổi mật khẩu."""

from datetime import timedelta

import jwt
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.core.security import JWT_ISSUER
from app.core.timeutil import now_utc
from app.shared.identity.models import SysRefreshToken, SysUser
from app.shared.identity.service import ACCOUNT_DISABLED, ACCOUNT_LOCKED, INVALID_CREDENTIALS
from tests.conftest import DEFAULT_PASSWORD

LOGIN = "/api/v2/auth/login"
REFRESH = "/api/v2/auth/refresh"
LOGOUT = "/api/v2/auth/logout"
ME = "/api/v2/auth/me"
CHANGE = "/api/v2/auth/change-password"


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    err = r.json()["error"]
    assert err["code"] == code
    assert err["request_id"] == r.headers["X-Request-ID"]
    return err


def _post_login(client, username: str, password: str = DEFAULT_PASSWORD):
    return client.post(LOGIN, json={"username": username, "password": password})


def _set_user(username: str, **values) -> None:
    with SessionLocal() as db:
        db.execute(update(SysUser).where(SysUser.username == username).values(**values))
        db.commit()


def _get_user(username: str) -> SysUser:
    with SessionLocal() as db:
        return db.scalar(select(SysUser).where(SysUser.username == username))


def _tokens_of(username: str) -> list[SysRefreshToken]:
    with SessionLocal() as db:
        user = db.scalar(select(SysUser).where(SysUser.username == username))
        return list(
            db.scalars(select(SysRefreshToken).where(SysRefreshToken.user_id == user.id).order_by(SysRefreshToken.id))
        )


# --- Đăng nhập -------------------------------------------------------------------------------------------


def test_login_returns_tokens_and_me_works(client, make_user):
    make_user("anh", roles=["manager", "ops_staff"], email="anh@example.vn")
    r = _post_login(client, "anh")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store"
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60 and body["refresh_expires_in"] == 7 * 24 * 3600

    me = client.get(ME, headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    data = me.json()
    assert (data["username"], data["roles"], data["is_2fa_enabled"]) == ("anh", ["manager", "ops_staff"], False)
    assert "password_hash" not in data and "totp_secret" not in data
    assert data["last_login_at"].endswith("Z") and data["created_at"].endswith("Z")


def test_username_is_case_insensitive(client, make_user):
    make_user("binh")
    assert _post_login(client, "  BINH ").status_code == 200


def test_wrong_password_and_unknown_user_look_the_same(client, make_user):
    make_user("chi")
    wrong = _error(_post_login(client, "chi", "SaiMatKhau1"), 401, "UNAUTHENTICATED")
    unknown = _error(_post_login(client, "khong-ton-tai", "SaiMatKhau1"), 401, "UNAUTHENTICATED")
    assert wrong["message"] == unknown["message"] == INVALID_CREDENTIALS
    assert _post_login(client, "khong-ton-tai").headers["WWW-Authenticate"] == "Bearer"


def test_login_validation(client):
    _error(client.post(LOGIN, json={"username": "an"}), 422, "VALIDATION_ERROR")
    _error(client.post(LOGIN, json={"username": "an", "password": "x" * 129}), 422, "VALIDATION_ERROR")


def test_inactive_user_cannot_login(client, make_user):
    make_user("dung", is_active=False)
    assert _error(_post_login(client, "dung"), 403, "FORBIDDEN")["message"] == ACCOUNT_DISABLED
    assert _tokens_of("dung") == []


def test_successful_login_records_last_login_and_resets_counter(client, make_user):
    make_user("giang")
    _post_login(client, "giang", "SaiMatKhau1")
    assert _get_user("giang").failed_login_count == 1
    _post_login(client, "giang")
    user = _get_user("giang")
    assert user.failed_login_count == 0 and user.last_login_at is not None


# --- Khóa tạm ----------------------------------------------------------------------------------------------


def test_lockout_after_five_failures(client, make_user):
    make_user("hang")
    for _ in range(4):
        assert (
            _error(_post_login(client, "hang", "SaiMatKhau1"), 401, "UNAUTHENTICATED")["message"] == INVALID_CREDENTIALS
        )
    assert _error(_post_login(client, "hang", "SaiMatKhau1"), 401, "UNAUTHENTICATED")["message"] == ACCOUNT_LOCKED

    # Đang khóa: mật khẩu đúng cũng bị từ chối, không phát token
    assert _error(_post_login(client, "hang"), 401, "UNAUTHENTICATED")["message"] == ACCOUNT_LOCKED
    user = _get_user("hang")
    assert user.locked_until is not None
    assert timedelta(minutes=14) < user.locked_until - now_utc() <= timedelta(minutes=15)
    assert _tokens_of("hang") == []


def test_lock_expires_then_login_succeeds(client, make_user):
    make_user("hai")
    for _ in range(5):
        _post_login(client, "hai", "SaiMatKhau1")
    _set_user("hai", locked_until=now_utc() - timedelta(seconds=1))
    assert _post_login(client, "hai").status_code == 200
    user = _get_user("hai")
    assert user.locked_until is None and user.failed_login_count == 0


def test_after_lock_expires_counter_starts_over(client, make_user):
    make_user("hoa")
    for _ in range(5):
        _post_login(client, "hoa", "SaiMatKhau1")
    _set_user("hoa", locked_until=now_utc() - timedelta(seconds=1))
    # Hết hạn khóa: sai 1 lần chưa bị khóa lại
    assert _error(_post_login(client, "hoa", "SaiMatKhau1"), 401, "UNAUTHENTICATED")["message"] == INVALID_CREDENTIALS


def test_success_in_between_resets_failures(client, make_user):
    make_user("khanh")
    for _ in range(4):
        _post_login(client, "khanh", "SaiMatKhau1")
    assert _post_login(client, "khanh").status_code == 200
    for _ in range(4):
        _post_login(client, "khanh", "SaiMatKhau1")
    assert _get_user("khanh").locked_until is None


def test_lockout_threshold_and_duration_configurable(client, make_user, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("AUTH_MAX_FAILED_LOGINS", "2")
    monkeypatch.setenv("AUTH_LOCKOUT_MINUTES", "1")
    get_settings.cache_clear()
    make_user("lan")
    _post_login(client, "lan", "SaiMatKhau1")
    assert _error(_post_login(client, "lan", "SaiMatKhau1"), 401, "UNAUTHENTICATED")["message"] == ACCOUNT_LOCKED
    assert _get_user("lan").locked_until - now_utc() <= timedelta(minutes=1)


# --- Access token ----------------------------------------------------------------------------------------


def test_me_requires_valid_token(client, make_user):
    make_user("minh")
    _error(client.get(ME), 401, "UNAUTHENTICATED")
    _error(client.get(ME, headers={"Authorization": "Bearer abc"}), 401, "UNAUTHENTICATED")
    _error(client.get(ME, headers={"Authorization": "Basic YTpi"}), 401, "UNAUTHENTICATED")
    r = client.get(ME)
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_refresh_token_is_not_an_access_token(client, make_user, login):
    make_user("nam")
    refresh = login("nam")["refresh_token"]
    _error(client.get(ME, headers={"Authorization": f"Bearer {refresh}"}), 401, "UNAUTHENTICATED")


def test_expired_access_token(client, make_user):
    user = make_user("ngoc")
    past = now_utc() - timedelta(hours=1)
    token = jwt.encode(
        {"iss": JWT_ISSUER, "sub": str(user.id), "type": "access", "iat": past, "exp": past + timedelta(minutes=15)},
        "test-only-jwt-secret-" + "x" * 32,
        algorithm="HS256",
    )
    assert _error(client.get(ME, headers={"Authorization": f"Bearer {token}"}), 401, "UNAUTHENTICATED")["message"] == (
        "Phiên đăng nhập đã hết hạn"
    )


def test_deactivated_user_token_rejected_immediately(client, make_user, auth_headers):
    make_user("oanh")
    headers = auth_headers("oanh")
    assert client.get(ME, headers=headers).status_code == 200
    _set_user("oanh", is_active=False)
    _error(client.get(ME, headers=headers), 401, "UNAUTHENTICATED")


def test_roles_come_from_database_not_token(client, make_user, auth_headers):
    make_user("phuc", roles=["admin", "learner"])
    headers = auth_headers("phuc")
    assert client.get("/api/v2/users", headers=headers).status_code == 200
    with SessionLocal() as db:
        user = db.scalar(select(SysUser).where(SysUser.username == "phuc"))
        user.roles = [r for r in user.roles if r.role != "admin"]
        db.commit()
    # Token vẫn ghi roles=[admin, learner] nhưng quyền đọc lại từ CSDL
    _error(client.get("/api/v2/users", headers=headers), 403, "FORBIDDEN")


# --- Refresh token -----------------------------------------------------------------------------------------


def test_refresh_rotates_tokens(client, make_user, login):
    make_user("quang")
    first = login("quang")
    r = client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert client.get(ME, headers={"Authorization": f"Bearer {second['access_token']}"}).status_code == 200

    old, new = _tokens_of("quang")
    assert old.revoked_reason == "rotated" and old.replaced_by_id == new.id
    assert old.family_id == new.family_id and new.revoked_at is None
    assert first["refresh_token"] not in (old.token_hash, new.token_hash)  # chỉ lưu hash


def test_reusing_rotated_refresh_token_revokes_family(client, make_user, login):
    make_user("son")
    first = login("son")
    second = client.post(REFRESH, json={"refresh_token": first["refresh_token"]}).json()
    # Kẻ gian dùng lại token cũ → bị từ chối, và token mới của người dùng thật cũng bị thu hồi
    _error(client.post(REFRESH, json={"refresh_token": first["refresh_token"]}), 401, "UNAUTHENTICATED")
    _error(client.post(REFRESH, json={"refresh_token": second["refresh_token"]}), 401, "UNAUTHENTICATED")
    assert {t.revoked_reason for t in _tokens_of("son")} == {"rotated", "reuse_detected"}


def test_reuse_detection_does_not_touch_other_sessions(client, make_user, login):
    make_user("tam")
    phone = login("tam")
    laptop = login("tam")
    client.post(REFRESH, json={"refresh_token": phone["refresh_token"]})
    client.post(REFRESH, json={"refresh_token": phone["refresh_token"]})  # dùng lại → thu hồi họ token của phone
    assert client.post(REFRESH, json={"refresh_token": laptop["refresh_token"]}).status_code == 200


def test_expired_refresh_token(client, make_user, login):
    make_user("thu")
    tokens = login("thu")
    with SessionLocal() as db:
        db.execute(update(SysRefreshToken).values(expires_at=now_utc() - timedelta(seconds=1)))
        db.commit()
    err = _error(client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]}), 401, "UNAUTHENTICATED")
    assert err["message"] == "Phiên đăng nhập đã hết hạn"


def test_unknown_refresh_token(client):
    _error(client.post(REFRESH, json={"refresh_token": "khong-co"}), 401, "UNAUTHENTICATED")
    _error(client.post(REFRESH, json={}), 422, "VALIDATION_ERROR")


def test_refresh_rejected_for_deactivated_user(client, make_user, login):
    make_user("uyen")
    tokens = login("uyen")
    _set_user("uyen", is_active=False)
    _error(client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]}), 401, "UNAUTHENTICATED")
    assert _tokens_of("uyen")[0].revoked_reason == "deactivated"


def test_logout_revokes_session_and_is_idempotent(client, make_user, login):
    make_user("van")
    tokens = login("van")
    assert client.post(LOGOUT, json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    _error(client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]}), 401, "UNAUTHENTICATED")
    assert client.post(LOGOUT, json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post(LOGOUT, json={"refresh_token": "khong-co"}).status_code == 204
    assert _tokens_of("van")[0].revoked_reason == "logout"


# --- Đổi mật khẩu ----------------------------------------------------------------------------------------


def test_change_password(client, make_user, login):
    make_user("xuan")
    tokens = login("xuan")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    body = {"current_password": DEFAULT_PASSWORD, "new_password": "MatKhauMoi@2026"}
    assert client.post(CHANGE, json=body, headers=headers).status_code == 204

    # Access token cũ (phát trước khi đổi, có thể trong cùng một giây) và refresh token cũ hết hiệu lực ngay
    assert _error(client.get(ME, headers=headers), 401, "UNAUTHENTICATED")["message"] == (
        "Mật khẩu đã thay đổi; đăng nhập lại"
    )
    _error(client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]}), 401, "UNAUTHENTICATED")
    assert _tokens_of("xuan")[0].revoked_reason == "password_changed"
    _error(_post_login(client, "xuan"), 401, "UNAUTHENTICATED")
    new_login = _post_login(client, "xuan", "MatKhauMoi@2026")
    assert new_login.status_code == 200
    assert client.get(ME, headers={"Authorization": f"Bearer {new_login.json()['access_token']}"}).status_code == 200


def test_change_password_validation(client, make_user, auth_headers):
    make_user("yen")
    headers = auth_headers("yen")
    err = _error(
        client.post(
            CHANGE, json={"current_password": "Sai@2026xx", "new_password": "MatKhauMoi@2026"}, headers=headers
        ),
        422,
        "VALIDATION_ERROR",
    )
    assert err["details"][0]["field"] == "body.current_password"
    _error(
        client.post(CHANGE, json={"current_password": DEFAULT_PASSWORD, "new_password": "ngan"}, headers=headers),
        422,
        "VALIDATION_ERROR",
    )
    same = {"current_password": DEFAULT_PASSWORD, "new_password": DEFAULT_PASSWORD}
    _error(client.post(CHANGE, json=same, headers=headers), 422, "VALIDATION_ERROR")
    _error(
        client.post(CHANGE, json={"current_password": "x", "new_password": "MatKhauMoi@2026"}), 401, "UNAUTHENTICATED"
    )


def test_concurrent_failed_logins_still_lock(client, make_user):
    """SELECT … FOR UPDATE: 10 lần sai đồng thời không ghi đè bộ đếm của nhau – tài khoản vẫn bị khóa."""
    from concurrent.futures import ThreadPoolExecutor

    from app.core.errors import AppError
    from app.shared.identity import service

    make_user("song.song")

    def attempt(_):
        with SessionLocal() as db:
            try:
                service.login(db, "song.song", "SaiMatKhau1", service.ClientInfo())
            except AppError as exc:
                return exc.message

    with ThreadPoolExecutor(max_workers=10) as pool:
        messages = list(pool.map(attempt, range(10)))
    assert messages.count(ACCOUNT_LOCKED) >= 6  # lần thứ 5 khóa, các lần sau gặp trạng thái khóa
    assert _get_user("song.song").locked_until is not None
