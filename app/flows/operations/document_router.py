"""
API chứng từ lô + kiểm tra chứng từ BR-103 (Giai đoạn 16).

- `GET /api/v2/ops/shipments/{id}/documents` (`?all=true` gồm bản cũ) · `GET …/document-checks` – đọc như lô hàng.
- `POST /api/v2/ops/shipments/{id}/documents` (multipart `file`, `doc_type`, `note`) – `ops_staff` phụ trách → EV-121,
  tạo lần kiểm tra `pending` nếu chưa có. Tải file: `GET /api/v2/files/{file_id}`.
- `GET /api/v2/ops/document-checks/{id}` · `POST …/{id}:complete` (`ops_staff` phụ trách lô) – kết quả từng loại
  chứng từ `ok` / `missing` / `invalid` + lý do → EV-122.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import Role, require_roles
from app.core.timeutil import UtcDatetime
from app.flows.operations import document_service as service
from app.flows.operations import shipment_service
from app.flows.operations.models import DocCheckOutcome, DocType
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops", tags=["Ops – Documents & checks (BR-103)"])

_reader = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.INSTRUCTOR, Role.ADMIN)
_staff = require_roles(Role.OPS_STAFF)
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_id: int
    doc_type: DocType
    version_no: int
    file_id: int
    status: str
    note: str | None
    uploaded_by: int
    uploaded_at: UtcDatetime


class CheckItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: DocType
    outcome: DocCheckOutcome = Field(description="ok = đủ, đúng · missing = thiếu · invalid = sai")
    reason: Note | None = Field(None, description="Bắt buộc khi missing / invalid")

    @model_validator(mode="after")
    def _reason(self):
        if self.outcome != DocCheckOutcome.OK and not self.reason:
            raise ValueError("Thiếu / sai: bắt buộc ghi lý do")
        return self


class CheckCompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CheckItemIn] = Field(min_length=1, max_length=50)
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] | None = None

    @model_validator(mode="after")
    def _unique(self):
        types = [i.doc_type for i in self.items]
        if len(types) != len(set(types)):
            raise ValueError("Mỗi loại chứng từ chỉ một kết quả")
        return self


class CheckItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    doc_type: str
    document_id: int | None
    outcome: str
    reason: str | None


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_id: int
    run_no: int
    status: str
    result: str | None
    blocking_count: int
    document_versions: dict | None
    note: str | None
    triggered_by_document_id: int | None
    created_at: UtcDatetime
    checked_by: int | None
    checked_at: UtcDatetime | None
    items: list[CheckItemOut] = []


class UploadOut(BaseModel):
    document: DocumentOut
    check: CheckOut


def _check_out(db: Session, check) -> CheckOut:
    return CheckOut.model_validate(check).model_copy(
        update={"items": [CheckItemOut.model_validate(i) for i in service.items_of(db, check.id)]}
    )


@router.get("/shipments/{shipment_id:int}/documents", response_model=list[DocumentOut], summary="Chứng từ của lô")
def list_documents(
    shipment_id: int,
    all_versions: bool = Query(False, alias="all", description="true: gồm phiên bản cũ (superseded)"),
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    shipment_service.get_shipment(db, shipment_id)
    return service.list_documents(db, shipment_id, all_versions)


@router.post(
    "/shipments/{shipment_id:int}/documents",
    response_model=UploadOut,
    status_code=status.HTTP_201_CREATED,
    summary="Tải chứng từ lên (→ EV-121, tạo lần kiểm tra)",
)
def upload_document(
    shipment_id: int,
    file: Annotated[UploadFile, File(description="pdf, jpg, png, docx, xlsx ≤ 20 MB")],
    doc_type: Annotated[DocType, Form()],
    note: Annotated[str | None, Form(max_length=500)] = None,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    doc, check = service.upload(db, shipment_id, doc_type.value, file, (note or "").strip() or None, actor)
    return {"document": DocumentOut.model_validate(doc), "check": _check_out(db, check)}


@router.get(
    "/shipments/{shipment_id:int}/document-checks", response_model=list[CheckOut], summary="Các lần kiểm tra chứng từ"
)
def list_checks(
    shipment_id: int,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    shipment_service.get_shipment(db, shipment_id)
    return [_check_out(db, c) for c in service.list_checks(db, shipment_id)]


@router.get("/document-checks/{check_id:int}", response_model=CheckOut, summary="Chi tiết lần kiểm tra")
def get_check(
    check_id: int,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _check_out(db, service.get_check(db, check_id))


@router.post(
    "/document-checks/{check_id:int}:complete",
    response_model=CheckOut,
    summary="Ghi kết quả kiểm tra (đủ / thiếu / sai + lý do) → EV-122",
)
def complete_check(
    check_id: int,
    body: CheckCompleteIn,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items = [{"doc_type": i.doc_type.value, "outcome": i.outcome.value, "reason": i.reason} for i in body.items]
    return _check_out(db, service.complete_check(db, check_id, items, body.note, actor))
