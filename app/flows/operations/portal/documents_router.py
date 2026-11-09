"""
Cổng khách hàng – gửi chứng từ cho lô (Giai đoạn 17; 02 §9.4).

- `POST /api/v2/portal/shipments/{id}/documents` (multipart `file`, `doc_type`, `note`) – `customer`: lô thấy được trên
  cổng (BR-111 – lô khách hàng khác, nháp, hợp đồng không bật cổng → 404), chưa đóng / hủy. File nhận dạng theo nội dung
  (magic bytes – như Giai đoạn 4), pdf, jpg, png, docx, xlsx ≤ 20 MB. Phát EV-121; lô đang `docs_verified` → quay về
  `docs_collecting`, nhân viên kiểm tra lại.
- `GET …/documents` – chứng từ hiện hành của lô (không có ghi chú nội bộ, người tải, file id).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import Role, require_roles
from app.core.timeutil import UtcDatetime
from app.flows.operations import document_service, shipment_service
from app.flows.operations.models import DocType
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/portal/shipments", tags=["Portal – Shipment documents"])

_customer = require_roles(Role.CUSTOMER)


class PortalDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doc_type: DocType
    version_no: int
    channel: str
    uploaded_at: UtcDatetime


@router.get("/{shipment_id:int}/documents", response_model=list[PortalDocumentOut], summary="Chứng từ của lô")
def list_documents(
    shipment_id: int,
    user: SysUser = Depends(_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    shipment_service.get_portal_shipment(db, user.party_id, shipment_id)
    return document_service.list_documents(db, shipment_id)


@router.post(
    "/{shipment_id:int}/documents",
    response_model=PortalDocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Gửi chứng từ cho lô (→ EV-121; nhân viên kiểm tra)",
)
def upload_document(
    shipment_id: int,
    file: Annotated[UploadFile, File(description="pdf, jpg, png, docx, xlsx ≤ 20 MB")],
    doc_type: Annotated[DocType, Form()],
    note: Annotated[str | None, Form(max_length=500)] = None,
    user: SysUser = Depends(_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    doc, _ = document_service.portal_upload(
        db, user.party_id, shipment_id, doc_type.value, file, (note or "").strip() or None, user
    )
    return doc
