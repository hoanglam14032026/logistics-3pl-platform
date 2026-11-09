"""Schema API danh mục model AI: quản trị `/api/v2/admin/ai-models` và danh sách chọn `/api/v2/ai-models/active`."""

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

from app.core.timeutil import UtcDatetime
from app.shared.ai_models.models import MODEL_CODE_PATTERN, ModelType, Provider


def _strip_lower(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


def _blank_none(value: object) -> object:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


Code = Annotated[str, BeforeValidator(_strip_lower), StringConstraints(pattern=MODEL_CODE_PATTERN)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ModelRef = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:/-]+$")
]
Endpoint = Annotated[
    Annotated[str, StringConstraints(max_length=500)] | None,
    BeforeValidator(_blank_none),
]
Description = Annotated[
    Annotated[str, StringConstraints(max_length=4000)] | None,
    BeforeValidator(_blank_none),
]


class AiModelCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        protected_namespaces=(),
        json_schema_extra={
            "example": {
                "code": "qwen3-vl-8b",
                "display_name": "Qwen3-VL 8B (nhanh, đọc ảnh)",
                "model_id": "qwen3-vl:8b",
                "model_type": "vision",
                "supports_pdf": True,
                "supports_image": True,
                "supports_docx": True,
                "description": "Model nhỏ cho chứng từ ngắn",
            }
        },
    )

    code: Code
    display_name: DisplayName
    provider: Provider = Provider.OLLAMA
    endpoint: Endpoint = Field(None, description="Bỏ trống = OLLAMA_URL. Chỉ localhost / mạng nội bộ")
    model_id: ModelRef = Field(description="Tên model trong Ollama, vd. qwen3.8:27b")
    model_type: ModelType
    supports_pdf: bool = True
    supports_image: bool = True
    supports_docx: bool = Field(True, description="docx, xlsx, txt")
    is_active: bool = True
    description: Description = None

    @model_validator(mode="after")
    def _capability(self):
        if not (self.supports_pdf or self.supports_image or self.supports_docx):
            raise ValueError("Model phải hỗ trợ ít nhất một loại đầu vào (pdf, ảnh, docx)")
        return self


class AiModelUpdate(BaseModel):
    """PATCH (JSON Merge Patch). `code` không đổi (job cũ ghi lại mã). Bật/tắt qua `:activate` / `:deactivate`."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    display_name: DisplayName | None = None
    endpoint: Endpoint = None
    model_id: ModelRef | None = None
    model_type: ModelType | None = None
    supports_pdf: bool | None = None
    supports_image: bool | None = None
    supports_docx: bool | None = None
    description: Description = None

    @model_validator(mode="after")
    def _not_null(self):
        for name in ("display_name", "model_id", "model_type", "supports_pdf", "supports_image", "supports_docx"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} không được null")
        return self


class AiModelAdminOut(BaseModel):
    """Đầy đủ (chỉ admin) – gồm `endpoint`, tên model trong Ollama."""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    code: str
    display_name: str
    provider: str
    endpoint: str | None
    effective_endpoint: str
    model_id: str
    model_type: ModelType
    supports_pdf: bool
    supports_image: bool
    supports_docx: bool
    is_active: bool
    description: str | None
    is_seeded: bool
    deleted_at: UtcDatetime | None
    created_at: UtcDatetime
    created_by: int | None
    updated_at: UtcDatetime
    row_version: int


class AiModelOptionOut(BaseModel):
    """Danh sách chọn ở bước công việc: không có endpoint, provider, tên model trong máy chủ."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    display_name: str
    model_type: ModelType
    supports_pdf: bool
    supports_image: bool
    supports_docx: bool
    description: str | None
    suggested: bool = Field(False, description="Gợi ý hiển thị (AI_MODEL_DEFAULT) – vẫn phải chọn và gửi model_id")


class AiModelProbeOut(BaseModel):
    reachable: bool
    model_available: bool
    message: str
