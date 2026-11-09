"""
API vòng đời hợp đồng – Giai đoạn 19 (`/api/v2/ops/service-contracts/...`).

- Phụ lục: `GET|POST /{id}/amendments` (lập: `ops_staff`, `manager`); `POST /amendments/{aid}:approve` (duyệt 2 người –
  `manager`, `business_manager`), `:reject` (lý do), `:withdraw` (người lập).
- Chấm dứt trước hạn: `GET|POST /{id}/terminations` (đề xuất: `manager`); `POST /terminations/{tid}:approve` /
  `:reject` (`business_manager`).
- Quyết toán BR-110: `GET|POST|PATCH /{id}/settlement` (lập, sửa: `manager`), `POST /{id}/settlement:submit`,
  `:approve` / `:reject` (`business_manager`).
Đọc: `ops_staff`, `manager`, `business_manager`, `instructor`, `admin`.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import Role, require_roles
from app.flows.operations import contract_lifecycle as lc
from app.flows.operations.schemas import (
    AmendmentCreate,
    AmendmentOut,
    DecisionIn,
    RejectIn,
    SettlementIn,
    SettlementOut,
    TerminationCreate,
    TerminationOut,
)
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops/service-contracts", tags=["Ops – Amendments, termination, settlement"])

_reader = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.INSTRUCTOR, Role.ADMIN)
_drafter = require_roles(Role.OPS_STAFF, Role.MANAGER)
_approver = require_roles(Role.MANAGER, Role.BUSINESS_MANAGER)
_manager = require_roles(Role.MANAGER)
_bm = require_roles(Role.BUSINESS_MANAGER)


# --- Phụ lục ----------------------------------------------------------------------------------------------------


@router.get("/{contract_id:int}/amendments", response_model=list[AmendmentOut], summary="Phụ lục của hợp đồng")
def list_amendments(contract_id: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    return lc.list_amendments(db, contract_id)


@router.post(
    "/{contract_id:int}/amendments",
    response_model=AmendmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Lập phụ lục (phạm vi / SLA / giá / thời hạn / điều khoản) – chờ duyệt 2 người",
)
def create_amendment(
    contract_id: int,
    body: AmendmentCreate,
    actor: SysUser = Depends(_drafter),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return lc.create_amendment(db, contract_id, body, actor)


@router.post("/amendments/{amendment_id:int}:approve", response_model=AmendmentOut, summary="Duyệt phụ lục (2 người)")
def approve_amendment(
    amendment_id: int,
    body: DecisionIn | None = None,
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return lc.approve_amendment(db, amendment_id, body.note if body else None, actor)


@router.post("/amendments/{amendment_id:int}:reject", response_model=AmendmentOut, summary="Từ chối phụ lục")
def reject_amendment(
    amendment_id: int,
    body: RejectIn,
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return lc.reject_amendment(db, amendment_id, body.note, actor)


@router.post("/amendments/{amendment_id:int}:withdraw", response_model=AmendmentOut, summary="Rút phụ lục (người lập)")
def withdraw_amendment(
    amendment_id: int,
    actor: SysUser = Depends(_drafter),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return lc.withdraw_amendment(db, amendment_id, actor)


# --- Chấm dứt trước hạn -----------------------------------------------------------------------------------------


@router.get("/{contract_id:int}/terminations", response_model=list[TerminationOut], summary="Đề xuất chấm dứt")
def list_terminations(contract_id: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    return lc.list_terminations(db, contract_id)


@router.post(
    "/{contract_id:int}/terminations",
    response_model=TerminationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Đề xuất chấm dứt trước hạn (manager)",
)
def request_termination(
    contract_id: int,
    body: TerminationCreate,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return lc.request_termination(db, contract_id, body.reason, body.effective_date, actor)


@router.post(
    "/terminations/{termination_id:int}:approve",
    response_model=TerminationOut,
    summary="Duyệt chấm dứt – hủy lô chưa bắt đầu, hợp đồng → thanh lý",
)
def approve_termination(
    termination_id: int,
    body: DecisionIn | None = None,
    actor: SysUser = Depends(_bm),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return lc.decide_termination(db, termination_id, True, body.note if body else None, actor)


@router.post("/terminations/{termination_id:int}:reject", response_model=TerminationOut, summary="Từ chối chấm dứt")
def reject_termination(
    termination_id: int,
    body: RejectIn,
    actor: SysUser = Depends(_bm),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return lc.decide_termination(db, termination_id, False, body.note, actor)


# --- Quyết toán -------------------------------------------------------------------------------------------------


@router.get("/{contract_id:int}/settlement", response_model=SettlementOut, summary="Quyết toán của hợp đồng")
def get_settlement(contract_id: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    return lc.get_settlement(db, contract_id)


@router.post(
    "/{contract_id:int}/settlement",
    response_model=SettlementOut,
    status_code=status.HTTP_201_CREATED,
    summary="Lập quyết toán (BR-110) – hóa đơn + KPI",
)
def create_settlement(
    contract_id: int,
    body: SettlementIn | None = None,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return lc.create_settlement(db, contract_id, body.model_dump(exclude_unset=True) if body else {}, actor)


@router.patch("/{contract_id:int}/settlement", response_model=SettlementOut, summary="Sửa quyết toán (tính lại)")
def update_settlement(
    contract_id: int,
    body: SettlementIn,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return lc.update_settlement(db, contract_id, body.model_dump(exclude_unset=True), actor)


@router.post("/{contract_id:int}/settlement:submit", response_model=SettlementOut, summary="Gửi duyệt quyết toán")
def submit_settlement(contract_id: int, actor: SysUser = Depends(_manager), db: Session = Depends(get_db)):  # noqa: B008
    return lc.submit_settlement(db, contract_id, actor)


@router.post("/{contract_id:int}/settlement:approve", response_model=SettlementOut, summary="Duyệt quyết toán")
def approve_settlement(
    contract_id: int,
    body: DecisionIn | None = None,
    actor: SysUser = Depends(_bm),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return lc.decide_settlement(db, contract_id, True, body.note if body else None, actor)


@router.post("/{contract_id:int}/settlement:reject", response_model=SettlementOut, summary="Trả lại quyết toán")
def reject_settlement(
    contract_id: int,
    body: RejectIn,
    actor: SysUser = Depends(_bm),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return lc.decide_settlement(db, contract_id, False, body.note, actor)
