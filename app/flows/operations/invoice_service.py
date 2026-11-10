"""
Hóa đơn thương mại `ops_commercial_invoices` (Giai đoạn 10) – gắn hợp đồng / lô hàng; tạo tay hoặc từ job trích xuất AI
đã duyệt (`?source_doc_job_id=` – app/shared/docai/linking.py, logic tạo không đổi).

- Có lô: lô phải tồn tại, chưa hủy; `ops_staff` chỉ gắn hóa đơn vào lô **được phân công** (`manager` mọi lô); hợp đồng
  = hợp đồng của lô (khóa `FOR SHARE` lô tới khi commit).
- Chỉ hợp đồng: hợp đồng `production`, không còn nháp.
- Trùng (hợp đồng, người bán, số hóa đơn) → `409 DUPLICATE`. Nhật ký `invoice.create`.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.pagination import PageParams, order_by_clause, paginate
from app.core.rbac import Role, forbidden
from app.flows.operations.invoice_schemas import InvoiceCreate
from app.flows.operations.models import (
    ContractMode,
    ContractStatus,
    OpsCommercialInvoice,
    OpsCommercialInvoiceItem,
    OpsServiceContract,
    OpsShipment,
    ShipmentStatus,
)
from app.shared.audit import service as audit
from app.shared.identity.models import SysUser

ENTITY = "commercial_invoice"
AUDIT_FIELDS = (
    "contract_id",
    "shipment_id",
    "invoice_no",
    "invoice_date",
    "seller_name",
    "buyer_name",
    "incoterm",
    "currency",
    "total_amount",
    "source_doc_job_id",
)
_SORTABLE = {
    "created_at": OpsCommercialInvoice.created_at,
    "invoice_date": OpsCommercialInvoice.invoice_date,
    "total_amount": OpsCommercialInvoice.total_amount,
}


def _field_error(field: str, message: str, issue: str = "invalid") -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, message, [{"field": f"body.{field}", "issue": issue, "message": message}]
    )


def _not_found() -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy hóa đơn")


def _duplicate() -> AppError:
    message = "Hóa đơn (người bán, số hóa đơn) đã có trong hợp đồng này"
    return AppError(
        ErrorCode.DUPLICATE, 409, message, [{"field": "body.invoice_no", "issue": "duplicate", "message": message}]
    )


def _resolve_contract(db: Session, data: InvoiceCreate, actor: SysUser) -> tuple[int, int | None]:
    if data.shipment_id is not None:
        shipment = db.scalar(
            select(OpsShipment)
            .where(OpsShipment.id == data.shipment_id, OpsShipment.mode == ContractMode.PRODUCTION.value)
            .with_for_update(read=True)
        )
        if shipment is None:
            raise _field_error("shipment_id", "Không có lô hàng này", "not_found")
        if shipment.status == ShipmentStatus.CANCELLED.value:
            raise _field_error("shipment_id", "Lô hàng đã hủy – không gắn hóa đơn được")
        if Role.MANAGER.value not in actor.role_codes and shipment.assignee_id != actor.id:
            raise forbidden("Lô hàng không được phân công cho bạn")
        if data.contract_id is not None and data.contract_id != shipment.contract_id:
            raise _field_error("contract_id", "contract_id khác hợp đồng của lô hàng")
        return shipment.contract_id, shipment.id
    contract = db.scalar(
        select(OpsServiceContract)
        .where(OpsServiceContract.id == data.contract_id, OpsServiceContract.mode == ContractMode.PRODUCTION.value)
        .with_for_update(read=True)
    )
    if contract is None:
        raise _field_error("contract_id", "Không có hợp đồng này", "not_found")
    if contract.status == ContractStatus.DRAFT.value:
        raise _field_error("contract_id", "Hợp đồng còn nháp – kích hoạt trước khi gắn hóa đơn")
    return contract.id, None


def create_invoice(db: Session, data: InvoiceCreate, actor: SysUser) -> OpsCommercialInvoice:
    contract_id, shipment_id = _resolve_contract(db, data, actor)
    # Giai đoạn 20: kỳ đã có bảng kê đối soát confirmed / closed của khách hàng → khóa (trigger CSDL giữ thêm lớp nữa)
    from app.flows.operations.statement_service import locked_by

    locked = locked_by(db, contract_id, data.invoice_date)
    if locked:
        raise AppError(
            ErrorCode.INVALID_STATE_TRANSITION,
            409,
            f"Kỳ của ngày hóa đơn đã khóa bởi bảng kê đối soát {locked} (đã xác nhận)",
            [{"field": "body.invoice_date", "issue": "period_locked", "message": f"Đã khóa bởi {locked}"}],
        )
    invoice = OpsCommercialInvoice(
        contract_id=contract_id,
        shipment_id=shipment_id,
        **data.model_dump(exclude={"shipment_id", "contract_id", "items"}),
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(invoice)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if "uq_ops_commercial_invoices_contract_seller_no" in str(exc.orig):
            raise _duplicate() from exc
        raise
    for i, item in enumerate(data.items, 1):
        db.add(OpsCommercialInvoiceItem(invoice_id=invoice.id, line_no=i, **item.model_dump()))
    after = {k: str(v) if k == "total_amount" else v for k, v in audit.snapshot(invoice, AUDIT_FIELDS).items()}
    audit.record(db, "invoice.create", ENTITY, invoice.id, actor.id, after={**after, "item_count": len(data.items)})
    db.commit()
    return invoice


def get_invoice(db: Session, invoice_id: int) -> OpsCommercialInvoice:
    invoice = db.get(OpsCommercialInvoice, invoice_id)
    if invoice is None:
        raise _not_found()
    return invoice


def items_of(db: Session, invoice_id: int) -> list[OpsCommercialInvoiceItem]:
    stmt = select(OpsCommercialInvoiceItem).where(OpsCommercialInvoiceItem.invoice_id == invoice_id)
    return list(db.scalars(stmt.order_by(OpsCommercialInvoiceItem.line_no)))


def list_invoices(
    db: Session, params: PageParams, shipment_id: int | None, contract_id: int | None
) -> tuple[list[OpsCommercialInvoice], int]:
    stmt = select(OpsCommercialInvoice)
    if shipment_id is not None:
        stmt = stmt.where(OpsCommercialInvoice.shipment_id == shipment_id)
    if contract_id is not None:
        stmt = stmt.where(OpsCommercialInvoice.contract_id == contract_id)
    stmt = stmt.order_by(order_by_clause(params.sort, _SORTABLE), OpsCommercialInvoice.id)
    return paginate(db, stmt, params)
