"""
Mã hóa đối xứng giá trị bí mật lưu trong CSDL (Giai đoạn 6): hiện dùng cho `sys_users.totp_secret`.

Fernet (AES-128-CBC + HMAC-SHA256, thư viện `cryptography`) với khóa riêng `TOTP_ENCRYPTION_KEY` – **không** dùng lại
`JWT_SECRET`: lộ khóa ký JWT không làm lộ secret TOTP và ngược lại.

Xoay khóa: `TOTP_ENCRYPTION_KEY=<khóa mới>,<khóa cũ>` – mã hóa bằng khóa đầu, giải mã thử lần lượt mọi khóa
(`MultiFernet`); mã hóa lại dữ liệu bằng `rotate()` (hoặc để người dùng thiết lập lại 2FA) rồi bỏ khóa cũ.
Bản mã Fernet luôn bắt đầu bằng `gAAAAA` – CSDL có CHECK chặn ghi secret dạng rõ (migration 0008).
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings

CIPHERTEXT_PREFIX = "gAAAAA"


class SecretDecryptError(Exception):
    """Không giải mã được: sai khóa (đổi TOTP_ENCRYPTION_KEY mà không giữ khóa cũ) hoặc dữ liệu hỏng."""


@lru_cache(maxsize=4)
def _box(keys: tuple[str, ...]) -> MultiFernet:
    return MultiFernet([Fernet(k.encode("ascii")) for k in keys])


def _current() -> MultiFernet:
    return _box(get_settings().totp_encryption_keys)


def encrypt(plaintext: str) -> str:
    return _current().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    try:
        return _current().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as exc:
        raise SecretDecryptError("Không giải mã được secret (sai TOTP_ENCRYPTION_KEY?)") from exc


def rotate(ciphertext: str) -> str:
    """Mã hóa lại bằng khóa đầu tiên (khóa hiện hành)."""
    try:
        return _current().rotate(ciphertext.encode("ascii")).decode("ascii")
    except InvalidToken as exc:
        raise SecretDecryptError("Không giải mã được secret (sai TOTP_ENCRYPTION_KEY?)") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


if __name__ == "__main__":  # python -m app.core.secretbox → in một khóa mới cho TOTP_ENCRYPTION_KEY
    print(generate_key())
