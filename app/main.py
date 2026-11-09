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

from app import handlers as _event_handlers  # noqa: F401  (Giai đoạn 11: xử lý sự kiện của các luồng)
from app.core.config import Settings, get_settings
from app.core.db import SessionLocal, engine
from app.core.errors import UnhandledErrorMiddleware, register_exception_handlers
from app.core.middleware import REQUEST_ID_HEADER, RequestIDMiddleware
from app.core.migrations import upgrade_to_head
from app.flows.assessment.router import router as assessment_router
from app.flows.control.router import router as control_router
from app.flows.operations import document_service as ops_document_service
from app.flows.operations import service as ops_service
from app.flows.operations.checkpoint_router import router as ops_checkpoints_router
from app.flows.operations.document_router import router as ops_documents_router
from app.flows.operations.invoice_router import router as ops_invoices_router
from app.flows.operations.portal.router import router as portal_contracts_router
from app.flows.operations.portal.shipments_router import router as portal_shipments_router
from app.flows.operations.router import router as ops_contracts_router
from app.flows.operations.shipment_router import router as ops_shipments_router
from app.flows.operations.task_router import router as ops_tasks_router
from app.legacy_v1 import models as _legacy_models  # noqa: F401  (đăng ký model v1 vào Base.metadata)
from app.legacy_v1.api import business_guide
from app.legacy_v1.api.business_data import records as business_data_records
from app.legacy_v1.api.training import documents as training_documents
from app.legacy_v1.api.video import resources as video_resources
from app.shared.ai_models.router import admin_router as ai_models_admin_router
from app.shared.ai_models.router import router as ai_models_router
from app.shared.audit.router import router as audit_router
from app.shared.docai import registry as docai_registry
from app.shared.docai import service as docai_service
from app.shared.docai.router import router as doc_jobs_router
from app.shared.docai.router import types_router as doc_types_router
from app.shared.events import outbox  # Giai đoạn 10: đăng ký xếp gửi outbox sau mỗi commit
from app.shared.files.router import router as files_router
from app.shared.identity.portal_router import router as portal_users_router
from app.shared.identity.router import auth_router, users_router
from app.shared.identity.throttle import RateLimitMiddleware
from app.shared.masterdata.location_router import router as locations_router
from app.shared.masterdata.process_router import router as process_templates_router
from app.shared.masterdata.router import router as parties_router

APP_TITLE = "3PL Logistics Business Guide"
APP_VERSION = "1.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Kết nối CSDL và đưa schema lên head bằng Alembic khi khởi động (lỗi thì dừng ngay), đóng pool khi tắt."""
    upgrade_to_head(engine)
    with SessionLocal() as db:
        ops_service.expire_due_contracts(db)  # 02 §6.1: hợp đồng quá hạn → expired (tới khi có tác vụ nền)
    outbox.schedule_pending()  # sự kiện còn chờ (Redis lỗi lúc xếp, tiến trình dừng giữa chừng) – xếp gửi lại
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
    # Giai đoạn 9: schema loại chứng từ (app/shared/docai/schemas/*.json) sai → dừng ngay, không chạy với schema hỏng.
    # OLLAMA_URL ngoài localhost / mạng nội bộ đã bị chặn khi đọc Settings (app/core/netguard.py).
    docai_registry.all_schemas()

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
    app.include_router(process_templates_router)
    app.include_router(locations_router)  # Giai đoạn 16
    app.include_router(portal_users_router)
    app.include_router(audit_router)
    app.include_router(files_router)
    # AI local trích xuất chứng từ (Giai đoạn 9) – dùng chung mọi tiến trình
    app.include_router(ai_models_admin_router)
    app.include_router(ai_models_router)
    app.include_router(doc_types_router)
    app.include_router(doc_jobs_router)
    docai_service.register_file_access()
    ops_document_service.register_file_access()  # Giai đoạn 16: file chứng từ lô
    # Điều kiện của Luồng 1 với danh mục khách hàng, tài khoản cổng: luôn đăng ký (kể cả khi tắt Luồng 1 – khi đó
    # không có hợp đồng nào nên manager không tạo được tài khoản customer; dữ liệu vẫn nhất quán).
    ops_service.register_guards()
    if "ops" in settings.enabled_flows:
        app.include_router(ops_contracts_router)
        app.include_router(ops_shipments_router)
        app.include_router(ops_tasks_router)  # Giai đoạn 13
        app.include_router(ops_documents_router)  # Giai đoạn 16: chứng từ + BR-103
        app.include_router(ops_checkpoints_router)  # Giai đoạn 16: điểm kiểm soát
        app.include_router(ops_invoices_router)  # Giai đoạn 10
        app.include_router(portal_contracts_router)
        app.include_router(portal_shipments_router)
    if "control" in settings.enabled_flows:  # Luồng 2 (Giai đoạn 11)
        app.include_router(control_router)
    if "assessment" in settings.enabled_flows:  # Luồng 4 – KPI (Giai đoạn 14)
        app.include_router(assessment_router)
    _include_legacy_v1_routers(app)
    return app
