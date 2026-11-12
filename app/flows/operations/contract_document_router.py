"""
API khai báo + nhập tay giấy tờ, chứng từ theo hợp đồng dịch vụ (Giai đoạn 23) – màn hình 2 phần (trái xem file,
phải nhập).

- `GET /api/v2/ops/document-catalog` (`?source=customer|partner&level=contract|shipment`) · `…/{code}` – danh mục
giấy tờ
  hệ thống lập sẵn + biểu mẫu trường nhập (nhân viên chọn loại từ đây).
- `GET|POST /api/v2/ops/service-contracts/{id}/documents` – danh sách / khai báo giấy tờ của hợp đồng (multipart,
file tùy
  chọn) → lưu ngay vào CSDL của hợp đồng.
- `GET /api/v2/ops/contract-documents/{id}` · `PATCH` (tự động lưu từng thay đổi, `If-Match`) · `PUT …/file` (đính
/ thay file)
  · `POST …:confirm` · `:reopen` · `:void`.
- `GET …/contract-documents/{id}/pages` · `…/pages/{n}/image` (PNG) · `…/pages/{n}/text` – xem file, lật trang.
Ghi: `ops_staff`, `manager` (hồ sơ cấp lô: `ops_staff` phụ trách lô). Đọc: như chứng từ lô. Khách hàng → 403.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.core.rbac import Role, require_roles
from app.core.timeutil import UtcDatetime
from app.core.versioning import etag, parse_if_match
from app.flows.operations import contract_document_service as service
from app.flows.operations import document_pages as pages
from app.flows.operations.document_router import _reader
from app.flows.operations.models import OpsContractDocument, OpsDocumentCatalog
from app.shared.docai.api_schemas import DocTypeOut
from app.shared.files.models import SysFile
from app.shared.files.storage import get_storage
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops", tags=["Ops – Giấy tờ theo hợp đồng dịch vụ (G23)"])
_writer = require_roles(Role.OPS_STAFF, Role.MANAGER)
_IF_MATCH = Header(None, description="ETag nhận từ GET; lệch → 409 VERSION_CONFLICT")
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class CatalogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    source: str = Field(description="customer – khách hàng cung cấp · partner – đối tác phát hành")
    partner_type: str | None = Field(None, description="Loại đối tác md_parties gợi ý")
    level: str = Field(description="contract – cả hợp đồng · shipment – gắn một lô")
    step_no: int
    step_label: str
    description: str | None
    is_active: bool


class CatalogDetailOut(CatalogOut):
    form: DocTypeOut


class ContractDocOut(BaseModel):
    id: int
    contract_id: int
    contract_version: int
    shipment_id: int | None
    catalog_code: str
    catalog_name: str
    step_no: int
    step_label: str
    source: str
    party_id: int | None
    party_name: str | None
    received_date: date | None
    note: str | None
    file_id: int | None
    status: str = Field(description="draft | confirmed | void")
    void_reason: str | None
    confirmed_at: UtcDatetime | None
    confirmed_by: int | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    row_version: int
    missing_required: list[str]
    editable: bool


class ContractDocDetailOut(ContractDocOut):
    schema_version: int
    values: dict[str, Any]
    form: DocTypeOut
    shipment_update: dict[str, Any] | None = Field(None, description="Chỉ khi xác nhận vận đơn gắn lô")
    shipment_document: dict[str, Any] | None = Field(
        None,
        description="Chỉ khi xác nhận giấy tờ cấp lô có loại trùng chứng từ lô: `registered` true → `document_id`, "
        "`version_no`, `check_id` (lần kiểm tra BR-103 đang chờ); false → `reason` no_file | file_type | unchanged",
    )


class DocPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict, description="Chỉ các trường thay đổi; rỗng / null = xóa")
    party_id: int | None = Field(None, gt=0)
    party_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    received_date: date | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class PageOut(BaseModel):
    number: int
    kind: str = Field(description="image → …/image · text → …/text")
    label: str | None = None


class PagesOut(BaseModel):
    document_id: int
    filename: str
    mime_type: str
    page_count: int
    pages: list[PageOut]


def _form(code: str) -> DocTypeOut:
    schema = service.get_schema(code)
    if schema is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Chưa có biểu mẫu nhập cho loại giấy tờ này")
    return DocTypeOut.model_validate(schema.model_dump())


def _row(db: Session, doc: OpsContractDocument, actor: SysUser, catalogs: dict[str, OpsDocumentCatalog]) -> dict:
    item = catalogs.get(doc.catalog_code) or service.get_catalog(db, doc.catalog_code)
    schema = service.get_schema(doc.catalog_code)
    return {
        "id": doc.id,
        "contract_id": doc.contract_id,
        "contract_version": doc.contract_version,
        "shipment_id": doc.shipment_id,
        "catalog_code": doc.catalog_code,
        "catalog_name": item.name,
        "step_no": item.step_no,
        "step_label": item.step_label,
        "source": doc.source,
        "party_id": doc.party_id,
        "party_name": doc.party_name,
        "received_date": doc.received_date,
        "note": doc.note,
        "file_id": doc.file_id,
        "status": doc.status,
        "void_reason": doc.void_reason,
        "confirmed_at": doc.confirmed_at,
        "confirmed_by": doc.confirmed_by,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "row_version": doc.row_version,
        "missing_required": service.missing_required(schema, doc.data) if schema else [],
        "editable": service.is_editable(db, doc, actor),
    }


def _detail(db: Session, doc: OpsContractDocument, actor: SysUser, response: Response, **extra) -> dict:
    response.headers["ETag"] = etag(doc.row_version)
    return {
        **_row(db, doc, actor, {}),
        "schema_version": doc.schema_version,
        "values": doc.data,
        "form": _form(doc.catalog_code),
        **extra,
    }


# --- Danh mục --------------------------------------------------------------------------------------------------------


@router.get(
    "/document-catalog", response_model=list[CatalogOut], summary="Danh mục giấy tờ, chứng từ nghiệp vụ lập sẵn"
)
def list_catalog(
    source: str | None = Query(None, pattern="^(customer|partner)$"),
    level: str | None = Query(None, pattern="^(contract|shipment)$"),
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return service.list_catalog(db, source, level)


@router.get("/document-catalog/{code}", response_model=CatalogDetailOut, summary="Một loại giấy tờ + biểu mẫu nhập")
def get_catalog(code: str, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    item = service.get_catalog(db, code)
    return {**CatalogOut.model_validate(item).model_dump(), "form": _form(code)}


# --- Giấy tờ của hợp đồng ----------------------------------------------------------------------------------------


@router.get(
    "/service-contracts/{contract_id:int}/documents",
    response_model=list[ContractDocOut],
    summary="Giấy tờ đã khai báo của hợp đồng (theo bước chuỗi nghiệp vụ)",
)
def list_documents(
    contract_id: int,
    shipment_id: int | None = Query(None, gt=0),
    catalog_code: str | None = Query(None, max_length=50),
    status_: str | None = Query(None, alias="status", pattern="^(draft|confirmed|void)$"),
    actor: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    docs = service.list_docs(db, contract_id, shipment_id, catalog_code, status_)
    catalogs = {c.code: c for c in service.list_catalog(db, include_inactive=True)}
    rows = [_row(db, d, actor, catalogs) for d in docs]
    return sorted(rows, key=lambda r: (r["step_no"], r["id"]))


@router.post(
    "/service-contracts/{contract_id:int}/documents",
    response_model=ContractDocDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Khai báo giấy tờ (chọn từ danh mục) – lưu ngay vào CSDL của hợp đồng",
)
def declare_document(
    contract_id: int,
    response: Response,
    catalog_code: Annotated[str, Form(min_length=2, max_length=50, description="Mã trong danh mục giấy tờ")],
    shipment_id: Annotated[int | None, Form(gt=0, description="Bắt buộc với giấy tờ cấp lô")] = None,
    party_id: Annotated[int | None, Form(gt=0, description="Đối tác phát hành (giấy tờ nguồn partner)")] = None,
    party_name: Annotated[str | None, Form(max_length=300)] = None,
    received_date: Annotated[date | None, Form()] = None,
    note: Annotated[str | None, Form(max_length=500)] = None,
    file: Annotated[UploadFile | None, File(description="Bản scan / ảnh / pdf / docx (tùy chọn)")] = None,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    upload = file if file is not None and file.filename else None
    doc = service.declare(
        db, contract_id, catalog_code, shipment_id, party_id, (party_name or "").strip() or None,
        received_date, (note or "").strip() or None, upload, actor,
    )  # fmt: skip
    response.headers["Location"] = f"/api/v2/ops/contract-documents/{doc.id}"
    return _detail(db, doc, actor, response)


@router.get(
    "/contract-documents/{doc_id:int}",
    response_model=ContractDocDetailOut,
    summary="Giấy tờ + dữ liệu đã nhập + biểu mẫu",
)
def get_document(
    doc_id: int,
    response: Response,
    actor: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, service.get_doc(db, doc_id), actor, response)


@router.patch(
    "/contract-documents/{doc_id:int}",
    response_model=ContractDocDetailOut,
    summary="Tự động lưu: ghi ngay các trường vừa nhập",
)
def patch_document(
    doc_id: int,
    body: DocPatchIn,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    meta = body.model_dump(exclude_unset=True, exclude={"values"})
    doc = service.update(db, doc_id, body.values, meta, actor, parse_if_match(if_match))
    return _detail(db, doc, actor, response)


@router.put(
    "/contract-documents/{doc_id:int}/file", response_model=ContractDocDetailOut, summary="Đính / thay file giấy tờ"
)
def put_file(
    doc_id: int,
    response: Response,
    file: Annotated[UploadFile, File()],
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    doc = service.attach_file(db, doc_id, file, actor, parse_if_match(if_match))
    return _detail(db, doc, actor, response)


@router.post(
    "/contract-documents/{doc_id:int}:confirm",
    response_model=ContractDocDetailOut,
    summary="Xác nhận – khóa; giấy tờ cấp lô → chứng từ lô (BR-103); vận đơn → điền chỗ trống của lô",
)
def confirm_document(
    doc_id: int,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    doc, applied, registered = service.confirm(db, doc_id, actor, parse_if_match(if_match))
    return _detail(db, doc, actor, response, shipment_update=applied, shipment_document=registered)


@router.post(
    "/contract-documents/{doc_id:int}:reopen", response_model=ContractDocDetailOut, summary="Mở lại để sửa (ghi lý do)"
)
def reopen_document(
    doc_id: int,
    body: ReasonIn,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, service.reopen(db, doc_id, body.reason, actor, parse_if_match(if_match)), actor, response)


@router.post(
    "/contract-documents/{doc_id:int}:void",
    response_model=ContractDocDetailOut,
    summary="Hủy giấy tờ khai báo nhầm (ghi lý do, không xóa)",
)
def void_document(
    doc_id: int,
    body: ReasonIn,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, service.void(db, doc_id, body.reason, actor, parse_if_match(if_match)), actor, response)


# --- Xem file theo trang -------------------------------------------------------------------------------------------


def _file_of(db: Session, doc_id: int):
    doc = service.get_doc(db, doc_id)
    if doc.file_id is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Giấy tờ chưa đính file")
    file = db.get(SysFile, doc.file_id)
    return file, get_storage().path(file.storage_key)


def _page_error(exc: pages.PageError) -> AppError:
    return AppError(ErrorCode.NOT_FOUND if exc.status == 404 else ErrorCode.VALIDATION_ERROR, exc.status, str(exc))


@router.get("/contract-documents/{doc_id:int}/pages", response_model=PagesOut, summary="Danh sách trang của file")
def list_pages(doc_id: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    file, path = _file_of(db, doc_id)
    try:
        infos = pages.describe(path, file.mime_type)
    except pages.PageError as exc:
        raise _page_error(exc) from exc
    return {
        "document_id": doc_id,
        "filename": file.original_filename,
        "mime_type": file.mime_type,
        "page_count": len(infos),
        "pages": [{"number": p.number, "kind": p.kind, "label": p.label} for p in infos],
    }


@router.get(
    "/contract-documents/{doc_id:int}/pages/{number:int}/image",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
    summary="Ảnh một trang (PNG)",
)
def page_image(doc_id: int, number: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    file, path = _file_of(db, doc_id)
    try:
        data = pages.image_of(path, file.mime_type, number)
    except pages.PageError as exc:
        raise _page_error(exc) from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get(
    "/contract-documents/{doc_id:int}/pages/{number:int}/text",
    response_class=Response,
    responses={200: {"content": {"text/plain": {}}}},
    summary="Văn bản một trang (docx, xlsx, txt)",
)
def page_text(doc_id: int, number: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    file, path = _file_of(db, doc_id)
    try:
        data = pages.text_of(path, file.mime_type, number)
    except pages.PageError as exc:
        raise _page_error(exc) from exc
    return Response(data, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "private, max-age=300"})
