"""Công tắc AI (bản demo): AI_ENABLED=false → mọi endpoint AI/DocAI trả 503, không gọi máy chủ model."""

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode

AI_DISABLED_MESSAGE = "Bản demo tắt AI, vui lòng nhập tay"


def require_ai_enabled() -> None:
    """Dependency gắn ở mức router AI/DocAI; chạy trước mọi xử lý nên không có lời gọi mạng nào."""
    if not get_settings().ai_enabled:
        raise AppError(ErrorCode.INTERNAL_ERROR, 503, AI_DISABLED_MESSAGE)
