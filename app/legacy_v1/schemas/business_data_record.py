"""
Schema cho Bản ghi dữ liệu kinh doanh (Business Data Record).
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BusinessDataRecordBase(BaseModel):
    # Giá trị gợi ý: customer, partner, contract, form, business_result (không ràng buộc)
    record_type: str = Field(..., min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    data_content: Optional[str] = None  # JSON hoặc văn bản, lưu dạng chuỗi
    category: Optional[str] = Field(None, max_length=100)
    is_active: bool = True


class BusinessDataRecordCreate(BusinessDataRecordBase):
    pass


class BusinessDataRecordUpdate(BaseModel):
    """Cập nhật từng phần."""
    record_type: Optional[str] = Field(None, min_length=1, max_length=100)
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    data_content: Optional[str] = None
    category: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None


class BusinessDataRecord(BusinessDataRecordBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
