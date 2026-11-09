"""
Job trích xuất chứng từ bằng AI local (Giai đoạn 9) – module dùng chung cho mọi tiến trình có nhập chứng từ.

Upload → OCR → AI trích xuất → tự điền form → người duyệt xem, sửa (`PATCH …/review`) → duyệt (`POST …/approve`) /
từ chối (`POST …/reject`). Job **chưa duyệt** không tạo được bản ghi nghiệp vụ (`source_doc_job_id` – app/shared/docai/
linking.py). Đủ ngưỡng tin cậy vẫn phải duyệt; `needs_review`: trường dưới ngưỡng chưa sửa phải được người duyệt xác
nhận từng trường (`confirmed_fields`), thiếu trường bắt buộc → không duyệt được.

Quyền (06 §4): `ops_staff` – job của mình; `manager` – mọi job; vai trò khác (kể cả `customer`) → 403 ở router. Không có
quyền với job → 404 như không tồn tại. Nhật ký: `file.uploaded`, `doc_job.create` (model đã chọn), `doc_job.extracted`
(AI trích ra gì), `doc_job.review` (giá trị AI / trước / sau), `doc_job.approve`, `doc_job.reject`, `doc.failed`,
`doc_job.linked` – kèm `request_id`, `ip` (tác vụ nền ghi `request_id`, `ip` của request tải lên).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.pagination import PageParams, order_by_clause, paginate
from app.core.rbac import Role
from app.core.request_context import client_ip_var, current_client_ip, current_request_id, request_id_var
from app.core.timeutil import now_utc
from app.shared.ai_models import service as ai_models
from app.shared.ai_models.models import ModelType
from app.shared.audit import service as audit
from app.shared.docai import convert, extract, registry
from app.shared.docai.models import REVIEWABLE, JobStatus, OcrEngine, SysDocExtraction, SysDocJob
from app.shared.files import service as files
from app.shared.files.models import SysFile
from app.shared.files.storage import get_storage

ENTITY = "doc_job"
FILE_PURPOSE = "doc_ai_source"
TARGET_URLS = {
    "party": "/api/v2/md/parties",
    "service_contract": "/api/v2/ops/service-contracts",
    "shipment": "/api/v2/ops/shipments",
}
_SORTABLE = {"created_at": SysDocJob.created_at, "status": SysDocJob.status, "doc_type": SysDocJob.doc_type}
_CAPABILITY = {"pdf": "supports_pdf", "image": "supports_image", "docx": "supports_docx"}
_KIND_LABEL = {"pdf": "PDF", "image": "ảnh", "docx": "docx/xlsx/txt"}


# --- Lỗi, quyền --------------------------------------------------------------------------------------------------


def field_error(field: str, message: str, issue: str = "invalid") -> AppError:
    return AppError(ErrorCode.VALIDATION_ERROR, 422, message, [{"field": field, "issue": issue, "message": message}])


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def not_found() -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy job trích xuất")


def can_view(user, job: SysDocJob) -> bool:
    roles = set(user.role_codes)
    return Role.MANAGER.value in roles or (Role.OPS_STAFF.value in roles and job.created_by == user.id)


def _check_version(job: SysDocJob, expected: int | None) -> None:
    if expected is not None and expected != job.row_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, 409, "Bản ghi đã bị người khác thay đổi; tải lại rồi thử lại")


def get_job(db: Session, job_id: int, user, for_update: bool = False) -> SysDocJob:
    stmt = select(SysDocJob).where(SysDocJob.id == job_id)
    job = db.scalar(stmt.with_for_update() if for_update else stmt)
    if job is None or not can_view(user, job):
        raise not_found()
    return job


def schema_of(job: SysDocJob) -> registry.DocTypeSchema:
    schema = registry.get_schema(job.doc_type)
    if schema is None:
        raise _conflict(f"Schema loại chứng từ '{job.doc_type}' không còn trong app/shared/docai/schemas/")
    return schema


# --- Nhật ký -----------------------------------------------------------------------------------------------------


def _short(value: Any) -> Any:
    """Rút gọn giá trị dài khi ghi nhật ký (bản đầy đủ nằm ở sys_doc_extractions / sys_doc_jobs)."""
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + "…"
    if isinstance(value, list):
        return [_short(v) for v in value[:20]] + (["…"] if len(value) > 20 else [])
    if isinstance(value, dict):
        return {k: _short(v) for k, v in value.items()}
    return value


def record(db: Session, action: str, job: SysDocJob, actor_id: int | None, before=None, after=None) -> None:
    audit.record(db, action, ENTITY, job.id, actor_id, _short(before) if before else None, _short(after))


@contextmanager
def request_context_of(job: SysDocJob) -> Iterator[None]:
    """Tác vụ nền: nhật ký mang `request_id`, `ip` của request đã tải chứng từ lên."""
    t1, t2 = request_id_var.set(job.request_id), client_ip_var.set(job.client_ip)
    try:
        yield
    finally:
        request_id_var.reset(t1)
        client_ip_var.reset(t2)


# --- Tạo job -----------------------------------------------------------------------------------------------------


def create_job(
    db: Session, upload: UploadFile, doc_type: str, model_id: int, ocr_model_id: int | None, actor
) -> SysDocJob:
    schema = registry.get_schema(doc_type)
    if schema is None:
        known = ", ".join(sorted(registry.all_schemas()))
        raise field_error("body.doc_type", f"Loại chứng từ không có (có: {known})", "not_found")
    model = ai_models.usable_for_job(db, model_id, "body.model_id", {ModelType.VISION, ModelType.TEXT})
    ocr_model = None
    if ocr_model_id is not None:
        ocr_model = ai_models.usable_for_job(db, ocr_model_id, "body.ocr_model_id", {ModelType.OCR})
    storage = get_storage()
    stored = files.store(db, upload, FILE_PURPOSE, "docai", ENTITY, 0, actor.id, storage)
    try:
        kind = convert.kind_of(stored.mime_type)
        for m, field in ((model, "body.model_id"), (ocr_model, "body.ocr_model_id")):
            if m is not None and not getattr(m, _CAPABILITY[kind]):
                message = f"Model '{m.display_name}' không hỗ trợ file {_KIND_LABEL[kind]}"
                raise field_error(field, message, "capability")
        engine = OcrEngine.OLLAMA if ocr_model else OcrEngine(get_settings().docai_ocr_engine)
        job = SysDocJob(
            doc_type=schema.code,
            schema_version=schema.version,
            target_type=schema.target,
            file_id=stored.id,
            ai_model_id=model.id,
            model_code=model.code,
            model_ref=model.model_id,
            model_type=model.model_type,
            ocr_engine=engine.value,
            ocr_model_id=ocr_model.id if ocr_model else None,
            ocr_model_ref=ocr_model.model_id if ocr_model else None,
            status=JobStatus.QUEUED.value,
            request_id=current_request_id(),
            client_ip=current_client_ip(),
            created_by=actor.id,
            updated_by=actor.id,
        )
        db.add(job)
        db.flush()
        stored.owner_id = job.id
        audit.record(
            db,
            "file.uploaded",
            "file",
            stored.id,
            actor.id,
            after={
                "purpose": FILE_PURPOSE,
                "owner_type": ENTITY,
                "owner_id": job.id,
                "original_filename": stored.original_filename,
                "mime_type": stored.mime_type,
                "size_bytes": stored.size_bytes,
                "sha256": stored.sha256,
            },
        )
        record(
            db,
            "doc_job.create",
            job,
            actor.id,
            after={
                "doc_type": job.doc_type,
                "schema_version": job.schema_version,
                "file_id": stored.id,
                "ai_model_id": model.id,
                "model_code": model.code,
                "model_ref": model.model_id,
                "ocr_engine": engine.value,
                "ocr_model_id": job.ocr_model_id,
                "ocr_model_ref": job.ocr_model_ref,
            },
        )
    except BaseException:
        db.rollback()
        storage.delete(stored.storage_key)
        raise
    files.commit_or_discard(db, [stored], storage)
    return job


def mark_queued_via(db: Session, job_id: int, via: str) -> None:
    db.execute(update(SysDocJob).where(SysDocJob.id == job_id).values(queued_via=via))
    db.commit()


def list_jobs(
    db: Session, user, params: PageParams, status: JobStatus | None, doc_type: str | None, mine: bool
) -> tuple[list[SysDocJob], int]:
    stmt = select(SysDocJob)
    roles = set(user.role_codes)
    if mine or Role.MANAGER.value not in roles:
        stmt = stmt.where(SysDocJob.created_by == user.id)
    if status is not None:
        stmt = stmt.where(SysDocJob.status == status.value)
    if doc_type:
        stmt = stmt.where(SysDocJob.doc_type == doc_type)
    stmt = stmt.order_by(order_by_clause(params.sort, _SORTABLE), SysDocJob.id.desc())
    return paginate(db, stmt, params)


def file_of(db: Session, job: SysDocJob) -> SysFile:
    return db.get(SysFile, job.file_id)


def extractions(db: Session, job: SysDocJob) -> list[SysDocExtraction]:
    stmt = select(SysDocExtraction).where(SysDocExtraction.job_id == job.id).order_by(SysDocExtraction.run_no)
    return list(db.scalars(stmt))


def current_values(job: SysDocJob) -> dict[str, Any]:
    return {k: f.get("value") for k, f in (job.fields or {}).items()}


# --- Người duyệt xem, sửa ----------------------------------------------------------------------------------------


def _refresh_reasons(job: SysDocJob, schema: registry.DocTypeSchema, fields: dict[str, dict]) -> None:
    reasons = extract.review_reasons(schema, fields, get_settings().docai_min_confidence)
    job.fields = fields
    job.review_reasons = reasons
    job.overall_confidence = extract.overall(fields)
    needs = bool(reasons["low_confidence"] or reasons["missing_required"])
    job.status = (JobStatus.NEEDS_REVIEW if needs else JobStatus.READY_FOR_REVIEW).value


def _reviewable(job: SysDocJob) -> None:
    if job.status not in REVIEWABLE:
        raise _conflict(f"Job đang ở trạng thái '{job.status}' – chỉ xem, sửa, duyệt khi đã trích xuất xong")


def review(db: Session, job_id: int, changes: dict[str, Any], actor, expected: int | None) -> SysDocJob:
    job = get_job(db, job_id, actor, for_update=True)
    _check_version(job, expected)
    _reviewable(job)
    schema = schema_of(job)
    fields = {k: dict(v) for k, v in (job.fields or {}).items()}
    edits: dict[str, dict] = {}
    for key, raw in changes.items():
        spec = schema.field(key)
        if spec is None:
            raise field_error(f"body.fields.{key}", f"Loại chứng từ '{schema.code}' không có trường '{key}'", "unknown")
        try:
            value = registry.normalize(spec, raw)
        except registry.InvalidValue as exc:
            raise field_error(f"body.fields.{key}", str(exc)) from exc
        current = fields.setdefault(key, {"value": None, "confidence": 0.0, "ai_value": None, "ai_confidence": 0.0})
        if value == current.get("value"):
            continue
        edits[key] = {"ai": current.get("ai_value"), "before": current.get("value"), "after": value}
        current.update(
            value=value,
            confidence=1.0,
            source="human",
            issue=None,
            reviewed_by=actor.id,
            reviewed_at=now_utc().isoformat(),
        )
    if not edits:
        return job
    old_status = job.status
    _refresh_reasons(job, schema, fields)
    job.updated_by = actor.id
    db.flush()
    record(
        db,
        "doc_job.review",
        job,
        actor.id,
        before={"status": old_status},
        after={"fields": edits, "status": job.status, "review_reasons": job.review_reasons},
    )
    db.commit()
    return job


def approve(db: Session, job_id: int, confirmed: list[str], actor, expected: int | None) -> SysDocJob:
    job = get_job(db, job_id, actor, for_update=True)
    _check_version(job, expected)
    _reviewable(job)
    schema = schema_of(job)
    fields = {k: dict(v) for k, v in (job.fields or {}).items()}
    reasons = extract.review_reasons(schema, fields, get_settings().docai_min_confidence)
    if reasons["missing_required"]:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            "Thiếu trường bắt buộc – nhập bổ sung (PATCH …/review) trước khi duyệt",
            [
                {"field": f"body.fields.{k}", "issue": "missing", "message": "Trường bắt buộc chưa có giá trị"}
                for k in reasons["missing_required"]
            ],
        )
    unknown = sorted(set(confirmed) - {f.key for f in schema.fields})
    if unknown:
        raise field_error("body.confirmed_fields", f"Trường không có: {', '.join(unknown)}", "unknown")
    unconfirmed = [k for k in reasons["low_confidence"] if k not in confirmed]
    if unconfirmed:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            "Có trường độ tin cậy thấp chưa được kiểm – sửa giá trị hoặc xác nhận từng trường (confirmed_fields)",
            [
                {
                    "field": f"body.confirmed_fields.{k}",
                    "issue": "unconfirmed",
                    "message": f"Độ tin cậy {fields[k].get('confidence')} < {get_settings().docai_min_confidence}",
                }
                for k in unconfirmed
            ],
        )
    for key in reasons["low_confidence"]:
        fields[key].update(confirmed_by=actor.id, confirmed_at=now_utc().isoformat())
    values = {k: f.get("value") for k, f in fields.items()}
    old_status = job.status
    job.fields = fields
    job.approved_data = values
    job.status = JobStatus.APPROVED.value
    job.approved_at = now_utc()
    job.approved_by = actor.id
    job.updated_by = actor.id
    db.flush()
    record(
        db,
        "doc_job.approve",
        job,
        actor.id,
        before={"status": old_status},
        after={
            "status": job.status,
            "approved_data": values,
            "confirmed_fields": reasons["low_confidence"],
            "human_fields": sorted(k for k, f in fields.items() if f.get("source") == "human"),
            "target_type": job.target_type,
        },
    )
    db.commit()
    return job


def reject(db: Session, job_id: int, reason: str, actor, expected: int | None) -> SysDocJob:
    job = get_job(db, job_id, actor, for_update=True)
    _check_version(job, expected)
    _reviewable(job)
    old_status = job.status
    job.status = JobStatus.REJECTED.value
    job.rejected_at = now_utc()
    job.rejected_by = actor.id
    job.reject_reason = reason
    job.updated_by = actor.id
    db.flush()
    record(db, "doc_job.reject", job, actor.id, {"status": old_status}, {"status": job.status, "reason": reason})
    db.commit()
    return job


def form_of(job: SysDocJob) -> dict[str, Any]:
    schema = registry.get_schema(job.doc_type)
    if schema is None or schema.target is None:
        return {}
    data = job.approved_data if job.status == JobStatus.APPROVED.value else current_values(job)
    return extract.form_payload(schema, data or {})


def create_hint(job: SysDocJob) -> dict[str, str] | None:
    if job.status != JobStatus.APPROVED.value or job.target_type is None or job.linked_entity_id is not None:
        return None
    return {"method": "POST", "url": f"{TARGET_URLS[job.target_type]}?source_doc_job_id={job.id}"}


# --- Quyền tải file gốc ------------------------------------------------------------------------------------------


def _can_read_file(db: Session, user, file: SysFile) -> bool:
    job = db.get(SysDocJob, file.owner_id)
    return job is not None and job.file_id == file.id and can_view(user, job)


def register_file_access() -> None:
    files.ACCESS_CHECKERS[ENTITY] = _can_read_file
