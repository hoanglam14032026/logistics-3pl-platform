"""
Business Data Record Schemas for 3PL Logistics
"""

from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class BusinessDataRecordBase(BaseModel):
    record_type: str  # customer, partner, contract, form, business_result
    title: str
    description: Optional[str] = None
    data_content: str
    category: Optional[str] = None
    is_active: bool = True

class BusinessDataRecordCreate(BusinessDataRecordBase):
    pass

class BusinessDataRecordUpdate(BusinessDataRecordBase):
    id: int

class BusinessDataRecord(BusinessDataRecordBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        orm_mode = True