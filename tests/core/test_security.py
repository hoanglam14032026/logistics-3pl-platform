"""Test đơn vị app/core/security.py (06 §3): Argon2id, JWT HS256, refresh token – không cần CSDL."""

import base64
import json
from datetime import timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import (
    JWT_ALGORITHM,
    JWT_ISSUER,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.core.timeutil import now_utc

SETTINGS = Settings(_env_file=None, jwt_secret="k" * 40, jwt_access_ttl_min=15)
OTHER = Settings(_env_file=None, jwt_secret="z" * 40)


def _claims(**override) -> dict:
    now = now_utc()
    claims = {"iss": JWT_ISSUER, "sub": "7", "type": "access", "iat": now, "exp": now + timedelta(minutes=5)}
    claims.update(override)
    return {k: v for k, v in claims.items() if v is not None}


def _encode(claims: dict, secret: str = "k" * 40, alg: str = JWT_ALGORITHM) -> str:
    return jwt.encode(claims, secret, algorithm=alg)


def _assert_401(token: str, message: str | None = None):
    with pytest.raises(AppError) as exc:
        decode_access_token(token, SETTINGS)
    assert exc.value.status_code == 401
    assert exc.value.code == "UNAUTHENTICATED"
    assert exc.value.headers == {"WWW-Authenticate": "Bearer"}
    if message:
        assert exc.value.message == message


# --- Mật khẩu -------------------------------------------------------------------------------------------


def test_password_hash_is_argon2id_and_verifies():
    h = hash_password("MatKhau@2026")
    assert h.startswith("$argon2id$")
    assert "MatKhau@2026" not in h
    assert verify_password(h, "MatKhau@2026")
    assert not verify_password(h, "matkhau@2026")
    assert not password_needs_rehash(h)


def test_same_password_gives_different_hashes():
    assert hash_password("MatKhau@2026") != hash_password("MatKhau@2026")


@pytest.mark.parametrize("stored", [None, "", "khong-phai-hash", "$argon2id$hong"])
def test_verify_without_valid_hash_is_false(stored):
    assert verify_password(stored, "MatKhau@2026") is False


def test_old_parameters_need_rehash():
    from argon2 import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("MatKhau@2026")
    assert verify_password(weak, "MatKhau@2026")
    assert password_needs_rehash(weak)


# --- JWT -------------------------------------------------------------------------------------------------


def test_access_token_roundtrip():
    token, expires_in = create_access_token(42, ["manager", "admin"], SETTINGS, extra_claims={"pwd_at": 123})
    assert expires_in == 15 * 60
    claims = decode_access_token(token, SETTINGS)
    assert claims["sub"] == "42"
    assert claims["roles"] == ["admin", "manager"]
    assert claims["type"] == "access"
    assert claims["iss"] == JWT_ISSUER
    assert claims["pwd_at"] == 123
    assert claims["exp"] - claims["iat"] == 15 * 60
    header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    assert header["alg"] == "HS256"


def test_extra_claims_cannot_override_standard_claims():
    with pytest.raises(ValueError, match="sub"):
        create_access_token(1, [], SETTINGS, extra_claims={"sub": "999"})


def test_each_token_has_unique_jti():
    a, _ = create_access_token(1, [], SETTINGS)
    b, _ = create_access_token(1, [], SETTINGS)
    assert decode_access_token(a, SETTINGS)["jti"] != decode_access_token(b, SETTINGS)["jti"]


def test_expired_token_rejected():
    past = now_utc() - timedelta(minutes=30)
    _assert_401(_encode(_claims(iat=past, exp=past + timedelta(minutes=15))), "Phiên đăng nhập đã hết hạn")


def test_token_signed_with_other_secret_rejected():
    token, _ = create_access_token(1, [], OTHER)
    _assert_401(token)


def test_tampered_payload_rejected():
    token, _ = create_access_token(1, ["learner"], SETTINGS)
    header, payload, signature = token.split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=="))
    data["roles"] = ["admin"]
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    _assert_401(f"{header}.{forged}.{signature}")


def test_alg_none_rejected():
    _assert_401(jwt.encode(_claims(), None, algorithm="none"))


def test_other_hmac_algorithm_rejected():
    _assert_401(_encode(_claims(), alg="HS512"))


@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "iss", "type"])
def test_required_claims(missing):
    _assert_401(_encode(_claims(**{missing: None})))


@pytest.mark.parametrize(("field", "value"), [("type", "refresh"), ("iss", "he-thong-khac"), ("sub", "abc")])
def test_wrong_claim_values_rejected(field, value):
    _assert_401(_encode(_claims(**{field: value})))


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "x" * 2000])
def test_garbage_rejected(token):
    _assert_401(token)


# --- Refresh token ---------------------------------------------------------------------------------------


def test_refresh_token_random_and_hashed():
    a, b = generate_refresh_token(), generate_refresh_token()
    assert a != b and len(a) >= 64
    assert hash_refresh_token(a) == hash_refresh_token(a)
    assert len(hash_refresh_token(a)) == 64 and a not in hash_refresh_token(a)
