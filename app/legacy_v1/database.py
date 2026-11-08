"""
Cấu hình cơ sở dữ liệu cho ứng dụng 3PL Logistics Business Guide.

Lưu ý: module này KHÔNG import app.legacy_v1.models để tránh import vòng tròn.
Các model được import tại app/main.py và migrations/env.py; schema do Alembic quản lý (migrations/).
"""

from sqlalchemy.orm import declarative_base, sessionmaker

# Dùng chung engine của app/core/db.py (một pool kết nối cho cả v1 và v2); Base v1 giữ riêng.
from app.core.db import engine

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency cấp phiên làm việc CSDL cho mỗi request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
