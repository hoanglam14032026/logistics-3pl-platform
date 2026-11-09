"""
Quy tắc chứng từ bắt buộc `/api/v2/ops/document-requirements` (Giai đoạn 17) – kiểm tra tự động cơ bản: loại chứng từ
bắt buộc theo loại hình vận chuyển / hướng (NULL = mọi giá trị). Đọc: vai trò nội bộ; thêm, bật / tắt: `admin`,
`manager` (nhật ký `document_requirement.*`). Không xóa – tắt (`:deactivate`).
"""

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.core.rbac import INTERNAL_ROLES, Role, require_roles
from app.core.timeutil import UtcDatetime
from app.flows.operations.models import Direction, DocType, OpsDocumentRequirement, TransportMode
from app.shared.audit import service as audit
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops/document-requirements", tags=["Ops – Document requirements"])

_reader = require_roles(*INTERNAL_ROLES)
_writer = require_roles(Role.ADMIN, Role.MANAGER)
ENTITY = "document_requirement"


class RequirementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport_mode: TransportMode | None = Field(None, description="NULL = mọi loại hình")
    direction: Direction | None = Field(None, description="NULL = mọi hướng")
    doc_type: DocType
    note: str | None = Field(None, max_length=500)


class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    transport_mode: str | None
    direction: str | None
    doc_type: str
    is_active: bool
    note: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


def _snap(r: OpsDocumentRequirement) -> dict:
    return {k: getattr(r, k) for k in ("transport_mode", "direction", "doc_type", "is_active", "note")}


@router.get("", response_model=list[RequirementOut], summary="Quy tắc chứng từ bắt buộc")
def list_requirements(
    transport_mode: TransportMode | None = None,
    include_inactive: bool = Query(False),
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    r = OpsDocumentRequirement
    stmt = select(r).order_by(r.transport_mode.nulls_first(), r.direction.nulls_first(), r.doc_type, r.id)
    if transport_mode is not None:
        stmt = stmt.where((r.transport_mode == transport_mode.value) | r.transport_mode.is_(None))
    if not include_inactive:
        stmt = stmt.where(r.is_active.is_(True))
    return db.scalars(stmt).all()


@router.post("", response_model=RequirementOut, status_code=status.HTTP_201_CREATED, summary="Thêm quy tắc")
def create_requirement(
    body: RequirementIn,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    rule = OpsDocumentRequirement(**body.model_dump(mode="json"), created_by=actor.id, updated_by=actor.id)
    db.add(rule)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(ErrorCode.DUPLICATE, 409, "Quy tắc này đã có (bật lại nếu đang tắt)") from exc
    audit.record(db, "document_requirement.create", ENTITY, rule.id, actor.id, after=_snap(rule))
    db.commit()
    return rule


def _set_active(db: Session, rule_id: int, active: bool, actor: SysUser) -> OpsDocumentRequirement:
    rule = db.scalar(select(OpsDocumentRequirement).where(OpsDocumentRequirement.id == rule_id).with_for_update())
    if rule is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy quy tắc")
    if rule.is_active == active:
        raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, "Quy tắc đang bật" if active else "Quy tắc đang tắt")
    rule.is_active, rule.updated_by = active, actor.id
    action = "document_requirement.activate" if active else "document_requirement.deactivate"
    audit.record(db, action, ENTITY, rule.id, actor.id, {"is_active": not active}, {"is_active": active})
    db.commit()
    db.refresh(rule)
    return rule


@router.post("/{rule_id:int}:deactivate", response_model=RequirementOut, summary="Tắt quy tắc")
def deactivate(rule_id: int, actor: SysUser = Depends(_writer), db: Session = Depends(get_db)):  # noqa: B008
    return _set_active(db, rule_id, False, actor)


@router.post("/{rule_id:int}:activate", response_model=RequirementOut, summary="Bật lại quy tắc")
def activate(rule_id: int, actor: SysUser = Depends(_writer), db: Session = Depends(get_db)):  # noqa: B008
    return _set_active(db, rule_id, True, actor)
