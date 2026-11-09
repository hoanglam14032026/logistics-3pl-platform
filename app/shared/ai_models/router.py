"""
API danh mục model AI local (Giai đoạn 9).

- `/api/v2/admin/ai-models` – **chỉ `admin`**: xem đầy đủ (gồm `endpoint`), thêm, sửa, xóa (mềm), bật/tắt, thử kết nối.
- `/api/v2/ai-models/active` – `ops_staff`, `manager`, `admin`: danh sách model **đang bật** để chọn ở bước nhập
  chứng từ; không có `endpoint`, provider, tên model trong máy chủ. `customer` và vai trò khác → 403.
"""

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.core.versioning import etag, parse_if_match
from app.shared.ai_models import ollama, service
from app.shared.ai_models.models import AiModel
from app.shared.ai_models.schemas import (
    AiModelAdminOut,
    AiModelCreate,
    AiModelOptionOut,
    AiModelProbeOut,
    AiModelUpdate,
)
from app.shared.identity.models import SysUser

admin_router = APIRouter(prefix="/api/v2/admin/ai-models", tags=["Admin – AI models"])
router = APIRouter(prefix="/api/v2/ai-models", tags=["AI models"])

_admin = require_roles(Role.ADMIN)
_chooser = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.ADMIN)
_IF_MATCH = Header(None, description="ETag nhận từ GET; lệch → 409 VERSION_CONFLICT")


def _validate_admin(model: AiModel) -> AiModelAdminOut:
    data = {c: getattr(model, c) for c in AiModelAdminOut.model_fields if c != "effective_endpoint"}
    return AiModelAdminOut(**data, effective_endpoint=ollama.base_url(model.endpoint))


@router.get(
    "/active",
    response_model=list[AiModelOptionOut],
    summary="Model AI đang bật – để người thực hiện chọn ở bước nhập chứng từ",
)
def list_active(
    _: SysUser = Depends(_chooser),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    suggested = service.suggested_code()
    return [
        AiModelOptionOut.model_validate(m).model_copy(update={"suggested": m.code == suggested})
        for m in service.active_models(db)
    ]


@admin_router.get("", response_model=Page[AiModelAdminOut], summary="Danh mục model AI (admin)")
def list_models(
    params: PageParams = Depends(),  # noqa: B008
    include_deleted: bool = Query(False, description="Gồm model đã xóa khỏi danh mục"),
    is_active: bool | None = None,
    _: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_models(db, params, include_deleted, is_active)
    return Page[AiModelAdminOut](
        items=[_validate_admin(m) for m in items], total=total, page=params.page, page_size=params.page_size
    )


@admin_router.post("", response_model=AiModelAdminOut, status_code=status.HTTP_201_CREATED, summary="Thêm model")
def create_model(
    body: AiModelCreate,
    response: Response,
    actor: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    model = service.create_model(db, body, actor.id)
    response.headers["Location"] = f"/api/v2/admin/ai-models/{model.id}"
    return _with_etag(response, model)


def _with_etag(response: Response, model: AiModel) -> AiModelAdminOut:
    response.headers["ETag"] = etag(model.row_version)
    return _validate_admin(model)


@admin_router.get("/{model_id:int}", response_model=AiModelAdminOut, summary="Chi tiết model")
def get_model(
    model_id: int,
    response: Response,
    _: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _with_etag(response, service.get_model(db, model_id))


@admin_router.patch("/{model_id:int}", response_model=AiModelAdminOut, summary="Sửa model (JSON Merge Patch)")
def update_model(
    model_id: int,
    body: AiModelUpdate,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _with_etag(response, service.update_model(db, model_id, body, actor.id, parse_if_match(if_match)))


@admin_router.delete(
    "/{model_id:int}",
    response_model=AiModelAdminOut,
    summary="Xóa khỏi danh mục (mềm – job cũ vẫn giữ model đã dùng)",
)
def delete_model(
    model_id: int,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _with_etag(response, service.delete_model(db, model_id, actor.id, parse_if_match(if_match)))


@admin_router.post("/{model_id:int}:activate", response_model=AiModelAdminOut, summary="Bật model")
def activate_model(
    model_id: int,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _with_etag(response, service.set_active(db, model_id, True, actor.id, parse_if_match(if_match)))


@admin_router.post("/{model_id:int}:deactivate", response_model=AiModelAdminOut, summary="Tắt model")
def deactivate_model(
    model_id: int,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _with_etag(response, service.set_active(db, model_id, False, actor.id, parse_if_match(if_match)))


@admin_router.post(
    "/{model_id:int}:probe",
    response_model=AiModelProbeOut,
    summary="Thử kết nối máy chủ model (localhost / mạng nội bộ) và kiểm model đã pull",
)
def probe_model(
    model_id: int,
    _: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    reachable, available, message = service.probe(service.get_model(db, model_id))
    return AiModelProbeOut(reachable=reachable, model_available=available, message=message)
