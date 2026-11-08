"""TOTP (RFC 6238 / HOTP RFC 4226) – app/core/totp.py, test đơn vị."""

import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.core import totp

RFC_KEY = b"12345678901234567890"
RFC_SECRET = base64.b32encode(RFC_KEY).decode().rstrip("=")


def test_rfc4226_hotp_vectors():
    expected = ["755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489"]
    assert [totp.hotp(RFC_KEY, c) for c in range(10)] == expected


@pytest.mark.parametrize(
    ("unix", "code8"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc6238_sha1_vectors(unix, code8):
    assert totp.hotp(RFC_KEY, unix // 30, digits=8) == code8
    assert totp.code_at(RFC_SECRET, datetime.fromtimestamp(unix, UTC)) == code8[-6:]


def test_verify_window_and_replay():
    secret = totp.generate_secret()
    now = datetime(2026, 11, 8, 10, 0, 15, tzinfo=UTC)
    step = totp.step_at(now)
    for delta in (-30, 0, 30):  # lệch ±1 chu kỳ vẫn nhận
        assert totp.verify(secret, totp.code_at(secret, now + timedelta(seconds=delta)), now) == step + delta // 30
    for delta in (-60, 60):
        assert totp.verify(secret, totp.code_at(secret, now + timedelta(seconds=delta)), now) is None
    code = totp.code_at(secret, now)
    assert totp.verify(secret, code, now, last_step=step) is None  # đã dùng
    assert totp.verify(secret, code, now, last_step=step - 1) == step
    nxt = totp.code_at(secret, now + timedelta(seconds=30))
    assert totp.verify(secret, nxt, now, last_step=step) == step + 1  # mã chu kỳ kế tiếp vẫn dùng được


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12 34 5"])
def test_verify_rejects_malformed(code):
    assert totp.verify(totp.generate_secret(), code, datetime.now(UTC)) is None


def test_spaces_in_code_allowed():
    secret, now = totp.generate_secret(), datetime.now(UTC)
    code = totp.code_at(secret, now)
    assert totp.verify(secret, f" {code[:3]} {code[3:]} ", now) is not None


def test_secret_is_160_bit_base32():
    secrets_ = {totp.generate_secret() for _ in range(50)}
    assert len(secrets_) == 50
    for s in secrets_:
        assert len(base64.b32decode(s + "=" * (-len(s) % 8))) == 20


def test_provisioning_uri():
    uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", "minhan.lan", "Logistics 3PL")
    parsed = urlparse(uri)
    assert (parsed.scheme, parsed.netloc) == ("otpauth", "totp")
    assert parsed.path == "/Logistics%203PL%3Aminhan.lan"
    q = parse_qs(parsed.query)
    assert q == {
        "secret": ["JBSWY3DPEHPK3PXP"],
        "issuer": ["Logistics 3PL"],
        "algorithm": ["SHA1"],
        "digits": ["6"],
        "period": ["30"],
    }
