"""Test đơn vị app/core/secretbox.py – mã hóa totp_secret bằng Fernet (Giai đoạn 6). Không đụng CSDL."""

import pytest

from app.core import secretbox
from app.core.config import get_settings

KEY_A = secretbox.generate_key()
KEY_B = secretbox.generate_key()


@pytest.fixture()
def keys(monkeypatch):
    def _set(value: str) -> None:
        monkeypatch.setenv("TOTP_ENCRYPTION_KEY", value)
        get_settings.cache_clear()

    return _set


def test_round_trip_and_ciphertext_shape(keys):
    keys(KEY_A)
    secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    token = secretbox.encrypt(secret)
    assert token.startswith(secretbox.CIPHERTEXT_PREFIX) and secret not in token
    assert len(token) <= 255  # vừa cột sys_users.totp_secret
    assert secretbox.encrypt(secret) != token  # IV ngẫu nhiên
    assert secretbox.decrypt(token) == secret


def test_wrong_key_or_tampered_data_fails(keys):
    keys(KEY_A)
    token = secretbox.encrypt("ABC")
    with pytest.raises(secretbox.SecretDecryptError):
        secretbox.decrypt(token[:-4] + "AAAA")
    with pytest.raises(secretbox.SecretDecryptError):
        secretbox.decrypt("JBSWY3DPEHPK3PXP")  # dạng rõ
    keys(KEY_B)
    with pytest.raises(secretbox.SecretDecryptError):
        secretbox.decrypt(token)


def test_key_rotation(keys):
    keys(KEY_A)
    old = secretbox.encrypt("ABC")
    keys(f"{KEY_B},{KEY_A}")  # khóa mới đứng đầu, giữ khóa cũ để giải mã
    assert secretbox.decrypt(old) == "ABC"
    rotated = secretbox.rotate(old)
    keys(KEY_B)  # bỏ khóa cũ
    assert secretbox.decrypt(rotated) == "ABC"
    with pytest.raises(secretbox.SecretDecryptError):
        secretbox.decrypt(old)
