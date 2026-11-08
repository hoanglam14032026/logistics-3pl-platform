"""
Cổng khách hàng – xem hợp đồng (PRT-01; 02 §9.4 `/api/v2/portal/contracts`).

- Mọi tài khoản `customer` (không cần `is_customer_admin`). BR-111: chỉ hợp đồng `production` của **chính khách hàng
  mình**, có `portal_enabled`; hợp đồng khác → 404 như không tồn tại (06 §2.1).
- Không có thông tin nội bộ: lý do tạm dừng, người lập/duyệt, `row_version`, file không đánh dấu cho khách hàng.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.flows.operations import service
from app.flows.operations.models import ContractStatus
from app.flows.operations.schemas import (
    PortalAttachmentOut,
    PortalContractDetailOut,
    PortalContractOut,
    PortalVersionOut,
)
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/portal/contracts", tags=["Portal – Contracts"])

_customer = require_roles(Role.CUSTOMER)


@router.get("", response_model=Page[PortalContractOut], summary="Hợp đồng của khách hàng mình")
def list_contracts(
    params: PageParams = Depends(),  # noqa: B008
    status_: ContractStatus | None = Query(None, alias="status"),
    user: SysUser = Depends(_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_portal_contracts(db, user.party_id, params, status_)
    return Page[PortalContractOut](
        items=[PortalContractOut.model_validate(c) for c in items],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/{contract_id:int}", response_model=PortalContractDetailOut, summary="Chi tiết hợp đồng")
def get_contract(
    contract_id: int,
    user: SysUser = Depends(_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    contract = service.get_portal_contract(db, user.party_id, contract_id)
    return PortalContractDetailOut.model_validate(contract).model_copy(update=service.children(db, contract))


@router.get("/{contract_id:int}/versions", response_model=list[PortalVersionOut], summary="Lịch sử phiên bản")
def list_versions(
    contract_id: int,
    user: SysUser = Depends(_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return service.list_versions(db, service.get_portal_contract(db, user.party_id, contract_id))


@router.get(
    "/{contract_id:int}/attachments",
    response_model=list[PortalAttachmentOut],
    summary="File của hợp đồng (bản đã ký…) – tải qua download_url",
)
def list_attachments(
    contract_id: int,
    user: SysUser = Depends(_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    contract = service.get_portal_contract(db, user.party_id, contract_id)
    return service.list_attachments(db, contract, customer_only=True)
