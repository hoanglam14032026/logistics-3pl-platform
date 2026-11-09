"""
Danh mục model AI local (Giai đoạn 9): thêm / sửa / xóa (mềm) / bật / tắt – `admin`; danh sách model đang bật cho người
thực hiện chọn ở bước nhập chứng từ. Mọi thao tác ghi nhật ký `ai_model.*`.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.netguard import ExternalAddressError
from app.core.pagination import PageParams, order_by_clause, paginate
from app.core.timeutil import now_utc
from app.shared.ai_models import ollama
from app.shared.ai_models.models import AiModel, ModelType
from app.shared.ai_models.schemas import AiModelCreate, AiModelUpdate
from app.shared.audit import service as audit

AUDIT_FIELDS = (
    "code",
    "display_name",
    "provider",
    "endpoint",
    "model_id",
    "model_type",
    "supports_pdf",
    "supports_image",
    "supports_docx",
    "is_active",
    "description",
    "deleted_at",
)
_SORTABLE = {"created_at": AiModel.created_at, "code": AiModel.code, "display_name": AiModel.display_name}


def _field_error(field: str, message: str, issue: str = "invalid") -> AppError:
    return AppError(ErrorCode.VALIDATION_ERROR, 422, message, [{"field": field, "issue": issue, "message": message}])


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _not_found() -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy model AI")


def _check_endpoint(endpoint: str | None) -> None:
    if endpoint is None:
        return
    try:
        ollama.check_endpoint(endpoint)
    except ExternalAddressError as exc:
        raise _field_error("body.endpoint", str(exc), "external_address") from exc


def _check_version(model: AiModel, expected: int | None) -> None:
    if expected is not None and expected != model.row_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, 409, "Bản ghi đã bị người khác thay đổi; tải lại rồi thử lại")


def _audit(db: Session, action: str, model: AiModel, actor_id: int, before=None, after=None) -> None:
    audit.record(db, action, "ai_model", model.id, actor_id, before, after)


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "code" in str(exc.orig):
            message = "Mã model đã có trong danh mục"
            raise AppError(
                ErrorCode.DUPLICATE, 409, message, [{"field": "body.code", "issue": "duplicate", "message": message}]
            ) from exc
        raise


def get_model(db: Session, model_id: int, for_update: bool = False) -> AiModel:
    stmt = select(AiModel).where(AiModel.id == model_id)
    model = db.scalar(stmt.with_for_update() if for_update else stmt)
    if model is None:
        raise _not_found()
    return model


def list_models(
    db: Session, params: PageParams, include_deleted: bool = False, is_active: bool | None = None
) -> tuple[list[AiModel], int]:
    stmt = select(AiModel)
    if not include_deleted:
        stmt = stmt.where(AiModel.deleted_at.is_(None))
    if is_active is not None:
        stmt = stmt.where(AiModel.is_active.is_(is_active))
    stmt = stmt.order_by(order_by_clause(params.sort, _SORTABLE, default="code"), AiModel.id)
    return paginate(db, stmt, params)


def active_models(db: Session) -> list[AiModel]:
    stmt = select(AiModel).where(AiModel.is_active.is_(True), AiModel.deleted_at.is_(None))
    return list(db.scalars(stmt.order_by(AiModel.display_name, AiModel.id)))


def suggested_code() -> str | None:
    return get_settings().ai_model_default


def create_model(db: Session, data: AiModelCreate, actor_id: int) -> AiModel:
    _check_endpoint(data.endpoint)
    model = AiModel(**data.model_dump(mode="json"), created_by=actor_id, updated_by=actor_id)
    db.add(model)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        message = "Mã model đã có trong danh mục"
        raise AppError(
            ErrorCode.DUPLICATE, 409, message, [{"field": "body.code", "issue": "duplicate", "message": message}]
        ) from exc
    _audit(db, "ai_model.create", model, actor_id, after=audit.snapshot(model, AUDIT_FIELDS))
    _commit(db)
    return model


def _editable(model: AiModel) -> None:
    if model.deleted_at is not None:
        raise _conflict("Model đã xóa khỏi danh mục – không sửa, bật được")


def update_model(db: Session, model_id: int, data: AiModelUpdate, actor_id: int, expected: int | None) -> AiModel:
    model = get_model(db, model_id, for_update=True)
    _check_version(model, expected)
    _editable(model)
    changes = data.model_dump(mode="json", exclude_unset=True)
    if "endpoint" in changes:
        _check_endpoint(changes["endpoint"])
    merged = {k: changes.get(k, getattr(model, k)) for k in ("supports_pdf", "supports_image", "supports_docx")}
    if not any(merged.values()):
        raise _field_error("body", "Model phải hỗ trợ ít nhất một loại đầu vào (pdf, ảnh, docx)")
    before = audit.snapshot(model, AUDIT_FIELDS)
    for key, value in changes.items():
        setattr(model, key, value)
    model.updated_by = actor_id
    db.flush()
    old, new = audit.changed(before, audit.snapshot(model, AUDIT_FIELDS))
    if new:
        _audit(db, "ai_model.update", model, actor_id, old, new)
    _commit(db)
    return model


def set_active(db: Session, model_id: int, active: bool, actor_id: int, expected: int | None) -> AiModel:
    model = get_model(db, model_id, for_update=True)
    _check_version(model, expected)
    _editable(model)
    if model.is_active == active:
        raise _conflict("Model đã đang bật" if active else "Model đã đang tắt")
    model.is_active = active
    model.updated_by = actor_id
    db.flush()
    action = "ai_model.activate" if active else "ai_model.deactivate"
    _audit(db, action, model, actor_id, {"is_active": not active}, {"is_active": active})
    _commit(db)
    return model


def delete_model(db: Session, model_id: int, actor_id: int, expected: int | None) -> AiModel:
    """Xóa khỏi danh mục (mềm): job cũ vẫn tham chiếu `ai_model_id`; không chọn được cho job mới."""
    model = get_model(db, model_id, for_update=True)
    _check_version(model, expected)
    if model.deleted_at is not None:
        raise _conflict("Model đã xóa khỏi danh mục")
    before = audit.snapshot(model, AUDIT_FIELDS)
    model.is_active = False
    model.deleted_at = now_utc()
    model.deleted_by = actor_id
    model.updated_by = actor_id
    db.flush()
    old, new = audit.changed(before, audit.snapshot(model, AUDIT_FIELDS))
    _audit(db, "ai_model.delete", model, actor_id, old, new)
    _commit(db)
    return model


def probe(model: AiModel) -> tuple[bool, bool, str]:
    """Thử kết nối máy chủ model, kiểm model có sẵn (đã `ollama pull`). Không gửi dữ liệu chứng từ."""
    try:
        names = ollama.list_models(model.endpoint)
    except ollama.OllamaError as exc:
        return False, False, str(exc)
    if model.model_id in names or f"{model.model_id}:latest" in names:
        return True, True, "Máy chủ model sẵn sàng"
    return True, False, f"Máy chủ chưa có model '{model.model_id}' – chạy: ollama pull {model.model_id}"


def usable_for_job(db: Session, model_id: int, field: str, allowed_types: set[ModelType]) -> AiModel:
    """Model người dùng chọn cho job mới: phải đang bật, chưa xóa, đúng loại. Khóa FOR SHARE tới khi tạo xong job."""
    model = db.scalar(select(AiModel).where(AiModel.id == model_id).with_for_update(read=True))
    if model is None or model.deleted_at is not None:
        raise _field_error(field, "Model không có trong danh mục", "not_found")
    if not model.is_active:
        raise _field_error(field, "Model đang tắt – chọn model khác trong danh sách đang bật", "inactive")
    if ModelType(model.model_type) not in allowed_types:
        allowed = ", ".join(sorted(t.value for t in allowed_types))
        raise _field_error(field, f"Model loại '{model.model_type}' không dùng được ở đây (cần: {allowed})", "type")
    return model
