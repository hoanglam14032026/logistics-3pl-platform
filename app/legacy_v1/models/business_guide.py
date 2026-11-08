"""
Model CSDL: Hướng dẫn nghiệp vụ (bảng business_guides).
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from app.legacy_v1.database import Base
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