"""
Giới hạn tần suất mọi API và tải file theo tài khoản (Giai đoạn 7; 06 §3) – middleware ASGI.

Mỗi request `/api/…` tính vào bộ đếm cửa sổ cố định `RATE_LIMIT_WINDOW_SEC` (`app.core.rate_limit`, PostgreSQL):
- `api_user` (`RATE_LIMIT_API_PER_USER`): request có access token hợp lệ – theo tài khoản (claim `sub`, chữ ký đã kiểm).
- `api_ip` (`RATE_LIMIT_API_PER_IP`): chưa đăng nhập / token không hợp lệ – theo IP.
- `file_upload` (`RATE_LIMIT_FILE_UPLOAD_PER_USER`): thêm cho request `multipart/form-data`.
- `file_download` (`RATE_LIMIT_FILE_DOWNLOAD_PER_USER`): thêm cho `GET /api/v2/files/…`.

Kiểm **trước** khi đọc body: file tải lên bị từ chối không chiếm băng thông, đĩa tạm. Vượt ngưỡng → `429
RATE_LIMITED` + `Retry-After` (cùng định dạng /login – Giai đoạn 2); lần đầu vượt trong cửa sổ ghi nhật ký
`api.rate_limited`. `/api/v2/auth/login`, `/password/forgot` còn có giới hạn riêng chặt hơn (theo IP và tên đăng nhập).

Không áp cho `/health`, `/docs`, request `OPTIONS` (CORS preflight). `RATE_LIMIT_ENABLED=false` tắt hẳn.
"""

import logging

import anyio
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import rate_limit
from app.core.config import Settings, get_settings
from app.core.db import SessionLocal, engine
from app.core.errors import AppError, ErrorCode, error_response
from app.core.security import decode_access_token
from app.shared.audit import service as audit

logger = logging.getLogger("app.rate_limit")

MESSAGE = "Gửi quá nhiều yêu cầu; thử lại sau"


def _user_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() != "bearer" or not token:
                return None
            try:
                return str(decode_access_token(token.strip())["sub"])
            except AppError:
                return None
    return None


def _content_type(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"content-type":
            return value.decode("latin-1").lower()
    return ""


def rules(scope: Scope, settings: Settings) -> list[tuple[str, str, int]]:
    """(scope bộ đếm, khóa, giới hạn) áp cho request."""
    user_id = _user_id(scope)
    client = scope.get("client")
    key = f"user:{user_id}" if user_id else f"ip:{client[0] if client else 'unknown'}"
    if user_id:
        checks = [("api_user", key, settings.rate_limit_api_per_user)]
    else:
        checks = [("api_ip", key, settings.rate_limit_api_per_ip)]
    method, path = scope["method"], scope["path"]
    if method in ("POST", "PUT", "PATCH") and _content_type(scope).startswith("multipart/form-data"):
        checks.append(("file_upload", key, settings.rate_limit_file_upload_per_user))
    if method in ("GET", "HEAD") and path.startswith("/api/v2/files/"):
        checks.append(("file_download", key, settings.rate_limit_file_download_per_user))
    return checks


def _check(checks: list[tuple[str, str, int]], window: int) -> int | None:
    """Đếm mọi bộ đếm; trả số giây chờ nếu vượt ngưỡng, ngược lại None. Chạy trong threadpool (truy cập CSDL)."""
    hits = [(name, key, rate_limit.hit(engine, name, key, limit, window)) for name, key, limit in checks]
    exceeded = [(name, key, h) for name, key, h in hits if h.exceeded]
    if not exceeded:
        return None
    first = [(name, key) for name, key, h in exceeded if h.first_exceeded]
    if first:
        try:
            with SessionLocal() as db:
                for name, key in first:
                    audit.record(db, "api.rate_limited", "rate_limit", None, None, after={"scope": name, "key": key})
                db.commit()
        except Exception:
            logger.exception("Không ghi được nhật ký vượt giới hạn tần suất")
    return max(h.retry_after for _, _, h in exceeded)


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/") or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return
        settings = get_settings()
        if not settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return
        retry_after = await anyio.to_thread.run_sync(_check, rules(scope, settings), settings.rate_limit_window_sec)
        if retry_after is None:
            await self.app(scope, receive, send)
            return
        response = error_response(
            Request(scope), 429, ErrorCode.RATE_LIMITED, MESSAGE, headers={"Retry-After": str(retry_after)}
        )
        await response(scope, receive, send)
