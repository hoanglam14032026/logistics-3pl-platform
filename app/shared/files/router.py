"""Tải file `GET /api/v2/files/{file_id}` (06 §1, §2.6): kiểm quyền theo thực thể sở hữu; hỗ trợ `Range`."""

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.shared.files import service
from app.shared.files.models import SysFile
from app.shared.files.storage import get_storage
from app.shared.identity.dependencies import get_current_user
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/files", tags=["Files"])


@router.get(
    "/{file_id:int}",
    response_class=FileResponse,
    summary="Tải file",
    description="Không có quyền với thực thể sở hữu file → 404 như không tồn tại (06 §2.1).",
)
def download(file_id: int, user: SysUser = Depends(get_current_user), db: Session = Depends(get_db)):  # noqa: B008
    record = db.get(SysFile, file_id)
    if record is None or not service.can_read(db, user, record):
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy file")
    path = get_storage().path(record.storage_key)
    if not path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy file")
    return FileResponse(
        path,
        media_type=record.mime_type,
        filename=record.original_filename,  # Content-Disposition: attachment; filename*=UTF-8''…
        headers={
            "X-Content-Type-Options": "nosniff",  # trình duyệt không đoán lại loại nội dung
            "Content-Security-Policy": "sandbox",  # mở trực tiếp cũng không chạy script trong file
            "Cache-Control": "private, no-store",
        },
    )
