"""
API đọc nhật ký thao tác `/api/v2/audit-logs` – chỉ `admin` (06 §4 – quản trị; 06 §5). Chỉ đọc: không có API sửa/xóa.

Lọc (06 §2.3): `action` (khớp đúng; kết thúc bằng `.` = theo tiền tố, vd. `party.`), `entity_type` + `entity_id`,
`actor_id`, `request_id`, khoảng thời gian `from`, `to` (ISO 8601). Mặc định mới nhất trước (`sort=-at`).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.core.pagination import Page, PageParams, like_pattern, order_by_clause, paginate
from app.core.rbac import Role, require_roles
from app.shared.audit.models import SysAuditLog
from app.shared.audit.schemas import AuditLogOut
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/audit-logs", tags=["Audit logs"])

_admin = require_roles(Role.ADMIN)
_SORTABLE = {"at": SysAuditLog.at, "id": SysAuditLog.id}
_CODE = r"^[a-z0-9_.]+$"


def _validation(field: str, message: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, message, [{"field": f"query.{field}", "issue": "invalid", "message": message}]
    )


@router.get("", response_model=Page[AuditLogOut], summary="Tra cứu nhật ký thao tác")
def list_audit_logs(
    params: PageParams = Depends(),  # noqa: B008
    action: str | None = Query(
        None, min_length=1, max_length=64, pattern=_CODE, description="vd. party.update, party."
    ),
    entity_type: str | None = Query(None, min_length=1, max_length=64, pattern=_CODE),
    entity_id: int | None = Query(None, gt=0),
    actor_id: int | None = Query(None, gt=0),
    request_id: str | None = Query(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"),
    from_: datetime | None = Query(None, alias="from", description="ISO 8601, có múi giờ (vd. 2026-11-08T00:00:00Z)"),
    to: datetime | None = Query(None, description="ISO 8601, có múi giờ; không gồm thời điểm này"),
    _: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    for name, value in (("from", from_), ("to", to)):
        if value is not None and value.tzinfo is None:
            raise _validation(name, "Thời gian phải có múi giờ (vd. hậu tố Z)")
    if from_ and to and from_ >= to:
        raise _validation("to", "to phải sau from")
    if entity_id is not None and entity_type is None:
        raise _validation("entity_id", "Lọc entity_id cần kèm entity_type")

    stmt = select(SysAuditLog)
    if action:
        if action.endswith("."):
            stmt = stmt.where(SysAuditLog.action.like(like_pattern(action)[1:], escape="\\"))  # bỏ % đầu: theo tiền tố
        else:
            stmt = stmt.where(SysAuditLog.action == action)
    if entity_type:
        stmt = stmt.where(SysAuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(SysAuditLog.entity_id == entity_id)
    if actor_id is not None:
        stmt = stmt.where(SysAuditLog.actor_id == actor_id)
    if request_id:
        stmt = stmt.where(SysAuditLog.request_id == request_id)
    if from_:
        stmt = stmt.where(SysAuditLog.at >= from_)
    if to:
        stmt = stmt.where(SysAuditLog.at < to)
    stmt = stmt.order_by(order_by_clause(params.sort, _SORTABLE, default="-at"), SysAuditLog.id.desc())
    items, total = paginate(db, stmt, params)
    return Page[AuditLogOut](
        items=[AuditLogOut.model_validate(i) for i in items], total=total, page=params.page, page_size=params.page_size
    )


@router.get("/{log_id:int}", response_model=AuditLogOut, summary="Chi tiết một dòng nhật ký")
def get_audit_log(
    log_id: int,
    _: SysUser = Depends(_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    entry = db.get(SysAuditLog, log_id)
    if entry is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy dòng nhật ký")
    return entry
