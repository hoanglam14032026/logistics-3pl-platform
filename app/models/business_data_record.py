"""
Model CSDL: Dữ liệu kinh doanh (bảng business_data_records).
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from app.database import Base
from datetime import datetime

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