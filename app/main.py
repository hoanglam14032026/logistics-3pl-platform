"""
FastAPI application for 3PL Logistics Business Guide
"""

from fastapi import FastAPI
from app.api.v1 import business_guide
from app.api.v1.training import documents as training_documents
from app.api.v1.business_data import records as business_data_records
from app.api.v1.video import resources as video_resources
from app.database import Base, engine
from app.models.business_guide import BusinessGuide
from app.models.training_document import TrainingDocument
from app.models.business_data_record import BusinessDataRecord
from app.models.video_resource import VideoResource

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="3PL Logistics Business Guide",
    description="Application for providing business guidelines and procedures for 3PL logistics employees",
    version="1.0.0"
)

# Include business guide API routes
app.include_router(
    business_guide.router,
    prefix="/api/v1/guide",
    tags=["Business Guide"]
)

# Include training document API routes
app.include_router(
    training_documents.router,
    prefix="/api/v1/training",
    tags=["Training Documents"]
)

# Include business data records API routes
app.include_router(
    business_data_records.router,
    prefix="/api/v1/business-data",
    tags=["Business Data Records"]
)

# Include video resources API routes
app.include_router(
    video_resources.router,
    prefix="/api/v1/video",
    tags=["Video Resources"]
)

@app.get("/")
async def root():
    return {
        "message": "Welcome to 3PL Logistics Business Guide Application",
        "version": "1.0.0",
        "documentation": "/docs"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}