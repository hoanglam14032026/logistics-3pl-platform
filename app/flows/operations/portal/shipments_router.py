"""
Cổng khách hàng – theo dõi lô hàng (PRT-06; 02 §9.4 `/api/v2/portal/shipments`).

- Mọi tài khoản `customer`. BR-111: chỉ lô của hợp đồng `production`, `portal_enabled`, thuộc **chính khách hàng mình**;
  không có lô nháp. Lô khác → 404 như không tồn tại (06 §2.1).
- Không có thông tin nội bộ: người phụ trách, mức ưu tiên, lý do hủy, ghi chú, người thao tác, `row_version`.
- Mốc tiến độ: các trạng thái lô đã qua + thời điểm (tới khi có mốc theo bước quy trình `customer_visible_milestone`).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.flows.operations import shipment_service as service
from app.flows.operations.models import ShipmentStatus
from app.flows.operations.shipment_schemas import PortalShipmentDetailOut, PortalShipmentOut
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/portal/shipments", tags=["Portal – Shipments"])

_customer = require_roles(Role.CUSTOMER)


@router.get("", response_model=Page[PortalShipmentOut], summary="Lô hàng của khách hàng mình")
def list_shipments(
    params: PageParams = Depends(),  # noqa: B008
    status_: ShipmentStatus | None = Query(None, alias="status"),
    contract_id: int | None = Query(None, gt=0),
    user: SysUser = Depends(_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_portal_shipments(db, user.party_id, params, status_, contract_id)
    return Page[PortalShipmentOut](
        items=[PortalShipmentOut.model_validate(i) for i in items],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/{shipment_id:int}", response_model=PortalShipmentDetailOut, summary="Chi tiết + mốc tiến độ")
def get_shipment(
    shipment_id: int,
    user: SysUser = Depends(_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return service.get_portal_shipment(db, user.party_id, shipment_id)
