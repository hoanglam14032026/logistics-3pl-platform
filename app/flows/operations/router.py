"""
API hợp đồng dịch vụ `/api/v2/ops/service-contracts` (02 §8; 06 §4 – L1 Hợp đồng dịch vụ `production`).

Quyền: đọc – `ops_staff`, `manager`, `business_manager`, `instructor`, `admin`; tạo, sửa nháp – `ops_staff`, `manager`;
kích hoạt, tạm dừng, tiếp tục – `manager`. `learner`, `content_editor`: không có quyền; `customer` xem hợp đồng của mình
qua cổng (PRT-01 – chưa làm).
"""

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.core.versioning import etag, parse_if_match
from app.flows.operations import service
from app.flows.operations.models import ContractStatus
from app.flows.operations.schemas import ContractCreate, ContractOut, ContractUpdate, StatusReason
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops/service-contracts", tags=["Ops – Service contracts"])

_reader = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.INSTRUCTOR, Role.ADMIN)
_editor = require_roles(Role.OPS_STAFF, Role.MANAGER)
_manager = require_roles(Role.MANAGER)
_IF_MATCH = Header(None, description="ETag nhận từ GET; lệch → 409 VERSION_CONFLICT")


def _out(response: Response, contract) -> ContractOut:
    response.headers["ETag"] = etag(contract.row_version)
    return contract


@router.get("", response_model=Page[ContractOut], summary="Danh sách hợp đồng")
def list_contracts(
    params: PageParams = Depends(),  # noqa: B008
    q: str | None = Query(None, min_length=1, max_length=100, description="Số hợp đồng, số tham chiếu, tên khách hàng"),
    customer_id: int | None = Query(None, gt=0),
    status_: ContractStatus | None = Query(None, alias="status"),
    portal_enabled: bool | None = None,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_contracts(db, params, q, customer_id, status_, portal_enabled)
    return Page[ContractOut](
        items=[ContractOut.model_validate(c) for c in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post("", response_model=ContractOut, status_code=status.HTTP_201_CREATED, summary="Tạo hợp đồng (nháp)")
def create_contract(
    body: ContractCreate,
    response: Response,
    actor: SysUser = Depends(_editor),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    contract = service.create_contract(db, body, actor.id)
    response.headers["Location"] = f"/api/v2/ops/service-contracts/{contract.id}"
    return _out(response, contract)


@router.get("/{contract_id:int}", response_model=ContractOut, summary="Chi tiết hợp đồng")
def get_contract(
    contract_id: int,
    response: Response,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _out(response, service.get_contract(db, contract_id))


@router.patch("/{contract_id:int}", response_model=ContractOut, summary="Sửa hợp đồng nháp (JSON Merge Patch)")
def update_contract(
    contract_id: int,
    body: ContractUpdate,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_editor),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _out(response, service.update_contract(db, contract_id, body, actor.id, parse_if_match(if_match)))


@router.post("/{contract_id:int}:activate", response_model=ContractOut, summary="Kích hoạt")
def activate_contract(
    contract_id: int,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _out(response, service.activate_contract(db, contract_id, actor.id, parse_if_match(if_match)))


@router.post("/{contract_id:int}:suspend", response_model=ContractOut, summary="Tạm dừng (có lý do)")
def suspend_contract(
    contract_id: int,
    body: StatusReason,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    contract = service.suspend_contract(db, contract_id, body.reason, actor.id, parse_if_match(if_match))
    return _out(response, contract)


@router.post("/{contract_id:int}:resume", response_model=ContractOut, summary="Tiếp tục sau tạm dừng")
def resume_contract(
    contract_id: int,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _out(response, service.resume_contract(db, contract_id, actor.id, parse_if_match(if_match)))
