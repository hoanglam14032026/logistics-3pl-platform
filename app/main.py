"""
Điểm vào ứng dụng FastAPI – Nền tảng vận hành và đào tạo nghiệp vụ Logistics 3PL (đặc tả 07 §1).

Dùng hàm tạo ứng dụng, không tạo `app` ở cấp module:
    uvicorn app.main:create_app --factory --reload

Giai đoạn chuyển tiếp: chỉ gắn API v1.1.0 (app/legacy_v1), đánh dấu deprecated trên OpenAPI (06 §1).
Router v2 gắn theo ENABLED_FLOWS khi các luồng được xây dựng.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.errors import UnhandledErrorMiddleware, register_exception_handlers
from app.core.middleware import REQUEST_ID_HEADER, RequestIDMiddleware
from app.legacy_v1 import models as _legacy_models  # noqa: F401  (đăng ký model v1 vào Base.metadata)
from app.legacy_v1.api import business_guide
from app.legacy_v1.api.business_data import records as business_data_records
from app.legacy_v1.api.training import documents as training_documents
from app.legacy_v1.api.video import resources as video_resources
from app.legacy_v1.database import Base, engine

APP_TITLE = "3PL Logistics Business Guide"
APP_VERSION = "1.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Mở kết nối CSDL khi khởi động (báo lỗi ngay nếu không kết nối được), đóng pool khi tắt."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    # Bảng v1 – tạm dùng create_all cho tới khi có Alembic (bước 0.3).
    Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        engine.dispose()


def _include_legacy_v1_routers(app: FastAPI) -> None:
    legacy = {"deprecated": True}
    app.include_router(business_guide.router, prefix="/api/v1/guide", tags=["Business Guide"], **legacy)
    app.include_router(
        training_documents.router, prefix="/api/v1/training/documents", tags=["Training Documents"], **legacy
    )
    app.include_router(
        business_data_records.router,
        prefix="/api/v1/business-data/records",
        tags=["Business Data Records"],
        **legacy,
    )
    app.include_router(video_resources.router, prefix="/api/v1/video/resources", tags=["Video Resources"], **legacy)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=APP_TITLE,
        description="Application for providing business guidelines and procedures for 3PL logistics employees",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    # add_middleware xếp chồng: thêm sau = bọc ngoài. Thứ tự từ ngoài vào: CORS → RequestID → lỗi 500 → app,
    # để mọi phản hồi lỗi (4xx và 500) đều có header CORS và X-Request-ID.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "If-Match", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "ETag", "Location"],
    )

    register_exception_handlers(app)

    @app.get("/", tags=["System"])
    def root():
        return {
            "message": "Welcome to 3PL Logistics Business Guide Application",
            "version": APP_VERSION,
            "documentation": "/docs",
        }

    @app.api_route("/health", methods=["GET", "HEAD"], tags=["System"])
    def health_check():
        # Giữ nguyên phản hồi của v1.1.0 (90-hien-trang-v1.1); không cần đăng nhập, không chạm CSDL.
        return {"status": "healthy"}

    _include_legacy_v1_routers(app)
    return app
