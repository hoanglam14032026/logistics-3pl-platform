"""
API hợp đồng dịch vụ `/api/v2/ops/service-contracts` (02 §8; 06 §4 – L1 Hợp đồng dịch vụ `production`).

Quyền: đọc – `ops_staff`, `manager`, `business_manager`, `instructor`, `admin`; tạo, sửa nháp – `ops_staff`, `manager`;
kích hoạt, tạm dừng, tiếp tục – `manager`. `learner`, `content_editor`: không có quyền; `customer` xem hợp đồng của mình
qua cổng (PRT-01 – chưa làm).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.core.versioning import etag, parse_if_match
from app.flows.operations import service
from app.flows.operations.models import AttachmentKind, ContractStatus, OpsContractVersion
from app.flows.operations.schemas import (
    AttachmentOut,
    ContractCreate,
    ContractDetailOut,
    ContractOut,
    ContractUpdate,
    LiquidationStart,
    StatusReason,
    VersionCreate,
    VersionOut,
)
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops/service-contracts", tags=["Ops – Service contracts"])

_reader = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.INSTRUCTOR, Role.ADMIN)
_editor = require_roles(Role.OPS_STAFF, Role.MANAGER)
_manager = require_roles(Role.MANAGER)
_IF_MATCH = Header(None, description="ETag nhận từ GET; lệch → 409 VERSION_CONFLICT")


def _out(response: Response, contract) -> ContractOut:
    response.headers["ETag"] = etag(contract.row_version)
    return contract


def _detail(db: Session, response: Response, contract) -> ContractDetailOut:
    """Chi tiết + phạm vi, SLA, biểu giá của phiên bản hiện tại."""
    response.headers["ETag"] = etag(contract.row_version)
    return ContractDetailOut.model_validate(contract).model_copy(update=service.children(db, contract))


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


@router.post("", response_model=ContractDetailOut, status_code=status.HTTP_201_CREATED, summary="Tạo hợp đồng (nháp)")
def create_contract(
    body: ContractCreate,
    response: Response,
    actor: SysUser = Depends(_editor),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    contract = service.create_contract(db, body, actor.id)
    response.headers["Location"] = f"/api/v2/ops/service-contracts/{contract.id}"
    return _detail(db, response, contract)


@router.get("/{contract_id:int}", response_model=ContractDetailOut, summary="Chi tiết hợp đồng (phiên bản hiện tại)")
def get_contract(
    contract_id: int,
    response: Response,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, response, service.get_contract(db, contract_id))


@router.patch(
    "/{contract_id:int}",
    response_model=ContractDetailOut,
    summary="Sửa hợp đồng nháp (JSON Merge Patch – mảng gửi lên thay thế toàn bộ)",
)
def update_contract(
    contract_id: int,
    body: ContractUpdate,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_editor),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    contract = service.update_contract(db, contract_id, body, actor.id, parse_if_match(if_match))
    return _detail(db, response, contract)


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


@router.post("/{contract_id:int}:start-liquidation", response_model=ContractOut, summary="Khởi động thanh lý")
def start_liquidation(
    contract_id: int,
    response: Response,
    body: LiquidationStart | None = None,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    reason = body.reason if body else None
    return _out(response, service.start_liquidation(db, contract_id, reason, actor.id, parse_if_match(if_match)))


@router.post("/{contract_id:int}:close", response_model=ContractOut, summary="Đóng hợp đồng (sau thanh lý)")
def close_contract(
    contract_id: int,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _out(response, service.close_contract(db, contract_id, actor.id, parse_if_match(if_match)))


# --- Phiên bản ------------------------------------------------------------------------------------------------


@router.get("/{contract_id:int}/versions", response_model=list[VersionOut], summary="Lịch sử phiên bản")
def list_versions(
    contract_id: int,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return service.list_versions(db, service.get_contract(db, contract_id))


@router.get("/{contract_id:int}/versions/{version:int}", response_model=VersionOut, summary="Một phiên bản")
def get_version(
    contract_id: int,
    version: int,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    contract = service.get_contract(db, contract_id)
    for v in service.list_versions(db, contract):
        if v.contract_version == version:
            return v
    raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy phiên bản")


@router.post(
    "/{contract_id:int}/versions",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Phiên bản mới từ phụ lục đã ký (manager)",
)
def create_version(
    contract_id: int,
    body: VersionCreate,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> OpsContractVersion:
    version = service.create_version(db, contract_id, body, actor.id, parse_if_match(if_match))
    response.headers["Location"] = f"/api/v2/ops/service-contracts/{contract_id}/versions/{version.contract_version}"
    return version


# --- File đính kèm ----------------------------------------------------------------------------------------------


@router.get("/{contract_id:int}/attachments", response_model=list[AttachmentOut], summary="File đính kèm")
def list_attachments(
    contract_id: int,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return service.list_attachments(db, service.get_contract(db, contract_id))


@router.post(
    "/{contract_id:int}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Tải file đính kèm (pdf, jpg, png, docx, xlsx ≤ 20 MB)",
)
def add_attachment(
    contract_id: int,
    file: Annotated[UploadFile, File(description="Nhận dạng loại theo nội dung file, không theo đuôi")],
    kind: Annotated[AttachmentKind, Form()],
    description: Annotated[str | None, Form(max_length=500)] = None,
    customer_visible: Annotated[bool | None, Form(description="Mặc định: true với bản đã ký, false với other")] = None,
    actor: SysUser = Depends(_editor),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    attachment = service.add_attachment(db, contract_id, file, kind, description, customer_visible, actor.id)
    return next(
        a for a in service.list_attachments(db, service.get_contract(db, contract_id)) if a["id"] == attachment.id
    )
