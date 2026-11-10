"""
API tạm ứng – hoàn ứng nhân viên `/api/v2/ops/staff-advances` – Giai đoạn 20 (02 §3.10.3).

- Đề nghị, tải chứng từ, khai hoàn ứng: nhân viên (`ops_staff`, `manager`) cho chính mình.
- Duyệt / từ chối, chi tạm ứng, duyệt / trả lại hoàn ứng: `manager`, `business_manager` (khác nhân viên tạm ứng).
- Đọc: nhân viên thấy tạm ứng của mình; `manager`, `business_manager`, `admin` thấy tất cả (BC-TC-05: `?overdue=true`).
"""

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.flows.operations import advance_service as svc
from app.flows.operations.cashbook_schemas import (
    AdvanceCreate,
    AdvanceDetailOut,
    AdvanceOut,
    DisburseIn,
    FileOut,
    NoteIn,
    ReasonIn,
    SettlementApproveIn,
    SettlementSubmitIn,
)
from app.flows.operations.models import AdvanceStatus
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops/staff-advances", tags=["Ops – Staff advances"])

_reader = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.ADMIN)
_staff = require_roles(Role.OPS_STAFF, Role.MANAGER)
_approver = require_roles(Role.MANAGER, Role.BUSINESS_MANAGER)


def _detail(db: Session, a) -> AdvanceDetailOut:
    base = AdvanceOut.model_validate(a).model_dump()
    return AdvanceDetailOut.model_validate({**base, **svc.detail(db, a)}, from_attributes=True)


@router.get("", response_model=Page[AdvanceOut], summary="Danh sách tạm ứng (BC-TC-05)")
def list_advances(
    params: PageParams = Depends(),  # noqa: B008
    staff_id: int | None = Query(None, gt=0),
    status_: AdvanceStatus | None = Query(None, alias="status"),
    contract_id: int | None = Query(None, gt=0),
    overdue: bool | None = None,
    actor: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = svc.list_advances(
        db, actor, params, staff_id, status_.value if status_ else None, contract_id, overdue
    )
    return Page[AdvanceOut](
        items=[AdvanceOut.model_validate(a) for a in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post("", response_model=AdvanceOut, status_code=status.HTTP_201_CREATED, summary="Đề nghị tạm ứng (BR-115)")
def request_advance(body: AdvanceCreate, actor: SysUser = Depends(_staff), db: Session = Depends(get_db)):  # noqa: B008
    return svc.request(db, body, actor)


@router.get("/{advance_id:int}", response_model=AdvanceDetailOut, summary="Chi tiết + công nợ + phiếu liên quan")
def get_advance(advance_id: int, actor: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    return _detail(db, svc.get_advance(db, advance_id, actor))


@router.post(
    "/{advance_id:int}/files", response_model=FileOut, status_code=status.HTTP_201_CREATED, summary="Tải chứng từ"
)
def attach_file(
    advance_id: int,
    file: UploadFile = File(...),  # noqa: B008
    actor: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return svc.attach_file(db, advance_id, file, actor)


@router.post("/{advance_id:int}:approve", response_model=AdvanceDetailOut, summary="Duyệt tạm ứng")
def approve(
    advance_id: int,
    body: NoteIn | None = None,
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, svc.decide(db, advance_id, True, body.note if body else None, actor))


@router.post("/{advance_id:int}:reject", response_model=AdvanceDetailOut, summary="Từ chối tạm ứng (lý do)")
def reject(
    advance_id: int,
    body: ReasonIn,
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, svc.decide(db, advance_id, False, body.reason, actor))


@router.post("/{advance_id:int}:disburse", response_model=AdvanceDetailOut, summary="Chi tạm ứng – phiếu chi C08")
def disburse(
    advance_id: int,
    body: DisburseIn,
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, svc.disburse(db, advance_id, body, actor))


@router.post(
    "/{advance_id:int}:submit-settlement",
    response_model=AdvanceDetailOut,
    summary="Khai hoàn ứng: các khoản đã chi C01–C05 kèm chứng từ",
)
def submit_settlement(
    advance_id: int,
    body: SettlementSubmitIn,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, svc.submit_settlement(db, advance_id, body, actor))


@router.post("/{advance_id:int}:return-settlement", response_model=AdvanceDetailOut, summary="Trả lại hoàn ứng")
def return_settlement(
    advance_id: int,
    body: ReasonIn,
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, svc.return_settlement(db, advance_id, body.reason, actor))


@router.post(
    "/{advance_id:int}:approve-settlement",
    response_model=AdvanceDetailOut,
    summary="Duyệt hoàn ứng: ghi sổ, xử lý chênh lệch thừa (thu T05) / thiếu (chi C08)",
)
def approve_settlement(
    advance_id: int,
    body: SettlementApproveIn,
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, svc.approve_settlement(db, advance_id, body, actor))
