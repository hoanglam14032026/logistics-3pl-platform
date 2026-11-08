"""
Schema cho Tài nguyên video (Video Resource).

Tạo mới dùng multipart/form-data (xem router), nên không có schema Create.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class VideoResourceUpdate(BaseModel):
    """Cập nhật metadata từng phần. Đường dẫn file do server quản lý, không sửa qua API."""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    duration: Optional[int] = Field(None, ge=0)  # giây
    category: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None


class VideoResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str] = None
    video_path: str
    thumbnail_path: Optional[str] = None
    duration: Optional[int] = None
    category: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
