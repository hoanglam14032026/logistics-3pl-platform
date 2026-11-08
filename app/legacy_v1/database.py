"""
Cấu hình cơ sở dữ liệu cho ứng dụng 3PL Logistics Business Guide.

Lưu ý: module này KHÔNG import app.legacy_v1.models để tránh import vòng tròn.
Các model được import tại app/main.py trước khi gọi create_all().
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.legacy_v1.core.config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency cấp phiên làm việc CSDL cho mỗi request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
