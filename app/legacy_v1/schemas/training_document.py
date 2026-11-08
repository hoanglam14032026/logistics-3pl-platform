"""
Schema cho Tài liệu đào tạo (Training Document).

Tạo mới dùng multipart/form-data (xem router), nên không có schema Create.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TrainingDocumentUpdate(BaseModel):
    """Cập nhật metadata từng phần. Đường dẫn file do server quản lý, không sửa qua API."""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    file_type: Optional[str] = Field(None, max_length=50)
    category: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None


class TrainingDocument(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str] = None
    file_path: str
    file_type: Optional[str] = None
    category: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
