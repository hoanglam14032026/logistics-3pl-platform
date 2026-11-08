"""
Model CSDL: Tài liệu đào tạo (bảng training_documents).
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from app.legacy_v1.database import Base
from datetime import datetime

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