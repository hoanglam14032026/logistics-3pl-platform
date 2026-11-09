"""
API trích xuất chứng từ bằng AI local (Giai đoạn 9) – dùng chung cho mọi tiến trình có nhập chứng từ.

- `GET /api/v2/doc-types`: loại chứng từ (schema JSON trong app/shared/docai/schemas/) – giao diện chọn loại, vẽ form.
- `POST /api/v2/doc-jobs` (multipart: `file`, `doc_type`, `model_id` **bắt buộc** – id model đang bật từ
  `GET /api/v2/ai-models/active`, `ocr_model_id` tùy chọn) → `202`, job `queued`; OCR + AI chạy nền.
- `GET /api/v2/doc-jobs/{id}`: bản nháp tự điền (`draft`, `form`) + độ tin cậy; `GET …/extractions`: văn bản OCR, bbox.
- `PATCH /api/v2/doc-jobs/{id}/review`, `POST …/approve`, `POST …/reject` (If-Match).

Quyền: `ops_staff` (job của mình), `manager` (mọi job). Vai trò khác, `customer` → 403. Giới hạn tần suất + kiểm trước
khi đọc body cho upload: middleware Giai đoạn 7 (`multipart/form-data` tính thêm hạn mức `file_upload`).
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core import jobs
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.core.versioning import etag, parse_if_match
from app.shared.docai import registry, tasks
from app.shared.docai import service as docs
from app.shared.docai.api_schemas import (
    ApproveIn,
    DocJobDetailOut,
    DocJobOut,
    DocTypeOut,
    DraftFieldOut,
    ExtractionOut,
    RejectIn,
    ReviewIn,
)
from app.shared.docai.models import JobStatus, SysDocJob
from app.shared.files.schemas import FileOut
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/doc-jobs", tags=["Document AI – jobs"])
types_router = APIRouter(prefix="/api/v2/doc-types", tags=["Document AI – document types"])

_worker = require_roles(Role.OPS_STAFF, Role.MANAGER)
_IF_MATCH = Header(None, description="ETag nhận từ GET; lệch → 409 VERSION_CONFLICT")


def _doc_type_out(schema: registry.DocTypeSchema) -> DocTypeOut:
    return DocTypeOut.model_validate(schema.model_dump())


@types_router.get("", response_model=list[DocTypeOut], summary="Loại chứng từ AI trích xuất được")
def list_doc_types(_: SysUser = Depends(_worker)):  # noqa: B008
    return [_doc_type_out(s) for s in registry.all_schemas().values()]


@types_router.get("/{code}", response_model=DocTypeOut, summary="Schema một loại chứng từ")
def get_doc_type(code: str, _: SysUser = Depends(_worker)):  # noqa: B008
    schema = registry.get_schema(code)
    if schema is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không có loại chứng từ này")
    return _doc_type_out(schema)


def _detail(db: Session, response: Response, job: SysDocJob) -> DocJobDetailOut:
    response.headers["ETag"] = etag(job.row_version)
    schema = registry.get_schema(job.doc_type)
    min_conf = get_settings().docai_min_confidence
    low = set((job.review_reasons or {}).get("low_confidence", []))
    draft = []
    if schema is not None and job.fields:
        for spec in schema.fields:
            f = job.fields.get(spec.key) or {}
            draft.append(
                DraftFieldOut(
                    key=spec.key,
                    label=spec.label,
                    type=spec.type,
                    required=spec.required,
                    form_field=spec.form_field,
                    value=f.get("value"),
                    confidence=f.get("confidence") or 0.0,
                    ai_value=f.get("ai_value"),
                    ai_confidence=f.get("ai_confidence") or 0.0,
                    grounded=f.get("grounded"),
                    source=f.get("source", "ai"),
                    low_confidence=spec.key in low
                    or (f.get("value") is not None and (f.get("confidence") or 0) < min_conf),
                    issue=f.get("issue"),
                )
            )
    file = docs.file_of(db, job)
    return DocJobDetailOut.model_validate(job).model_copy(
        update={
            "file": FileOut.model_validate(file) if file else None,
            "doc_type_name": schema.name if schema else None,
            "draft": draft,
            "form": docs.form_of(job),
            "approved_data": job.approved_data,
            "create_hint": docs.create_hint(job),
        }
    )


@router.post(
    "",
    response_model=DocJobDetailOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Tải chứng từ lên + tạo job trích xuất (OCR + AI local chạy nền)",
    description=(
        "File: jpg, png, tiff, webp, pdf, docx, xlsx, txt ≤ 20 MB (nhận dạng theo nội dung; 0 byte → 422, "
        "quá dung lượng → 413, sai loại → 415; doc/xls cũ chưa nhận). `model_id`: id model đang bật "
        "(GET /api/v2/ai-models/active) – bắt buộc, hệ thống không tự chọn."
    ),
)
def create_job(
    file: Annotated[UploadFile, File(description="Chứng từ (scan ảnh, PDF, docx, xlsx, txt)")],
    doc_type: Annotated[str, Form(min_length=2, max_length=50, description="Mã loại chứng từ – GET /api/v2/doc-types")],
    model_id: Annotated[int, Form(gt=0, description="id model AI đã chọn (GET /api/v2/ai-models/active)")],
    background: BackgroundTasks,
    response: Response,
    ocr_model_id: Annotated[int | None, Form(gt=0, description="Tùy chọn: model loại ocr thay Tesseract")] = None,
    actor: SysUser = Depends(_worker),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    job = docs.create_job(db, file, doc_type, model_id, ocr_model_id, actor)
    via = jobs.enqueue(
        background, tasks.process_doc_job, job.id, actor.id, timeout=get_settings().docai_job_timeout_sec
    )
    docs.mark_queued_via(db, job.id, via)
    job.queued_via = via
    response.headers["Location"] = f"/api/v2/doc-jobs/{job.id}"
    return _detail(db, response, job)


@router.get("", response_model=Page[DocJobOut], summary="Danh sách job trích xuất")
def list_jobs(
    params: PageParams = Depends(),  # noqa: B008
    status_: JobStatus | None = Query(None, alias="status"),
    doc_type: str | None = Query(None, max_length=50),
    mine: bool = Query(False, description="manager: chỉ job mình tạo"),
    actor: SysUser = Depends(_worker),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = docs.list_jobs(db, actor, params, status_, doc_type, mine)
    return Page[DocJobOut](
        items=[DocJobOut.model_validate(j) for j in items], total=total, page=params.page, page_size=params.page_size
    )


@router.get("/{job_id:int}", response_model=DocJobDetailOut, summary="Chi tiết job: bản nháp tự điền + độ tin cậy")
def get_job(
    job_id: int,
    response: Response,
    actor: SysUser = Depends(_worker),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, response, docs.get_job(db, job_id, actor))


@router.get(
    "/{job_id:int}/extractions",
    response_model=list[ExtractionOut],
    summary="Các lần OCR + AI: văn bản theo trang, bbox từng từ, đầu ra thô của model",
)
def list_extractions(
    job_id: int,
    actor: SysUser = Depends(_worker),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return docs.extractions(db, docs.get_job(db, job_id, actor))


@router.patch("/{job_id:int}/review", response_model=DocJobDetailOut, summary="Người duyệt sửa từng trường")
def review_job(
    job_id: int,
    body: ReviewIn,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_worker),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, response, docs.review(db, job_id, body.fields, actor, parse_if_match(if_match)))


@router.post(
    "/{job_id:int}/approve",
    response_model=DocJobDetailOut,
    summary="Duyệt – sau đó mới tạo được bản ghi nghiệp vụ (source_doc_job_id)",
)
def approve_job(
    job_id: int,
    response: Response,
    body: ApproveIn | None = None,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_worker),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    confirmed = body.confirmed_fields if body else []
    return _detail(db, response, docs.approve(db, job_id, confirmed, actor, parse_if_match(if_match)))


@router.post("/{job_id:int}/reject", response_model=DocJobDetailOut, summary="Từ chối (kèm lý do)")
def reject_job(
    job_id: int,
    body: RejectIn,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_worker),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _detail(db, response, docs.reject(db, job_id, body.reason, actor, parse_if_match(if_match)))
