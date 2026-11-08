"""
Định dạng lỗi thống nhất (đặc tả 06 §2.2).

Mọi lỗi 4xx/5xx – kể cả lỗi validation của Pydantic – trả về:
    {"error": {"code": ..., "message": ..., "details": [...], "request_id": ...}}
"""

import logging
from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.middleware import REQUEST_ID_HEADER, get_request_id

logger = logging.getLogger("app.errors")


class ErrorCode(StrEnum):
    """Danh mục mã lỗi cố định (06 §2.2)."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    DUPLICATE = "DUPLICATE"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.VALIDATION_ERROR: "Dữ liệu không hợp lệ",
    ErrorCode.NOT_FOUND: "Không tìm thấy",
    ErrorCode.FORBIDDEN: "Không đủ quyền",
    ErrorCode.UNAUTHENTICATED: "Chưa đăng nhập hoặc phiên đã hết hạn",
    ErrorCode.INVALID_STATE_TRANSITION: "Chuyển trạng thái không hợp lệ",
    ErrorCode.DUPLICATE: "Dữ liệu bị trùng",
    ErrorCode.VERSION_CONFLICT: "Bản ghi đã bị người khác thay đổi",
    ErrorCode.FILE_TOO_LARGE: "File vượt dung lượng cho phép",
    ErrorCode.UNSUPPORTED_FILE_TYPE: "Loại file không được phép",
    ErrorCode.INTERNAL_ERROR: "Lỗi hệ thống",
}

# Mã HTTP → mã lỗi khi nơi phát sinh chỉ ném HTTPException (mã v1, lỗi định tuyến của Starlette).
# 409 có ba nghĩa (06 §2.1); mã mới nên ném AppError với code cụ thể thay vì HTTPException(409).
_STATUS_TO_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.NOT_FOUND,
    409: ErrorCode.DUPLICATE,
    413: ErrorCode.FILE_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_FILE_TYPE,
    422: ErrorCode.VALIDATION_ERROR,
}


class AppError(Exception):
    """Lỗi nghiệp vụ/kỹ thuật có mã cố định; mã mới (v2) ném lỗi này thay cho HTTPException."""

    def __init__(
        self,
        code: ErrorCode,
        status_code: int,
        message: str | None = None,
        details: list[dict] | None = None,
    ) -> None:
        super().__init__(message or DEFAULT_MESSAGES[code])
        self.code = code
        self.status_code = status_code
        self.message = message or DEFAULT_MESSAGES[code]
        self.details = details or []


def _code_for_status(status_code: int) -> ErrorCode:
    if status_code >= 500:
        return ErrorCode.INTERNAL_ERROR
    return _STATUS_TO_CODE.get(status_code, ErrorCode.VALIDATION_ERROR)


def error_response(
    request: Request,
    status_code: int,
    code: ErrorCode,
    message: str,
    details: list[dict] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    body = {"error": {"code": code, "message": message, "details": details or [], "request_id": request_id}}
    all_headers = dict(headers or {})
    if request_id:
        all_headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(status_code=status_code, content=body, headers=all_headers)


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return error_response(request, exc.status_code, exc.code, exc.message, exc.details)


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _code_for_status(exc.status_code)
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else DEFAULT_MESSAGES[code]
    return error_response(request, exc.status_code, code, message, headers=getattr(exc, "headers", None))


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "field": ".".join(str(part) for part in err.get("loc", ())),
            "issue": err.get("type", ""),
            "message": err.get("msg", ""),
        }
        for err in exc.errors()
    ]
    code = ErrorCode.VALIDATION_ERROR
    return error_response(request, 422, code, DEFAULT_MESSAGES[code], details)


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Không trả chi tiết lỗi cho client; ghi log kèm request_id để tra cứu.
    logger.exception("Lỗi không xử lý được (request_id=%s)", get_request_id(request))
    code = ErrorCode.INTERNAL_ERROR
    return error_response(request, 500, code, DEFAULT_MESSAGES[code])


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
