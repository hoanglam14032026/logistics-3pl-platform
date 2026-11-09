"""
Điểm vào ứng dụng FastAPI – Nền tảng vận hành và đào tạo nghiệp vụ Logistics 3PL (đặc tả 07 §1).

Dùng hàm tạo ứng dụng, không tạo `app` ở cấp module:
    uvicorn app.main:create_app --factory --reload

Router v2: xác thực `/api/v2/auth`, người dùng `/api/v2/users` (app/shared/identity, bước 0.6); router các luồng
gắn theo ENABLED_FLOWS khi được xây dựng. API v1.1.0 (app/legacy_v1) đánh dấu deprecated trên OpenAPI (06 §1).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings, get_settings
from app.core.db import SessionLocal, engine
from app.core.errors import UnhandledErrorMiddleware, register_exception_handlers
from app.core.middleware import REQUEST_ID_HEADER, RequestIDMiddleware
from app.core.migrations import upgrade_to_head
from app.flows.operations import service as ops_service
from app.flows.operations.portal.router import router as portal_contracts_router
from app.flows.operations.router import router as ops_contracts_router
from app.legacy_v1 import models as _legacy_models  # noqa: F401  (đăng ký model v1 vào Base.metadata)
from app.legacy_v1.api import business_guide
from app.legacy_v1.api.business_data import records as business_data_records
from app.legacy_v1.api.training import documents as training_documents
from app.legacy_v1.api.video import resources as video_resources
from app.shared.audit.router import router as audit_router
from app.shared.files.router import router as files_router
from app.shared.identity.portal_router import router as portal_users_router
from app.shared.identity.router import auth_router, users_router
from app.shared.identity.throttle import RateLimitMiddleware
from app.shared.masterdata.router import router as parties_router

APP_TITLE = "3PL Logistics Business Guide"
APP_VERSION = "1.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Kết nối CSDL và đưa schema lên head bằng Alembic khi khởi động (lỗi thì dừng ngay), đóng pool khi tắt."""
    upgrade_to_head(engine)
    with SessionLocal() as db:
        ops_service.expire_due_contracts(db)  # 02 §6.1: hợp đồng quá hạn → expired (tới khi có tác vụ nền)
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

    # add_middleware xếp chồng: thêm sau = bọc ngoài. Thứ tự từ ngoài vào: CORS → RequestID → lỗi 500 → giới hạn tần
    # suất → app, để mọi phản hồi lỗi (4xx, 429 và 500) đều có header CORS và X-Request-ID.
    app.add_middleware(RateLimitMiddleware)
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

    @app.get("/health", tags=["System"])
    def health_check():
        # Giữ nguyên phản hồi của v1.1.0 (90-hien-trang-v1.1); không cần đăng nhập, không chạm CSDL.
        return {"status": "healthy"}

    # HEAD cho công cụ healthcheck; route riêng, ẩn khỏi OpenAPI (một route GET+HEAD sinh trùng operation_id).
    app.add_api_route("/health", health_check, methods=["HEAD"], include_in_schema=False)

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(parties_router)
    app.include_router(portal_users_router)
    app.include_router(audit_router)
    app.include_router(files_router)
    # Điều kiện của Luồng 1 với danh mục khách hàng, tài khoản cổng: luôn đăng ký (kể cả khi tắt Luồng 1 – khi đó
    # không có hợp đồng nào nên manager không tạo được tài khoản customer; dữ liệu vẫn nhất quán).
    ops_service.register_guards()
    if "ops" in settings.enabled_flows:
        app.include_router(ops_contracts_router)
        app.include_router(portal_contracts_router)
    _include_legacy_v1_routers(app)
    return app
