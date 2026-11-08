"""
Lưu và xóa file upload trên đĩa.

Tên file lưu được sinh bằng UUID4 + phần mở rộng gốc, không dùng tiêu đề
người dùng nhập, nhằm tránh trùng tên và tấn công path traversal.
"""

import uuid
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from app.legacy_v1.core.config import UPLOAD_DIR


async def save_upload(file: UploadFile, subdir: str) -> str:
    """Lưu file upload vào UPLOAD_DIR/subdir, trả về đường dẫn đã lưu (chuỗi)."""
    target_dir = UPLOAD_DIR / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename or "").suffix.lower()  # đã gồm dấu chấm, ví dụ ".pdf"
    target = target_dir / f"{uuid.uuid4().hex}{suffix}"

    content = await file.read()
    with open(target, "wb") as buffer:
        buffer.write(content)
    return str(target)


def delete_file(path: Optional[str]) -> None:
    """Xóa file nếu tồn tại; bỏ qua nếu đường dẫn rỗng hoặc file không còn."""
    if not path:
        return
    p = Path(path)
    if p.is_file():
        p.unlink()
