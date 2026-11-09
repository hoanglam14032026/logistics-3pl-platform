"""Schema API job trích xuất chứng từ `/api/v2/doc-jobs` và danh mục loại chứng từ `/api/v2/doc-types`.

(Đặt tên `api_schemas.py`: thư mục `schemas/` chứa file JSON schema của từng loại chứng từ.)
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.timeutil import UtcDatetime
from app.shared.docai.models import JobStatus
from app.shared.files.schemas import FileOut


class FieldSpecOut(BaseModel):
    key: str
    label: str
    type: str
    required: bool
    description: str | None
    max_length: int | None
    values: list[str] | None
    items: list["FieldSpecOut"] | None
    form_field: str | None


class DocTypeOut(BaseModel):
    code: str
    version: int
    name: str
    description: str | None
    target: str | None
    fields: list[FieldSpecOut]


class DraftFieldOut(BaseModel):
    key: str
    label: str
    type: str
    required: bool
    form_field: str | None
    value: Any
    confidence: float = Field(description="0–1; giá trị người sửa = 1")
    ai_value: Any
    ai_confidence: float
    grounded: bool | None = Field(description="Giá trị AI có trong văn bản chứng từ không (null: không đối chiếu)")
    source: str = Field(description="ai | human")
    low_confidence: bool
    issue: str | None


class CreateHint(BaseModel):
    method: str
    url: str


class DocJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    doc_type: str
    schema_version: int
    target_type: str | None
    status: JobStatus
    file_id: int
    ai_model_id: int
    model_code: str
    ocr_engine: str
    ocr_model_id: int | None
    attempts: int
    last_error: str | None
    overall_confidence: float | None
    review_reasons: dict[str, list[str]] | None
    warnings: list[str] | None
    queued_via: str | None
    created_at: UtcDatetime
    created_by: int | None
    started_at: UtcDatetime | None
    extracted_at: UtcDatetime | None
    failed_at: UtcDatetime | None
    approved_at: UtcDatetime | None
    approved_by: int | None
    rejected_at: UtcDatetime | None
    rejected_by: int | None
    reject_reason: str | None
    linked_entity_type: str | None
    linked_entity_id: int | None
    row_version: int


class DocJobDetailOut(DocJobOut):
    file: FileOut | None = None
    doc_type_name: str | None = None
    draft: list[DraftFieldOut] = Field(default_factory=list, description="Bản nháp tự điền + độ tin cậy từng trường")
    form: dict[str, Any] = Field(
        default_factory=dict, description="Body tự điền cho API tạo bản ghi đích (không tự lưu – phải duyệt trước)"
    )
    approved_data: dict[str, Any] | None = None
    create_hint: CreateHint | None = Field(None, description="Sau khi duyệt: gọi API này kèm `form` để tạo bản ghi")


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    run_no: int
    ocr_engine: str
    pages: list[dict[str, Any]] = Field(
        description="[{page, source, label, text, mean_confidence, words: [{t, c, b}]}]",
    )
    text_chars: int
    raw_output: str
    result: dict[str, Any]
    ocr_ms: int
    llm_ms: int
    created_at: UtcDatetime


class ReviewIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"fields": {"tax_code": "0101234567", "phone": None}}},
    )

    fields: dict[str, Any] = Field(min_length=1, max_length=200, description="key → giá trị mới (null = xóa giá trị)")


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_fields: list[Annotated[str, StringConstraints(max_length=50)]] = Field(
        default_factory=list,
        max_length=200,
        description="Trường độ tin cậy thấp người duyệt đã kiểm, giữ nguyên giá trị AI (bắt buộc khi needs_review)",
    )


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
