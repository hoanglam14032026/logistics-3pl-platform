"""
API bảng kê đối soát định kỳ (SOA) `/api/v2/ops/periodic-statements` – Giai đoạn 20 (02 §3.9, §6.5).

- Lập, tính lại, phát hành lại bản bị khiếu nại: `ops_staff`, `manager`. Gửi, đóng: `manager`, `business_manager`.
- `:confirm` / `:dispute`: **cùng endpoint** cho `customer` (bảng kê của chính khách hàng mình – khác → 404) và
  `manager` (ghi nhận thay khi khách báo ngoài hệ thống – bắt buộc `note`).
- Xuất `…/export.csv`, trang in `…/print` (HTML). Đọc: `ops_staff`, `manager`, `business_manager`, `admin`.
"""

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.flows.operations import statement_service as svc
from app.flows.operations.cashbook_schemas import (
    PortalStatementOut,
    StatementCreate,
    StatementDetailOut,
    StatementDisputeIn,
    StatementOut,
    StatementResponseIn,
)
from app.flows.operations.models import StatementStatus
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops/periodic-statements", tags=["Ops – Statements of account"])

_reader = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.ADMIN)
_drafter = require_roles(Role.OPS_STAFF, Role.MANAGER)
_approver = require_roles(Role.MANAGER, Role.BUSINESS_MANAGER)
_responder = require_roles(Role.CUSTOMER, Role.MANAGER)


def _out(actor: SysUser, s):
    if Role.CUSTOMER.value in actor.role_codes:
        return PortalStatementOut.model_validate(s).model_dump(mode="json")
    return StatementDetailOut.model_validate(s).model_dump(mode="json")


@router.get("", response_model=Page[StatementOut], summary="Danh sách bảng kê đối soát")
def list_statements(
    params: PageParams = Depends(),  # noqa: B008
    customer_id: int | None = Query(None, gt=0),
    status_: StatementStatus | None = Query(None, alias="status"),
    month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = svc.list_statements(db, params, customer_id, status_.value if status_ else None, month)
    return Page[StatementOut](
        items=[StatementOut.model_validate(s) for s in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post(
    "", response_model=StatementDetailOut, status_code=status.HTTP_201_CREATED, summary="Lập bảng kê khách hàng + tháng"
)
def create(body: StatementCreate, actor: SysUser = Depends(_drafter), db: Session = Depends(get_db)):  # noqa: B008
    return svc.create(db, body, actor)


@router.get("/{statement_id:int}", response_model=StatementDetailOut, summary="Chi tiết bảng kê")
def get(statement_id: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    return svc.get(db, statement_id)


@router.post("/{statement_id:int}:recompute", response_model=StatementDetailOut, summary="Tính lại bản nháp")
def recompute(statement_id: int, actor: SysUser = Depends(_drafter), db: Session = Depends(get_db)):  # noqa: B008
    return svc.recompute(db, statement_id, actor)


@router.post("/{statement_id:int}:send", response_model=StatementDetailOut, summary="Gửi khách hàng (chốt số liệu)")
def send(statement_id: int, actor: SysUser = Depends(_approver), db: Session = Depends(get_db)):  # noqa: B008
    return svc.send(db, statement_id, actor)


@router.post(
    "/{statement_id:int}:confirm",
    summary="Xác nhận bảng kê – khách hàng, hoặc manager ghi nhận thay (bắt buộc note)",
)
def confirm(
    statement_id: int,
    body: StatementResponseIn | None = None,
    actor: SysUser = Depends(_responder),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _out(actor, svc.respond(db, statement_id, True, None, body.note if body else None, actor))


@router.post(
    "/{statement_id:int}:dispute",
    summary="Khiếu nại bảng kê (lý do) – khách hàng, hoặc manager ghi nhận thay (bắt buộc note)",
)
def dispute(
    statement_id: int,
    body: StatementDisputeIn,
    actor: SysUser = Depends(_responder),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _out(actor, svc.respond(db, statement_id, False, body.reason, body.note, actor))


@router.post("/{statement_id:int}:close", response_model=StatementDetailOut, summary="Đóng bảng kê đã xác nhận")
def close(statement_id: int, actor: SysUser = Depends(_approver), db: Session = Depends(get_db)):  # noqa: B008
    return svc.close(db, statement_id, actor)


@router.post(
    "/{statement_id:int}:reissue",
    response_model=StatementDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Phát hành lại bản bị khiếu nại – tạo bản mới, giữ bản cũ",
)
def reissue(statement_id: int, actor: SysUser = Depends(_drafter), db: Session = Depends(get_db)):  # noqa: B008
    return svc.reissue(db, statement_id, actor)


@router.get("/{statement_id:int}/export.csv", summary="Xuất CSV", response_class=Response)
def export_csv(statement_id: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    s = svc.get(db, statement_id)
    return csv_response(db, s)


@router.get("/{statement_id:int}/print", summary="Trang in (HTML)", response_class=HTMLResponse)
def print_view(statement_id: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    return HTMLResponse(svc.render_html(db, svc.get(db, statement_id)))


def csv_response(db: Session, s) -> Response:
    return Response(
        svc.export_csv(db, s),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{s.settlement_no}.csv"'},
    )
