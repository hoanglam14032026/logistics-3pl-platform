"""
Business Guide Schemas for 3PL Logistics
"""

from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class BusinessGuideBase(BaseModel):
    title: str
    category: str
    content: str
    is_active: bool = True

class BusinessGuideCreate(BusinessGuideBase):
    pass

class BusinessGuideUpdate(BusinessGuideBase):
    id: int

class BusinessGuide(BusinessGuideBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        orm_mode = True