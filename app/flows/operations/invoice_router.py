"""
API hóa đơn thương mại `/api/v2/ops/commercial-invoices` (Giai đoạn 10).

Quyền: đọc – `ops_staff`, `manager`, `business_manager`, `instructor`, `admin`; tạo – `ops_staff` (lô được phân công),
`manager`. Không sửa, không xóa. `?source_doc_job_id=`: tạo từ job trích xuất AI loại `commercial_invoice` **đã duyệt**
(giá trị lớn: đã đủ 2 người duyệt) – body = `form` của job + `shipment_id`/`contract_id`.
"""

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.core.versioning import etag
from app.flows.operations import invoice_service as service
from app.flows.operations.invoice_schemas import InvoiceCreate, InvoiceDetailOut, InvoiceItemOut, InvoiceOut
from app.flows.operations.models import OpsCommercialInvoice
from app.shared.docai import linking as docai_linking
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops/commercial-invoices", tags=["Ops – Commercial invoices"])

_reader = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.INSTRUCTOR, Role.ADMIN)
_writer = require_roles(Role.OPS_STAFF, Role.MANAGER)


def _detail(db: Session, response: Response, invoice: OpsCommercialInvoice) -> InvoiceDetailOut:
    response.headers["ETag"] = etag(invoice.row_version)
    items = [InvoiceItemOut.model_validate(i) for i in service.items_of(db, invoice.id)]
    return InvoiceDetailOut.model_validate(invoice).model_copy(update={"items": items})


@router.get("", response_model=Page[InvoiceOut], summary="Danh sách hóa đơn thương mại")
def list_invoices(
    params: PageParams = Depends(),  # noqa: B008
    shipment_id: int | None = Query(None, gt=0),
    contract_id: int | None = Query(None, gt=0),
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_invoices(db, params, shipment_id, contract_id)
    return Page[InvoiceOut](
        items=[InvoiceOut.model_validate(i) for i in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post("", response_model=InvoiceDetailOut, status_code=status.HTTP_201_CREATED, summary="Tạo hóa đơn")
def create_invoice(
    body: InvoiceCreate,
    response: Response,
    source_doc_job_id: int | None = Query(None, gt=0, description=docai_linking.QUERY_DESCRIPTION),
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    if source_doc_job_id is not None:
        docai_linking.attach(db, source_doc_job_id, "commercial_invoice", OpsCommercialInvoice, actor)
    invoice = service.create_invoice(db, body, actor)
    response.headers["Location"] = f"/api/v2/ops/commercial-invoices/{invoice.id}"
    return _detail(db, response, invoice)


@router.get("/{invoice_id:int}", response_model=InvoiceDetailOut, summary="Chi tiết hóa đơn + dòng hàng")
def get_invoice(
    invoice_id: int,
    response: Response,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, response, service.get_invoice(db, invoice_id))
