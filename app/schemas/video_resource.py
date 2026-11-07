"""
Video Resource Schemas for 3PL Logistics
"""

from pydantic import BaseModel
from datetime import datetime
from typing import Optional

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