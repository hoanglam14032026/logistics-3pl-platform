"""
Ứng dụng FastAPI: 3PL Logistics Business Guide.
"""

from fastapi import FastAPI

import app.models  # noqa: F401  (đăng ký các model vào Base.metadata trước create_all)
from app.api.v1 import business_guide
from app.api.v1.business_data import records as business_data_records
from app.api.v1.training import documents as training_documents
from app.api.v1.video import resources as video_resources
from app.database import Base, engine

# Tạo bảng nếu chưa có (không có cơ chế migration)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="3PL Logistics Business Guide",
    description="Application for providing business guidelines and procedures for 3PL logistics employees",
    version="1.1.0",
)

app.include_router(business_guide.router, prefix="/api/v1/guide", tags=["Business Guide"])
app.include_router(training_documents.router, prefix="/api/v1/training/documents", tags=["Training Documents"])
app.include_router(business_data_records.router, prefix="/api/v1/business-data/records", tags=["Business Data Records"])
app.include_router(video_resources.router, prefix="/api/v1/video/resources", tags=["Video Resources"])


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
