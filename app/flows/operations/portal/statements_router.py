"""
Cổng khách hàng – bảng kê đối soát định kỳ (PRT-07, chỉ API – giao diện để giai đoạn sau; Giai đoạn 20).

- Mọi tài khoản `customer`. BR-111: chỉ bảng kê của **chính khách hàng mình**, đã gửi (không có bản nháp); khác → 404.
- Xác nhận / khiếu nại: `POST /api/v2/ops/periodic-statements/{id}:confirm` / `:dispute` (cùng endpoint với `manager`
  ghi nhận thay).
- Không có người lập / gửi nội bộ, `row_version`.
"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.flows.operations import statement_service as svc
from app.flows.operations.cashbook_schemas import PortalStatementOut
from app.flows.operations.models import StatementStatus
from app.flows.operations.statement_router import csv_response
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/portal/statements", tags=["Portal – Statements of account"])

_customer = require_roles(Role.CUSTOMER)


@router.get("", response_model=Page[PortalStatementOut], summary="Bảng kê đối soát của khách hàng mình")
def list_statements(
    params: PageParams = Depends(),  # noqa: B008
    status_: StatementStatus | None = Query(None, alias="status"),
    user: SysUser = Depends(_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    if user.party_id is None:
        return Page[PortalStatementOut](items=[], total=0, page=params.page, page_size=params.page_size)
    items, total = svc.list_statements(db, params, user.party_id, status_.value if status_ else None, portal=True)
    return Page[PortalStatementOut](
        items=[PortalStatementOut.model_validate(s) for s in items],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/{statement_id:int}", response_model=PortalStatementOut, summary="Chi tiết bảng kê")
def get_statement(statement_id: int, user: SysUser = Depends(_customer), db: Session = Depends(get_db)):  # noqa: B008
    return svc.get_for_customer(db, user.party_id, statement_id)


@router.get("/{statement_id:int}/export.csv", summary="Xuất CSV")
def export_csv(statement_id: int, user: SysUser = Depends(_customer), db: Session = Depends(get_db)):  # noqa: B008
    return csv_response(db, svc.get_for_customer(db, user.party_id, statement_id))


@router.get("/{statement_id:int}/print", summary="Trang in (HTML)", response_class=HTMLResponse)
def print_view(statement_id: int, user: SysUser = Depends(_customer), db: Session = Depends(get_db)):  # noqa: B008
    return HTMLResponse(svc.render_html(db, svc.get_for_customer(db, user.party_id, statement_id)))
