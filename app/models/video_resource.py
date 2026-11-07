"""
Model CSDL: Tài nguyên video (bảng video_resources).
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from app.database import Base
from datetime import datetime

class VideoResource(Base):
    __tablename__ = "video_resources"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    video_path = Column(String(500), nullable=False)
    thumbnail_path = Column(String(500))
    duration = Column(Integer)  # seconds
    category = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)