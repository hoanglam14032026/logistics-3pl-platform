"""
TOTP – mật khẩu một lần theo thời gian (RFC 6238, dựa trên HOTP RFC 4226), viết bằng thư viện chuẩn.

Tham số tương thích Google Authenticator, Microsoft Authenticator…: HMAC-SHA1, 6 chữ số, chu kỳ 30 giây; secret 160 bit
mã hóa Base32. Kiểm tra cho lệch ±1 chu kỳ (đồng hồ điện thoại sai vài giây) và **chặn dùng lại**: chu kỳ đã dùng
(`last_step`) không được chấp nhận lần nữa.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime
from urllib.parse import quote, urlencode

PERIOD = 30
DIGITS = 6


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded, casefold=True)


def hotp(key: bytes, counter: int, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def step_at(at: datetime) -> int:
    return int(at.timestamp()) // PERIOD


def code_at(secret: str, at: datetime) -> str:
    return hotp(_key(secret), step_at(at))


def verify(secret: str, code: str, at: datetime, last_step: int | None = None, window: int = 1) -> int | None:
    """Trả chu kỳ khớp (để lưu làm `last_step`), hoặc None nếu sai / đã dùng."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    key, now_step = _key(secret), step_at(at)
    for step in range(now_step - window, now_step + window + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(hotp(key, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """`otpauth://` cho mã QR (Key Uri Format của Google Authenticator)."""
    label = quote(f"{issuer}:{account}", safe="")
    params = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": PERIOD})
    return f"otpauth://totp/{label}?{params}"
