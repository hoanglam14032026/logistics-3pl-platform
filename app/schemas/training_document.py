"""
Training Document Schemas for 3PL Logistics
"""

from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class TrainingDocumentBase(BaseModel):
    title: str
    description: Optional[str] = None
    file_path: str
    file_type: str
    category: Optional[str] = None
    is_active: bool = True

class TrainingDocumentCreate(TrainingDocumentBase):
    pass

class TrainingDocumentUpdate(TrainingDocumentBase):
    id: int

class TrainingDocument(TrainingDocumentBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        orm_mode = True