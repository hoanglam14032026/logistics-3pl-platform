"""
Hàm dùng chung cho các router CRUD.
"""

from typing import Iterable

from fastapi import HTTPException, status


def apply_update(obj, data: dict, non_nullable: Iterable[str]) -> None:
    """Gán các trường cập nhật vào đối tượng ORM.

    Từ chối (422) nếu client gửi null cho trường bắt buộc trong CSDL.
    """
    bad = [k for k in non_nullable if k in data and data[k] is None]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Fields cannot be null: {', '.join(bad)}",
        )
    for key, value in data.items():
        setattr(obj, key, value)
