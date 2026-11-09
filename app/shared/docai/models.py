"""
Job trích xuất chứng từ bằng AI local (Giai đoạn 9) – dùng chung cho mọi tiến trình có nhập chứng từ.

Quy trình: Upload → OCR → AI trích xuất → tự điền form (draft + confidence) → người duyệt xem, sửa → duyệt / từ chối.

- `sys_doc_jobs`: một chứng từ tải lên = một job. Lưu model người dùng chọn (`ai_model_id` + ảnh chụp `model_code`,
  `model_ref` lúc tạo – model bị sửa/tắt/xóa sau đó job vẫn giữ để truy vết), trạng thái, bản nháp từng trường
  (`fields`: giá trị AI, độ tin cậy, giá trị người sửa), dữ liệu đã duyệt (`approved_data`), bản ghi nghiệp vụ tạo từ
  job (`linked_entity_type`/`linked_entity_id`), `request_id`/`client_ip` của request tải lên (nhật ký của tác vụ nền).
- `sys_doc_extractions`: mỗi lần chạy OCR + AI thành công = một dòng **chỉ ghi thêm** (trigger chặn sửa/xóa): văn bản
  OCR theo trang + bbox từng từ, đầu ra thô của model, kết quả đã chuẩn hóa.
- File gốc qua `sys_files` (`owner_type = "doc_job"`). Dữ liệu chỉ nằm trong CSDL / ổ đĩa nội bộ.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditColumnsMixin, Base
from app.core.timeutil import now_utc


class JobStatus(StrEnum):
    QUEUED = "queued"  # chờ (hoặc chờ thử lại) trong hàng đợi
    PROCESSING = "processing"  # OCR + AI đang chạy
    READY_FOR_REVIEW = "ready_for_review"  # mọi trường ≥ ngưỡng, đủ trường bắt buộc – vẫn phải duyệt
    NEEDS_REVIEW = "needs_review"  # có trường < ngưỡng hoặc thiếu trường bắt buộc – bắt buộc người duyệt xử lý
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


REVIEWABLE = frozenset({JobStatus.READY_FOR_REVIEW, JobStatus.NEEDS_REVIEW})


class OcrEngine(StrEnum):
    TESSERACT = "tesseract"
    PADDLE = "paddle"
    OLLAMA = "ollama"  # model loại `ocr` trong danh mục


def _in(values) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class SysDocJob(AuditColumnsMixin, Base):
    __tablename__ = "sys_doc_jobs"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(JobStatus)})", name="status_valid"),
        CheckConstraint(f"ocr_engine IN ({_in(OcrEngine)})", name="ocr_engine_valid"),
        CheckConstraint("(status = 'approved') = (approved_at IS NOT NULL)", name="approved_matches_status"),
        CheckConstraint(
            "(status = 'rejected') = (rejected_at IS NOT NULL AND reject_reason IS NOT NULL)",
            name="rejected_matches_status",
        ),
        CheckConstraint("(status = 'failed') = (failed_at IS NOT NULL)", name="failed_matches_status"),
        CheckConstraint("(linked_entity_type IS NULL) = (linked_entity_id IS NULL)", name="linked_entity_together"),
        CheckConstraint("linked_entity_id IS NULL OR status = 'approved'", name="linked_only_when_approved"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_sys_doc_jobs_created_by_created_at", "created_by", "created_at"),
        Index("ix_sys_doc_jobs_status_created_at", "status", "created_at"),
        Index("ix_sys_doc_jobs_linked_entity", "linked_entity_type", "linked_entity_id"),
    )

    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Bảng nghiệp vụ đích của loại chứng từ (party, service_contract, shipment) – NULL: chỉ trích xuất, chưa có bảng
    target_type: Mapped[str | None] = mapped_column(String(32))
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_files.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    ai_model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_models.id", ondelete="RESTRICT"), nullable=False
    )
    model_code: Mapped[str] = mapped_column(String(50), nullable=False)
    model_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    model_type: Mapped[str] = mapped_column(String(10), nullable=False)
    ocr_engine: Mapped[str] = mapped_column(String(20), nullable=False)
    ocr_model_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ai_models.id", ondelete="RESTRICT"))
    ocr_model_ref: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(1000))
    # {key: {value, confidence, ai_value, ai_confidence, grounded, source: ai|human, issue}}
    fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    overall_confidence: Mapped[float | None] = mapped_column(Float)
    # {"low_confidence": [...], "missing_required": [...]}
    review_reasons: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[str] | None] = mapped_column(JSONB)
    approved_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    queued_via: Mapped[str | None] = mapped_column(String(16))  # queue | background
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[int | None] = mapped_column(BigInteger)
    reject_reason: Mapped[str | None] = mapped_column(String(500))
    linked_entity_type: Mapped[str | None] = mapped_column(String(32))
    linked_entity_id: Mapped[int | None] = mapped_column(BigInteger)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    linked_by: Mapped[int | None] = mapped_column(BigInteger)
    request_id: Mapped[str | None] = mapped_column(String(64))
    client_ip: Mapped[str | None] = mapped_column(String(45))


class SysDocExtraction(Base):
    __tablename__ = "sys_doc_extractions"
    __table_args__ = (UniqueConstraint("job_id", "run_no", name="uq_sys_doc_extractions_job_run"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_doc_jobs.id", ondelete="RESTRICT"), nullable=False)
    run_no: Mapped[int] = mapped_column(Integer, nullable=False)
    ocr_engine: Mapped[str] = mapped_column(String(20), nullable=False)
    model_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    # [{page, source, label, text, mean_confidence, words: [{t, c, b: [x, y, w, h]}]}]
    pages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    text_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_output: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ocr_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
