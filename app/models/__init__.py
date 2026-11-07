"""
Database models for 3PL Logistics Business Guide
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from app.database import Base
from datetime import datetime

class BusinessGuide(Base):
    __tablename__ = "business_guides"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    category = Column(String(100), nullable=False)
    content = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<BusinessGuide(id={self.id}, title='{self.title}', category='{self.category}')>"

class TrainingDocument(Base):
    __tablename__ = "training_documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    file_path = Column(String(500), nullable=False)
    file_type = Column(String(50))
    category = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

class BusinessDataRecord(Base):
    __tablename__ = "business_data_records"

    id = Column(Integer, primary_key=True, index=True)
    record_type = Column(String(100), nullable=False)  # customer, partner, contract, form, business_result
    title = Column(String(200), nullable=False)
    description = Column(Text)
    data_content = Column(Text)  # JSON hoặc nội dung dạng text
    category = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

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