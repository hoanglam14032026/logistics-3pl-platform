"""
Schema cho Hướng dẫn nghiệp vụ (Business Guide).
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BusinessGuideBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1)
    is_active: bool = True


class BusinessGuideCreate(BusinessGuideBase):
    pass


class BusinessGuideUpdate(BaseModel):
    """Cập nhật từng phần: chỉ các trường được gửi mới bị thay đổi."""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    category: Optional[str] = Field(None, min_length=1, max_length=100)
    content: Optional[str] = Field(None, min_length=1)
    is_active: Optional[bool] = None


class BusinessGuide(BusinessGuideBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
