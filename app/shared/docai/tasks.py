"""
Tác vụ nền OCR + AI trích xuất (Giai đoạn 9) – chạy qua rq + Redis (`app.core.jobs`), như gửi email Giai đoạn 7.

- Tham số chỉ là `job_id`, `user_id` – Redis không chứa file, văn bản OCR, kết quả AI, token hay bí mật. Tác vụ tự đọc
  CSDL + kho file nội bộ, gọi model local (`ai_models` → `OLLAMA_URL`).
- Lỗi tạm thời (máy chủ model không kết nối được, quá thời gian…): trong worker còn lượt → job về `queued`, ném lỗi để
  rq thử lại (`JOB_MAX_RETRIES` lần theo `JOB_RETRY_INTERVALS`); hết lượt → job `failed` + nhật ký `doc.failed`, tác
  vụ vào FailedJobRegistry. Chạy nền trong tiến trình API (Redis lỗi lúc xếp) / lỗi cố định (file hỏng, model đã tắt,
  đầu ra không phải JSON…): `failed` + `doc.failed` ngay, không thử lại. Request gốc không bị lỗi.
- "Ít nhất một lần": chạy lặp an toàn – job không còn `queued`/`processing` thì bỏ qua.
- OCR + gọi model chạy **ngoài** giao dịch CSDL (có thể vài phút); chỉ khóa dòng job lúc nhận việc và lúc ghi kết quả.
"""

import logging
import time
from dataclasses import dataclass

from sqlalchemy import func, select

from app.core import jobs
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.netguard import ExternalAddressError
from app.core.timeutil import now_utc
from app.shared.ai_models import ollama
from app.shared.ai_models.models import AiModel
from app.shared.docai import convert, extract, ocr, registry
from app.shared.docai import service as docs
from app.shared.docai.models import JobStatus, SysDocExtraction, SysDocJob
from app.shared.events.contracts import DocExtracted, DocFailed
from app.shared.files.models import SysFile
from app.shared.files.storage import get_storage

logger = logging.getLogger("app.docai")


class PermanentError(Exception):
    """Lỗi không hết khi thử lại."""


@dataclass
class Snapshot:
    job_id: int
    doc_type: str
    ocr_engine: str
    model_type: str
    model_ref: str
    endpoint: str | None
    ocr_model: tuple[str | None, str] | None
    storage_key: str
    mime: str


def _claim(job_id: int, user_id: int) -> Snapshot | None:
    with SessionLocal() as db:
        job = db.scalar(select(SysDocJob).where(SysDocJob.id == job_id).with_for_update())
        if job is None or job.created_by != user_id:
            logger.warning("Bỏ qua tác vụ trích xuất: job %s không có hoặc không khớp người tạo", job_id)
            return None
        if job.status not in (JobStatus.QUEUED.value, JobStatus.PROCESSING.value):
            return None
        model = db.get(AiModel, job.ai_model_id)
        ocr_model = db.get(AiModel, job.ocr_model_id) if job.ocr_model_id else None
        file = db.get(SysFile, job.file_id)
        job.status = JobStatus.PROCESSING.value
        job.attempts += 1
        job.started_at = now_utc()
        db.commit()
        problems = [m.display_name for m in (model, ocr_model) if m is not None and (not m.is_active or m.deleted_at)]
        snapshot = Snapshot(
            job_id=job.id,
            doc_type=job.doc_type,
            ocr_engine=job.ocr_engine,
            model_type=job.model_type,
            model_ref=job.model_ref,
            endpoint=model.endpoint,
            ocr_model=(ocr_model.endpoint, job.ocr_model_ref) if ocr_model else None,
            storage_key=file.storage_key,
            mime=file.mime_type,
        )
    if problems:
        raise PermanentError(f"Model đã tắt/xóa khỏi danh mục sau khi tạo job: {', '.join(problems)}")
    return snapshot


def _run(snap: Snapshot) -> tuple[dict, SysDocExtraction, list[str]]:
    settings = get_settings()
    schema = registry.get_schema(snap.doc_type)
    if schema is None:
        raise PermanentError(f"Schema loại chứng từ '{snap.doc_type}' không còn")
    want_images = settings.docai_vision_max_pages if snap.model_type == "vision" else 0
    started = time.monotonic()
    try:
        path = get_storage().path(snap.storage_key)
        converted = convert.convert(path, snap.mime, settings.docai_max_pages, settings.docai_pdf_dpi, want_images)
    except convert.ConversionError as exc:
        raise PermanentError(str(exc)) from exc
    pages, texts = [], []
    for page in converted.pages:
        words, mean = [], None
        text = page.text or ""
        if page.needs_ocr and page.image:
            try:
                result = ocr.ocr_image(page.image, snap.ocr_engine, snap.ocr_model)
            except ocr.OcrError as exc:
                if exc.transient:
                    raise
                raise PermanentError(str(exc)) from exc
            text, words, mean = result.text, result.words, result.mean_confidence
        pages.append(
            {
                "page": page.number,
                "source": page.source,
                "label": page.label,
                "text": text,
                "mean_confidence": mean,
                "words": words,
            }
        )
        header = f"--- Trang {page.number}" + (f" ({page.label})" if page.label else "") + " ---"
        texts.append(f"{header}\n{text}")
    source_text = "\n\n".join(texts)
    ocr_ms = int((time.monotonic() - started) * 1000)
    warnings = list(converted.warnings)
    if not source_text.strip() or not any(p["text"].strip() for p in pages):
        warnings.append("Không đọc được chữ nào từ chứng từ (OCR rỗng)")

    system, user, truncated = extract.build_prompt(schema, source_text, settings.docai_max_text_chars)
    if truncated:
        warnings.append(f"Văn bản dài hơn {settings.docai_max_text_chars} ký tự – model chỉ đọc phần đầu")
    images = [p.image for p in converted.pages if p.image][:want_images] if want_images else None
    started = time.monotonic()
    try:
        raw = ollama.chat_json(snap.endpoint, snap.model_ref, system, user, extract.output_schema(schema), images)
        data = extract.parse_output(raw)
    except ollama.OllamaError as exc:
        if exc.transient:
            raise
        raise PermanentError(str(exc)) from exc
    except extract.ExtractionError as exc:
        raise PermanentError(str(exc)) from exc
    llm_ms = int((time.monotonic() - started) * 1000)
    fields = extract.build_fields(schema, data, source_text)
    extraction = SysDocExtraction(
        job_id=snap.job_id,
        run_no=0,
        ocr_engine=snap.ocr_engine,
        model_ref=snap.model_ref,
        pages=pages,
        text_chars=len(source_text),
        raw_output=raw,
        result={k: {"value": f["value"], "confidence": f["confidence"]} for k, f in fields.items()},
        ocr_ms=ocr_ms,
        llm_ms=llm_ms,
    )
    return fields, extraction, warnings


def _save(snap: Snapshot, fields: dict, extraction: SysDocExtraction, warnings: list[str]) -> None:
    with SessionLocal() as db:
        job = db.scalar(select(SysDocJob).where(SysDocJob.id == snap.job_id).with_for_update())
        if job is None or job.status != JobStatus.PROCESSING.value:
            return
        schema = registry.get_schema(job.doc_type)
        last = db.scalar(select(func.max(SysDocExtraction.run_no)).where(SysDocExtraction.job_id == job.id))
        extraction.run_no = (last or 0) + 1
        db.add(extraction)
        docs._refresh_reasons(job, schema, fields)
        job.warnings = warnings or None
        job.extracted_at = now_utc()
        job.last_error = None
        db.flush()
        with docs.request_context_of(job):
            docs.record(
                db,
                "doc_job.extracted",
                job,
                None,
                after={
                    "status": job.status,
                    "extraction_id": extraction.id,
                    "run_no": extraction.run_no,
                    "model_code": job.model_code,
                    "model_ref": job.model_ref,
                    "ocr_engine": job.ocr_engine,
                    "fields": {k: {"value": f["value"], "confidence": f["confidence"]} for k, f in fields.items()},
                    "review_reasons": job.review_reasons,
                    "warnings": warnings,
                    "ocr_ms": extraction.ocr_ms,
                    "llm_ms": extraction.llm_ms,
                },
            )
            reasons = job.review_reasons or {}
            docs.publish(
                db,
                DocExtracted,
                job,
                None,
                overall_confidence=job.overall_confidence,
                low_confidence_count=len(reasons.get("low_confidence", [])),
                missing_required_count=len(reasons.get("missing_required", [])),
            )
        db.commit()


def _fail(job_id: int, message: str, final: bool) -> None:
    attempt_no, _ = jobs.attempt()
    with SessionLocal() as db:
        job = db.scalar(select(SysDocJob).where(SysDocJob.id == job_id).with_for_update())
        if job is None or job.status not in (JobStatus.QUEUED.value, JobStatus.PROCESSING.value):
            return
        job.last_error = message[:1000]
        if not final:
            job.status = JobStatus.QUEUED.value  # chờ rq thử lại
            db.commit()
            return
        job.status = JobStatus.FAILED.value
        job.failed_at = now_utc()
        db.flush()
        with docs.request_context_of(job):
            docs.record(
                db,
                "doc.failed",
                job,
                None,
                after={
                    "error": message[:500],
                    "attempts": job.attempts,
                    "model_code": job.model_code,
                    "doc_type": job.doc_type,
                    "in_worker": jobs.in_worker(),
                    "attempt_no": attempt_no,
                },
            )
            docs.publish(db, DocFailed, job, None, attempts=job.attempts)
        db.commit()


def process_doc_job(job_id: int, user_id: int) -> None:
    """Tác vụ nền: OCR + AI trích xuất cho một job. Không bao giờ làm lỗi request đã tải chứng từ lên."""
    try:
        snap = _claim(job_id, user_id)
        if snap is None:
            return
        fields, extraction, warnings = _run(snap)
        _save(snap, fields, extraction, warnings)
    except (PermanentError, ExternalAddressError) as exc:
        logger.warning("Job trích xuất %s lỗi cố định: %s", job_id, exc)
        _fail(job_id, str(exc), final=True)
    except Exception as exc:
        _, last = jobs.attempt()
        final = last or not jobs.in_worker()
        logger.error("Job trích xuất %s lỗi (%s)%s", job_id, type(exc).__name__, "" if final else " – rq sẽ thử lại")
        _fail(job_id, f"{type(exc).__name__}: {exc}", final=final)
        if jobs.in_worker():
            raise  # còn lượt: rq thử lại; hết lượt: vào FailedJobRegistry (quản trị xem/chạy lại) – job đã failed
