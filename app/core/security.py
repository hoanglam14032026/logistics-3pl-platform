"""
Bảo mật (06 §3, bước 0.6): băm mật khẩu Argon2id (argon2-cffi), JWT truy cập HS256 (PyJWT), refresh token.

- Không dùng passlib / python-jose (99 §10.12).
- JWT truy cập sống `JWT_ACCESS_TTL_MIN` phút; không lưu phía máy chủ.
- Refresh token là chuỗi ngẫu nhiên (không phải JWT), chỉ lưu **SHA-256** trong CSDL; thu hồi được.
"""

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.timeutil import now_utc

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "logistics-3pl-platform"
ACCESS_TOKEN_TYPE = "access"

# argon2id, tham số mặc định của argon2-cffi (RFC 9106 – khuyến nghị thứ hai: m=64 MiB, t=3, p=4).
_hasher = PasswordHasher()
# Băm sẵn để so khi tài khoản không tồn tại – thời gian phản hồi như khi sai mật khẩu (chống dò tài khoản).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

_UNAUTHENTICATED_HEADERS = {"WWW-Authenticate": "Bearer"}


def unauthenticated(message: str | None = None) -> AppError:
    return AppError(ErrorCode.UNAUTHENTICATED, 401, message, headers=_UNAUTHENTICATED_HEADERS)


# --- Mật khẩu -------------------------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """So mật khẩu; `password_hash` = None (không có tài khoản) vẫn tốn thời gian như so thật."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True nếu hash dùng tham số cũ hơn hiện tại – băm lại sau khi đăng nhập đúng."""
    return _hasher.check_needs_rehash(password_hash)


# --- JWT truy cập -----------------------------------------------------------------------------------------


def create_access_token(
    user_id: int, roles: list[str], settings: Settings | None = None, extra_claims: dict[str, Any] | None = None
) -> tuple[str, int]:
    """Trả (token, số giây còn hiệu lực). `extra_claims` không được ghi đè claim chuẩn."""
    settings = settings or get_settings()
    issued_at = now_utc()
    ttl = timedelta(minutes=settings.jwt_access_ttl_min)
    claims = {
        "iss": JWT_ISSUER,
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "roles": sorted(roles),
        "iat": issued_at,
        "exp": issued_at + ttl,
        "jti": uuid.uuid4().hex,
    }
    for key, value in (extra_claims or {}).items():
        if key in claims:
            raise ValueError(f"Không được ghi đè claim chuẩn: {key}")
        claims[key] = value
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    """Giải mã và kiểm tra chữ ký, hạn, issuer, loại token; sai thì 401 (không nói rõ lý do cho client)."""
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[JWT_ALGORITHM],  # cố định thuật toán – chặn tấn công "alg": "none"/đổi thuật toán
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise unauthenticated("Phiên đăng nhập đã hết hạn") from exc
    except jwt.PyJWTError as exc:
        raise unauthenticated() from exc
    if claims.get("type") != ACCESS_TOKEN_TYPE or not str(claims["sub"]).isdigit():
        raise unauthenticated()
    return claims


# --- Refresh token ----------------------------------------------------------------------------------------


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 (token đủ ngẫu nhiên 384 bit – không cần băm chậm); CSDL lộ cũng không dùng lại được token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- Token bước 2 của đăng nhập (xác thực 2 lớp) ---------------------------------------------------------------

MFA_TOKEN_TYPE = "mfa"
MFA_VERIFY = "verify"  # đã bật 2FA: nhập mã TOTP
MFA_SETUP = "setup"  # bắt buộc 2FA nhưng chưa bật: thiết lập rồi nhập mã


def create_mfa_token(user_id: int, purpose: str, pwd_at: int, settings: Settings | None = None) -> tuple[str, int]:
    """Sau khi đúng mật khẩu, trước khi đủ 2 lớp. Không dùng được như access token (`type = mfa`)."""
    settings = settings or get_settings()
    issued_at = now_utc()
    ttl = timedelta(seconds=settings.auth_mfa_token_ttl_sec)
    claims = {
        "iss": JWT_ISSUER,
        "sub": str(user_id),
        "type": MFA_TOKEN_TYPE,
        "purpose": purpose,
        "pwd_at": pwd_at,
        "iat": issued_at,
        "exp": issued_at + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM), int(ttl.total_seconds())


def decode_mfa_token(token: str, purpose: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "type", "purpose", "pwd_at"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise unauthenticated("Hết thời gian nhập mã xác thực; đăng nhập lại") from exc
    except jwt.PyJWTError as exc:
        raise unauthenticated() from exc
    if claims["type"] != MFA_TOKEN_TYPE or claims["purpose"] != purpose or not str(claims["sub"]).isdigit():
        raise unauthenticated()
    return claims
