"""
Lưu và cấp file (06 §2.6).

- `store(...)`: ghi theo luồng → nhận dạng MIME theo nội dung → kiểm loại/dung lượng theo **mục đích** → thêm
  `sys_files` (flush, chưa commit). Lỗi kiểm tra: xóa file vừa ghi; 422 (0 byte), 413 (quá dung lượng), 415 (sai loại).
- Nơi gọi commit cùng bản ghi nghiệp vụ trong `commit_or_discard(...)`: lỗi CSDL → xóa file vừa ghi (06 §2.6).
- Quyền tải: module sở hữu đăng ký hàm kiểm tra theo `owner_type` (`ACCESS_CHECKERS`) – `shared` không import `flows`.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.shared.files import sniff
from app.shared.files.models import SysFile
from app.shared.files.storage import StorageBackend, TooLarge, get_storage

MB = 1024 * 1024


@dataclass(frozen=True)
class Purpose:
    mime_types: frozenset[str]
    max_bytes: int


# Giá trị đề xuất 06 §2.6 (CẦN XÁC NHẬN); ghi đè bằng FILE_LIMITS_JSON:
#   {"contract_document": {"max_mb": 30, "ext": ["pdf"]}}
_DEFAULT_PURPOSES = {
    "contract_document": Purpose(frozenset({sniff.PDF, sniff.JPEG, sniff.PNG, sniff.DOCX, sniff.XLSX}), 20 * MB),
    # Chứng từ đưa vào AI local trích xuất (Giai đoạn 9). doc (Word 97–2003) chưa nhận – CẦN XÁC NHẬN.
    "doc_ai_source": Purpose(
        frozenset({sniff.JPEG, sniff.PNG, sniff.TIFF, sniff.WEBP, sniff.PDF, sniff.DOCX, sniff.XLSX, sniff.TXT}),
        20 * MB,
    ),
}
_EXT_TO_MIME = {ext: mime for mime, ext in sniff.EXTENSIONS.items()}


def purpose(name: str) -> Purpose:
    base = _DEFAULT_PURPOSES[name]
    override = (get_settings().file_limits_json or {}).get(name)
    if not isinstance(override, dict):
        return base
    mimes = frozenset(_EXT_TO_MIME[e] for e in override.get("ext", []) if e in _EXT_TO_MIME) or base.mime_types
    max_bytes = int(override["max_mb"] * MB) if "max_mb" in override else base.max_bytes
    return Purpose(mimes, max_bytes)


# owner_type → hàm(db, người dùng, file) → được tải hay không
AccessChecker = Callable[[Session, object, SysFile], bool]
ACCESS_CHECKERS: dict[str, AccessChecker] = {}

_UNSAFE = re.compile(r"[\x00-\x1f\x7f]")


def safe_filename(name: str | None) -> str:
    """Tên gốc để hiển thị/tải về: bỏ phần thư mục, ký tự điều khiển; tối đa 255 ký tự."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE.sub("", base).strip().strip(".")
    return base[:255] or "file"


def _error(code: ErrorCode, status: int, message: str) -> AppError:
    return AppError(code, status, message, [{"field": "body.file", "issue": code.lower(), "message": message}])


def store(
    db: Session,
    upload: UploadFile,
    purpose_name: str,
    module: str,
    owner_type: str,
    owner_id: int,
    actor_id: int | None,
    storage: StorageBackend | None = None,
) -> SysFile:
    storage = storage or get_storage()
    rule = purpose(purpose_name)
    try:
        blob = storage.save(module, upload.file, rule.max_bytes)
    except TooLarge as exc:
        raise _error(ErrorCode.FILE_TOO_LARGE, 413, f"File vượt {rule.max_bytes // MB} MB") from exc
    if blob.size_bytes == 0:
        storage.delete(blob.storage_key)
        raise _error(ErrorCode.VALIDATION_ERROR, 422, "File rỗng (0 byte)")
    mime = sniff.sniff(blob.head, storage.path(blob.storage_key))
    if mime not in rule.mime_types:
        storage.delete(blob.storage_key)
        allowed = ", ".join(sorted(sniff.EXTENSIONS[m] for m in rule.mime_types))
        legacy = " – file Office 97–2003 (doc/xls): lưu lại thành docx/xlsx" if mime == sniff.OLE2 else ""
        raise _error(ErrorCode.UNSUPPORTED_FILE_TYPE, 415, f"Loại file không được phép (cho phép: {allowed}){legacy}")
    record = SysFile(
        storage_key=blob.storage_key,
        original_filename=safe_filename(upload.filename),
        mime_type=mime,
        size_bytes=blob.size_bytes,
        sha256=blob.sha256,
        purpose=purpose_name,
        owner_type=owner_type,
        owner_id=owner_id,
        created_by=actor_id,
    )
    db.add(record)
    db.flush()
    return record


def commit_or_discard(db: Session, files: list[SysFile], storage: StorageBackend | None = None) -> None:
    """Commit; lỗi → rollback và xóa các file vừa ghi (không để file mồ côi)."""
    try:
        db.commit()
    except BaseException:
        db.rollback()
        storage = storage or get_storage()
        for f in files:
            storage.delete(f.storage_key)
        raise


def can_read(db: Session, user, file: SysFile) -> bool:
    checker = ACCESS_CHECKERS.get(file.owner_type)
    return bool(checker and checker(db, user, file))
