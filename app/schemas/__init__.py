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

class VideoResourceBase(BaseModel):
    title: str
    description: Optional[str] = None
    video_path: str
    thumbnail_path: Optional[str] = None
    duration: Optional[int] = None
    category: Optional[str] = None
    is_active: bool = True

class VideoResourceCreate(VideoResourceBase):
    pass

class VideoResourceUpdate(VideoResourceBase):
    id: int

class VideoResource(VideoResourceBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        orm_mode = True