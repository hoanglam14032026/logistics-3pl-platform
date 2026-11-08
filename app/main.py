"""
Điểm vào ứng dụng FastAPI – Nền tảng vận hành và đào tạo nghiệp vụ Logistics 3PL.

Giai đoạn chuyển tiếp: hiện chỉ gắn API v1.1.0 (app/legacy_v1), đánh dấu deprecated
trên OpenAPI (đặc tả 06 §1). Bước 0.2 sẽ thay bằng create_app() theo 07 §1.
"""

from fastapi import FastAPI

import app.legacy_v1.models  # noqa: F401  (đăng ký các model vào Base.metadata trước create_all)
from app.legacy_v1.api import business_guide
from app.legacy_v1.api.business_data import records as business_data_records
from app.legacy_v1.api.training import documents as training_documents
from app.legacy_v1.api.video import resources as video_resources
from app.legacy_v1.database import Base, engine

# Tạo bảng nếu chưa có (chưa có migration – Alembic ở bước 0.3)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="3PL Logistics Business Guide",
    description="Application for providing business guidelines and procedures for 3PL logistics employees",
    version="1.1.0",
)

_LEGACY = {"deprecated": True}
app.include_router(business_guide.router, prefix="/api/v1/guide", tags=["Business Guide"], **_LEGACY)
app.include_router(
    training_documents.router, prefix="/api/v1/training/documents", tags=["Training Documents"], **_LEGACY
)
app.include_router(
    business_data_records.router,
    prefix="/api/v1/business-data/records",
    tags=["Business Data Records"],
    **_LEGACY,
)
app.include_router(video_resources.router, prefix="/api/v1/video/resources", tags=["Video Resources"], **_LEGACY)


@app.get("/", tags=["System"])
def root():
    return {
        "message": "Welcome to 3PL Logistics Business Guide Application",
        "version": app.version,
        "documentation": "/docs",
    }


@app.get("/health", tags=["System"])
def health_check():
    return {"status": "healthy"}
