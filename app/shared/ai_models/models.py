"""
Danh mục model AI local `ai_models` (Giai đoạn 9) – `admin` quản lý; người thực hiện chọn model ở bước nhập chứng từ.

- Thêm model mới = thêm dòng vào bảng này (API `/api/v2/admin/ai-models`), **không sửa mã**. Mã nghiệp vụ không ghi tên
  model nào; job trích xuất lưu `ai_model_id` + ảnh chụp `model_code`, `model_ref` lúc tạo để truy vết.
- `endpoint` NULL = dùng `OLLAMA_URL`. Có giá trị: phải là localhost / mạng nội bộ (app/core/netguard.py) – kiểm khi lưu
  và mỗi lần gọi.
- Không xóa cứng (job cũ tham chiếu – FK RESTRICT): "xóa" = đặt `deleted_at` + tắt; job cũ vẫn giữ `ai_model_id`, job
  mới không chọn được.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, String, Text, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditColumnsMixin, Base

MODEL_CODE_PATTERN = r"^[a-z0-9][a-z0-9._:-]{1,49}$"


class Provider(StrEnum):
    OLLAMA = "ollama"


class ModelType(StrEnum):
    VISION = "vision"  # đọc được ảnh trang + văn bản OCR
    TEXT = "text"  # chỉ văn bản OCR
    OCR = "ocr"  # model OCR chạy qua Ollama – thay Tesseract/PaddleOCR khi người dùng chọn `ocr_model_id`


def _in(values) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class AiModel(AuditColumnsMixin, Base):
    __tablename__ = "ai_models"
    __table_args__ = (
        CheckConstraint(f"provider IN ({_in(Provider)})", name="provider_valid"),
        CheckConstraint(f"model_type IN ({_in(ModelType)})", name="model_type_valid"),
        CheckConstraint(f"code ~ '{MODEL_CODE_PATTERN}'", name="code_format"),
        CheckConstraint("deleted_at IS NULL OR NOT is_active", name="deleted_is_inactive"),
        CheckConstraint("supports_pdf OR supports_image OR supports_docx", name="supports_something"),
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="ollama", server_default="ollama")
    endpoint: Mapped[str | None] = mapped_column(String(500))
    # Tên model trong máy chủ model (vd. `qwen3.8:27b` của Ollama)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    model_type: Mapped[str] = mapped_column(String(10), nullable=False)
    supports_pdf: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    supports_image: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    # docx, xlsx, txt (tài liệu dạng văn bản)
    supports_docx: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    description: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(BigInteger)
    # Bản ghi khởi tạo bởi migration (model mẫu) – chỉ để biết nguồn gốc
    is_seeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
