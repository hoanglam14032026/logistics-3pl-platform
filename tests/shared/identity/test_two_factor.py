"""Xác thực 2 lớp TOTP (06 §3; 02 §9.2 – bắt buộc cho customer), trên PostgreSQL."""

from datetime import timedelta

import jwt
import pytest
from sqlalchemy import select, update

from app.core import totp
from app.core.db import SessionLocal
from app.core.security import JWT_ISSUER, create_access_token
from app.core.timeutil import now_utc
from app.shared.audit.models import SysAuditLog
from app.shared.identity import service
from app.shared.identity.models import SysRefreshToken, SysUser
from tests.conftest import DEFAULT_PASSWORD, totp_now

LOGIN, ME = "/api/v2/auth/login", "/api/v2/auth/me"
SECRET_KEY = "test-only-jwt-secret-" + "x" * 32


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _amr(access_token: str) -> list[str]:
    return jwt.decode(access_token, options={"verify_signature": False})["amr"]


def _user(username: str) -> SysUser:
    with SessionLocal() as db:
        return db.scalar(select(SysUser).where(SysUser.username == username))


def _password_login(client, username, password=DEFAULT_PASSWORD) -> dict:
    r = client.post(LOGIN, json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _enable_for(client, username) -> dict:
    """Người dùng nội bộ tự bật 2FA khi đã đăng nhập. Trả token mới (amr = pwd + otp)."""
    tokens = _password_login(client, username)
    setup = client.post("/api/v2/auth/2fa/setup", headers=_bearer(tokens["access_token"]))
    assert setup.status_code == 200, setup.text
    r = client.post(
        "/api/v2/auth/2fa/enable", json={"code": totp_now(username)}, headers=_bearer(tokens["access_token"])
    )
    assert r.status_code == 200, r.text
    return {"old": tokens, "new": r.json(), "setup": setup.json()}


# --- Người dùng nội bộ: tự bật / tắt ------------------------------------------------------------------------------


def test_internal_user_without_2fa_logs_in_directly(client, make_user):
    make_user("nhan.vien")
    body = _password_login(client, "nhan.vien")
    assert "access_token" in body and _amr(body["access_token"]) == ["pwd"]


def test_enable_2fa(client, make_user):
    make_user("nhan.vien")
    result = _enable_for(client, "nhan.vien")
    setup = result["setup"]
    assert setup["otpauth_uri"].startswith("otpauth://totp/Logistics%203PL%3Anhan.vien?secret=" + setup["secret"])
    assert _amr(result["new"]["access_token"]) == ["pwd", "otp"]
    # Token cũ chỉ có mật khẩu: hết dùng được ngay; phiên cũ bị thu hồi
    err = _error(client.get(ME, headers=_bearer(result["old"]["access_token"])), 401, "UNAUTHENTICATED")
    assert err["message"] == "Cần đăng nhập lại với xác thực 2 lớp"
    _error(
        client.post("/api/v2/auth/refresh", json={"refresh_token": result["old"]["refresh_token"]}),
        401,
        "UNAUTHENTICATED",
    )
    me = client.get(ME, headers=_bearer(result["new"]["access_token"])).json()
    assert me["is_2fa_enabled"] is True and "totp_secret" not in me
    # Làm mới phiên 2 lớp: giữ amr
    refreshed = client.post("/api/v2/auth/refresh", json={"refresh_token": result["new"]["refresh_token"]}).json()
    assert _amr(refreshed["access_token"]) == ["pwd", "otp"]


def test_login_with_totp(client, make_user):
    make_user("nhan.vien")
    _enable_for(client, "nhan.vien")
    challenge = _password_login(client, "nhan.vien")
    assert challenge["mfa_required"] is True and challenge["mfa_setup_required"] is False
    assert "access_token" not in challenge and challenge["expires_in"] == 300
    _error(client.get(ME, headers=_bearer(challenge["mfa_token"])), 401, "UNAUTHENTICATED")  # không phải access token
    r = client.post(
        "/api/v2/auth/login/totp", json={"mfa_token": challenge["mfa_token"], "code": totp_now("nhan.vien")}
    )
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert _amr(r.json()["access_token"]) == ["pwd", "otp"]
    assert client.get(ME, headers=_bearer(r.json()["access_token"])).status_code == 200


def test_totp_code_cannot_be_reused(client, make_user):
    make_user("nhan.vien")
    _enable_for(client, "nhan.vien")
    code = totp_now("nhan.vien")
    first = _password_login(client, "nhan.vien")
    assert (
        client.post("/api/v2/auth/login/totp", json={"mfa_token": first["mfa_token"], "code": code}).status_code == 200
    )
    second = _password_login(client, "nhan.vien")
    _error(
        client.post("/api/v2/auth/login/totp", json={"mfa_token": second["mfa_token"], "code": code}),
        401,
        "UNAUTHENTICATED",
    )


def test_wrong_codes_lock_account(client, make_user):
    make_user("nhan.vien")
    _enable_for(client, "nhan.vien")
    challenge = _password_login(client, "nhan.vien")
    messages = []
    for _ in range(5):
        r = client.post("/api/v2/auth/login/totp", json={"mfa_token": challenge["mfa_token"], "code": "000000"})
        messages.append(r.json()["error"]["message"])
    from app.shared.identity.twofactor import INVALID_CODE

    assert messages[:4] == [INVALID_CODE] * 4
    assert messages[-1] == service.ACCOUNT_LOCKED
    assert _user("nhan.vien").locked_until is not None
    # Đang khóa: đúng mã cũng không vào được
    _error(
        client.post(
            "/api/v2/auth/login/totp", json={"mfa_token": challenge["mfa_token"], "code": totp_now("nhan.vien")}
        ),
        401,
        "UNAUTHENTICATED",
    )
    with SessionLocal() as db:
        reasons = [
            r.after["reason"] for r in db.scalars(select(SysAuditLog).where(SysAuditLog.action == "auth.login_failed"))
        ]
    assert reasons.count("invalid_totp") == 5


def test_correct_password_does_not_reset_failures_before_second_factor(client, make_user):
    make_user("nhan.vien")
    _enable_for(client, "nhan.vien")
    for _ in range(3):
        client.post(LOGIN, json={"username": "nhan.vien", "password": "SaiMatKhau1"})
    _password_login(client, "nhan.vien")  # đúng mật khẩu, chưa qua mã
    assert _user("nhan.vien").failed_login_count == 3


def test_mfa_token_rules(client, make_user):
    make_user("nhan.vien")
    _enable_for(client, "nhan.vien")
    user = _user("nhan.vien")
    challenge = _password_login(client, "nhan.vien")
    # Sai mục đích: token verify không dùng cho setup
    _error(
        client.post("/api/v2/auth/login/2fa-setup", json={"mfa_token": challenge["mfa_token"]}), 401, "UNAUTHENTICATED"
    )
    # Hết hạn
    past = now_utc() - timedelta(minutes=10)
    expired = jwt.encode(
        {
            "iss": JWT_ISSUER,
            "sub": str(user.id),
            "type": "mfa",
            "purpose": "verify",
            "pwd_at": service.password_stamp(user),
            "iat": past,
            "exp": past + timedelta(minutes=5),
        },
        SECRET_KEY,
        algorithm="HS256",
    )
    err = _error(
        client.post("/api/v2/auth/login/totp", json={"mfa_token": expired, "code": totp_now("nhan.vien")}),
        401,
        "UNAUTHENTICATED",
    )
    assert err["message"] == "Hết thời gian nhập mã xác thực; đăng nhập lại"
    # Đổi mật khẩu sau bước 1 → mfa_token cũ vô hiệu
    with SessionLocal() as db:
        db.execute(update(SysUser).where(SysUser.id == user.id).values(password_changed_at=now_utc()))
        db.commit()
    _error(
        client.post(
            "/api/v2/auth/login/totp", json={"mfa_token": challenge["mfa_token"], "code": totp_now("nhan.vien")}
        ),
        401,
        "UNAUTHENTICATED",
    )
    for bad in ("12345", "abcdef", ""):
        _error(client.post("/api/v2/auth/login/totp", json={"mfa_token": "x", "code": bad}), 422, "VALIDATION_ERROR")


def test_enable_and_setup_errors(client, make_user, auth_headers):
    make_user("nhan.vien")
    headers = auth_headers("nhan.vien")
    _error(
        client.post("/api/v2/auth/2fa/enable", json={"code": "123456"}, headers=headers),
        409,
        "INVALID_STATE_TRANSITION",
    )
    client.post("/api/v2/auth/2fa/setup", headers=headers)
    _error(client.post("/api/v2/auth/2fa/enable", json={"code": "000000"}, headers=headers), 422, "VALIDATION_ERROR")
    assert _user("nhan.vien").is_2fa_enabled is False
    new = client.post("/api/v2/auth/2fa/enable", json={"code": totp_now("nhan.vien")}, headers=headers).json()
    _error(client.post("/api/v2/auth/2fa/setup", headers=_bearer(new["access_token"])), 409, "INVALID_STATE_TRANSITION")


def test_disable_2fa(client, make_user):
    make_user("nhan.vien")
    headers = _bearer(_enable_for(client, "nhan.vien")["new"]["access_token"])
    _error(
        client.post(
            "/api/v2/auth/2fa/disable", json={"password": "Sai@123456", "code": totp_now("nhan.vien")}, headers=headers
        ),
        422,
        "VALIDATION_ERROR",
    )
    _error(
        client.post("/api/v2/auth/2fa/disable", json={"password": DEFAULT_PASSWORD, "code": "000000"}, headers=headers),
        422,
        "VALIDATION_ERROR",
    )
    r = client.post(
        "/api/v2/auth/2fa/disable", json={"password": DEFAULT_PASSWORD, "code": totp_now("nhan.vien")}, headers=headers
    )
    assert r.status_code == 204
    user = _user("nhan.vien")
    assert user.is_2fa_enabled is False and user.totp_secret is None
    assert "access_token" in _password_login(client, "nhan.vien")
    actions = [a for a in _actions() if a.startswith("user.2fa")]
    assert actions == ["user.2fa_enable", "user.2fa_disable"]


def _actions() -> list[str]:
    with SessionLocal() as db:
        return list(db.scalars(select(SysAuditLog.action).order_by(SysAuditLog.id)))


# --- Customer: bắt buộc 2FA ------------------------------------------------------------------------------------------


@pytest.fixture()
def customer(client, make_party, make_user):
    make_user("kh.lan", roles=["customer"], party_id=make_party().id)
    return "kh.lan"


def test_customer_must_set_up_2fa_at_first_login(client, customer):
    challenge = _password_login(client, customer)
    assert challenge["mfa_setup_required"] is True and challenge["mfa_required"] is False
    assert "access_token" not in challenge
    _error(
        client.post("/api/v2/auth/login/2fa-enable", json={"mfa_token": challenge["mfa_token"], "code": "123456"}),
        409,
        "INVALID_STATE_TRANSITION",
    )  # chưa setup
    setup = client.post("/api/v2/auth/login/2fa-setup", json={"mfa_token": challenge["mfa_token"]})
    assert setup.status_code == 200 and setup.headers["Cache-Control"] == "no-store"
    secret = setup.json()["secret"]
    _error(
        client.post("/api/v2/auth/login/2fa-enable", json={"mfa_token": challenge["mfa_token"], "code": "000000"}),
        401,
        "UNAUTHENTICATED",
    )  # sai mã: tính như đăng nhập sai
    assert _user(customer).failed_login_count == 1
    code = totp.code_at(secret, now_utc())
    r = client.post("/api/v2/auth/login/2fa-enable", json={"mfa_token": challenge["mfa_token"], "code": code})
    assert r.status_code == 200 and _amr(r.json()["access_token"]) == ["pwd", "otp"]
    assert _user(customer).is_2fa_enabled is True and _user(customer).failed_login_count == 0
    assert _password_login(client, customer)["mfa_required"] is True  # lần sau: nhập mã


def test_customer_password_only_tokens_rejected(client, customer):
    user = _user(customer)
    token, _ = create_access_token(
        user.id, ["customer"], extra_claims={"pwd_at": service.password_stamp(user), "amr": ["pwd"]}
    )
    _error(client.get(ME, headers=_bearer(token)), 401, "UNAUTHENTICATED")
    legacy, _ = create_access_token(
        user.id, ["customer"], extra_claims={"pwd_at": service.password_stamp(user)}
    )  # trước 0.7
    _error(client.get(ME, headers=_bearer(legacy)), 401, "UNAUTHENTICATED")


def test_customer_old_refresh_token_rejected(client, customer):
    """Phiên cấp trước khi bắt buộc 2FA (mfa = false) không làm mới được."""
    with SessionLocal() as db:
        user = db.scalar(select(SysUser).where(SysUser.username == customer))
        tokens, _ = service._issue_tokens(db, user, service.ClientInfo(), service.get_settings(), mfa=False)
        db.commit()
    err = _error(
        client.post("/api/v2/auth/refresh", json={"refresh_token": tokens.refresh_token}), 401, "UNAUTHENTICATED"
    )
    assert err["message"] == "Cần đăng nhập lại với xác thực 2 lớp"
    with SessionLocal() as db:
        assert db.scalar(select(SysRefreshToken.revoked_reason)) == "mfa_required"


def test_customer_cannot_disable(client, customer, login):
    headers = _bearer(login(customer)["access_token"])
    _error(
        client.post(
            "/api/v2/auth/2fa/disable", json={"password": DEFAULT_PASSWORD, "code": totp_now(customer)}, headers=headers
        ),
        403,
        "FORBIDDEN",
    )


def test_required_roles_configurable(client, make_user, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("AUTH_2FA_REQUIRED_ROLES", "customer,manager,business_manager")
    get_settings.cache_clear()
    make_user("quan.ly", roles=["manager"])
    make_user("nhan.vien")
    assert _password_login(client, "quan.ly")["mfa_setup_required"] is True
    assert "access_token" in _password_login(client, "nhan.vien")


# --- Admin xóa 2FA (mất thiết bị) -----------------------------------------------------------------------------


def test_admin_reset_2fa(client, customer, make_user, login, auth_headers):
    tokens = login(customer)
    make_user("quan.tri", roles=["admin"])
    make_user("nhan.vien")
    uid = _user(customer).id
    _error(client.post(f"/api/v2/users/{uid}:reset-2fa", headers=auth_headers("nhan.vien")), 403, "FORBIDDEN")
    r = client.post(f"/api/v2/users/{uid}:reset-2fa", headers=auth_headers("quan.tri"))
    assert r.status_code == 200 and r.json()["is_2fa_enabled"] is False
    _error(client.post("/api/v2/auth/refresh", json={"refresh_token": tokens["refresh_token"]}), 401, "UNAUTHENTICATED")
    assert _password_login(client, customer)["mfa_setup_required"] is True  # thiết lập lại
    assert "user.2fa_reset" in _actions()


# --- Mã hóa totp_secret (Giai đoạn 6) ---------------------------------------------------------------------------


def test_totp_secret_stored_encrypted(client, make_user):
    from app.core import secretbox

    make_user("nhan.vien")
    result = _enable_for(client, "nhan.vien")
    stored = _user("nhan.vien").totp_secret
    secret = result["setup"]["secret"]
    assert stored.startswith("gAAAAA") and secret not in stored  # CSDL không có secret dạng rõ
    assert secretbox.decrypt(stored) == secret
    # Mã sinh từ secret trả cho ứng dụng xác thực vẫn đăng nhập được
    challenge = _password_login(client, "nhan.vien")
    with SessionLocal() as db:
        db.execute(update(SysUser).where(SysUser.username == "nhan.vien").values(totp_last_step=None))
        db.commit()
    code = totp.code_at(secret, now_utc())
    r = client.post("/api/v2/auth/login/totp", json={"mfa_token": challenge["mfa_token"], "code": code})
    assert r.status_code == 200, r.text


def test_database_rejects_plaintext_totp_secret(client, make_user):
    from sqlalchemy.exc import IntegrityError

    make_user("nhan.vien")
    with SessionLocal() as db, pytest.raises(IntegrityError, match="ck_sys_users_totp_secret_encrypted"):
        db.execute(update(SysUser).where(SysUser.username == "nhan.vien").values(totp_secret="JBSWY3DPEHPK3PXP"))
        db.commit()


def test_wrong_encryption_key_is_server_error_not_failed_login(client, make_user, monkeypatch, caplog):
    """Đổi TOTP_ENCRYPTION_KEY mà không giữ khóa cũ: 500 lỗi hệ thống, không tính là nhập sai mã (không khóa tạm)."""
    from app.core import secretbox
    from app.core.config import get_settings

    make_user("nhan.vien")
    _enable_for(client, "nhan.vien")
    challenge = _password_login(client, "nhan.vien")
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", secretbox.generate_key())
    get_settings.cache_clear()
    r = client.post("/api/v2/auth/login/totp", json={"mfa_token": challenge["mfa_token"], "code": "123456"})
    err = _error(r, 500, "INTERNAL_ERROR")
    assert err["message"] == "Không kiểm tra được mã xác thực; liên hệ quản trị hệ thống"
    assert _user("nhan.vien").failed_login_count == 0
    assert "kiểm tra TOTP_ENCRYPTION_KEY" in caplog.text
