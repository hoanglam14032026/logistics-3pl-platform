"""
Kho lưu trữ file (06 §2.6): `StorageBackend`; mặc định thư mục cục bộ `UPLOAD_DIR` (mở rộng: S3/MinIO).

- Ghi theo luồng từng khúc 1 MiB (không đọc cả file vào RAM), đồng thời tính SHA-256, dung lượng và lấy phần đầu file
  để nhận dạng loại; vượt giới hạn thì dừng đọc ngay.
- Ghi vào file tạm cùng thư mục rồi đổi tên (nguyên tử): không có file dở dang dưới `storage_key`.
- `storage_key` do hệ thống sinh, đã kiểm tra – không cho thoát khỏi thư mục gốc (`..`, đường dẫn tuyệt đối).
"""

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import get_settings
from app.core.timeutil import now_utc

CHUNK = 1024 * 1024
HEAD_BYTES = 8192


class TooLarge(Exception):
    pass


@dataclass(frozen=True)
class StoredBlob:
    storage_key: str
    size_bytes: int
    sha256: str
    head: bytes


class StorageBackend(Protocol):
    def save(self, module: str, stream: BinaryIO, max_bytes: int) -> StoredBlob: ...
    def path(self, storage_key: str) -> Path: ...
    def delete(self, storage_key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def _resolve(self, storage_key: str) -> Path:
        path = (self.root / storage_key).resolve()
        if self.root not in path.parents:
            raise ValueError(f"storage_key không hợp lệ: {storage_key!r}")
        return path

    def new_key(self, module: str) -> str:
        now = now_utc()
        return f"{module}/{now:%Y}/{now:%m}/{uuid.uuid4()}"

    def save(self, module: str, stream: BinaryIO, max_bytes: int) -> StoredBlob:
        key = self.new_key(module)
        final = self._resolve(key)
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = final.with_name(f".{final.name}.part")
        digest, size, head = hashlib.sha256(), 0, b""
        try:
            with open(tmp, "wb") as out:
                while chunk := stream.read(CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise TooLarge
                    if len(head) < HEAD_BYTES:
                        head += chunk[: HEAD_BYTES - len(head)]
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, final)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return StoredBlob(key, size, digest.hexdigest(), head)

    def path(self, storage_key: str) -> Path:
        return self._resolve(storage_key)

    def delete(self, storage_key: str) -> None:
        self._resolve(storage_key).unlink(missing_ok=True)


def get_storage() -> StorageBackend:
    settings = get_settings()
    if settings.storage_backend != "local":
        raise NotImplementedError("STORAGE_BACKEND=s3 chưa hỗ trợ")
    return LocalStorage(settings.upload_dir)
