"""
API lô hàng `/api/v2/ops/shipments` (02 §8; 06 §4 – L1 Lô hàng, chứng từ, bước `production`).

Quyền: đọc – `ops_staff`, `manager`, `business_manager`, `instructor`, `admin`; tạo – `ops_staff`; sửa nháp, chuyển
trạng thái, hủy – `ops_staff` **được phân công** (khác → 403); phân công lại – `manager` (tạm thay EV-202 của Luồng 2).
`learner`, `content_editor`: không có quyền; `customer` theo dõi qua `/api/v2/portal/shipments`.
Thao tác ghi nhận `If-Match` (ETag từ GET) – lệch → 409 VERSION_CONFLICT.
"""

from datetime import date

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.core.versioning import etag, parse_if_match
from app.flows.operations import shipment_service as service
from app.flows.operations.models import OpsShipment, ShipmentStatus
from app.flows.operations.shipment_schemas import (
    AssignIn,
    CancelIn,
    DeliveryIn,
    NoteIn,
    ShipmentCreate,
    ShipmentDetailOut,
    ShipmentOut,
    ShipmentUpdate,
)
from app.shared.docai import linking as docai_linking
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops/shipments", tags=["Ops – Shipments"])

_reader = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.INSTRUCTOR, Role.ADMIN)
_staff = require_roles(Role.OPS_STAFF)
_manager = require_roles(Role.MANAGER)
_IF_MATCH = Header(None, description="ETag nhận từ GET; lệch → 409 VERSION_CONFLICT")


def _detail(db: Session, response: Response, shipment) -> ShipmentDetailOut:
    response.headers["ETag"] = etag(shipment.row_version)
    return ShipmentDetailOut.model_validate(shipment).model_copy(
        update={"history": service.history(db, shipment.id), "delivery": service.delivery(db, shipment.id)}
    )


@router.get("", response_model=Page[ShipmentOut], summary="Danh sách lô hàng")
def list_shipments(
    params: PageParams = Depends(),  # noqa: B008
    q: str | None = Query(None, min_length=1, max_length=100, description="Số lô, tên người gửi/nhận"),
    contract_id: int | None = Query(None, gt=0),
    status_: ShipmentStatus | None = Query(None, alias="status"),
    assignee_id: int | None = Query(None, gt=0),
    from_: date | None = Query(None, alias="from", description="Ngày tạo từ (giờ Việt Nam)"),
    to: date | None = Query(None, description="Ngày tạo đến (gồm cả ngày này)"),
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_shipments(db, params, q, contract_id, status_, assignee_id, from_, to)
    return Page[ShipmentOut](
        items=[ShipmentOut.model_validate(s) for s in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post("", response_model=ShipmentDetailOut, status_code=status.HTTP_201_CREATED, summary="Tạo lô hàng (nháp)")
def create_shipment(
    body: ShipmentCreate,
    response: Response,
    source_doc_job_id: int | None = Query(None, gt=0, description=docai_linking.QUERY_DESCRIPTION),
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    if source_doc_job_id is not None:  # Giai đoạn 9: nối job trích xuất đã duyệt – logic tạo không đổi
        docai_linking.attach(db, source_doc_job_id, "shipment", OpsShipment, actor)
    shipment = service.create_shipment(db, body, actor)
    response.headers["Location"] = f"/api/v2/ops/shipments/{shipment.id}"
    return _detail(db, response, shipment)


@router.get("/{shipment_id:int}", response_model=ShipmentDetailOut, summary="Chi tiết lô hàng + lịch sử trạng thái")
def get_shipment(
    shipment_id: int,
    response: Response,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, response, service.get_shipment(db, shipment_id))


@router.patch("/{shipment_id:int}", response_model=ShipmentDetailOut, summary="Sửa lô hàng nháp (JSON Merge Patch)")
def update_shipment(
    shipment_id: int,
    body: ShipmentUpdate,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, response, service.update_shipment(db, shipment_id, body, actor, parse_if_match(if_match)))


def _action(name: str, summary: str):
    fn = getattr(service, name)

    def endpoint(
        shipment_id: int,
        response: Response,
        body: NoteIn | None = None,
        if_match: str | None = _IF_MATCH,
        actor: SysUser = Depends(_staff),  # noqa: B008
        db: Session = Depends(get_db),  # noqa: B008
    ):
        shipment = fn(db, shipment_id, actor, parse_if_match(if_match), note=body.note if body else None)
        return _detail(db, response, shipment)

    endpoint.__name__ = name
    path = name.replace("_", "-")
    router.add_api_route(
        f"/{{shipment_id:int}}:{path}", endpoint, methods=["POST"], response_model=ShipmentDetailOut, summary=summary
    )


_action("submit", "Bắt đầu nhận chứng từ (draft → docs_collecting; hợp đồng phải active)")
_action("verify_documents", "Xác nhận chứng từ đạt (docs_collecting → docs_verified)")
_action("start", "Bắt đầu thực hiện (docs_verified → in_execution)")
_action("close", "Đóng lô (delivered → closed)")


@router.post(
    "/{shipment_id:int}:deliver",
    response_model=ShipmentDetailOut,
    summary="Ghi nhận giao hàng (in_execution → delivered) – phát EV-142",
)
def deliver(
    shipment_id: int,
    body: DeliveryIn,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, response, service.deliver(db, shipment_id, body, actor, parse_if_match(if_match)))


@router.post(
    "/{shipment_id:int}:cancel", response_model=ShipmentDetailOut, summary="Hủy lô (trước delivered, có lý do)"
)
def cancel(
    shipment_id: int,
    body: CancelIn,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, response, service.cancel(db, shipment_id, body.reason, actor, parse_if_match(if_match)))


@router.post("/{shipment_id:int}:assign", response_model=ShipmentDetailOut, summary="Phân công lại (manager)")
def assign(
    shipment_id: int,
    body: AssignIn,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    shipment = service.assign(db, shipment_id, body.assignee_id, actor, parse_if_match(if_match))
    return _detail(db, response, shipment)
