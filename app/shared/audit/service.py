"""
Ghi nhật ký thao tác (06 §5).

- Ghi trong **cùng giao dịch** với thay đổi nghiệp vụ (`db.add` trước `commit` của service): thay đổi được lưu
  thì nhật ký được lưu, rollback thì cả hai cùng mất – không có thay đổi "không dấu vết".
- `ip`, `request_id` lấy từ ngữ cảnh request (app/core/request_context.py).
- Chỉ ghi trường trong danh sách cho phép (`snapshot(..., fields)`): không có mật khẩu, hash, token, `totp_secret`.
"""

from collections.abc import Iterable
from datetime import date, datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.core.request_context import current_client_ip, current_request_id
from app.core.timeutil import to_utc_iso
from app.shared.audit.models import SysAuditLog

# Trường không bao giờ được ghi vào nhật ký, kể cả khi vô tình có trong danh sách `fields`.
_FORBIDDEN = frozenset({"password", "password_hash", "totp_secret", "token", "token_hash", "refresh_token"})


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return to_utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(v) for v in value]
    return value


def snapshot(obj: Any, fields: Iterable[str]) -> dict[str, Any]:
    """Ảnh chụp các trường cho phép của đối tượng (giá trị JSON được)."""
    return {f: _json_value(getattr(obj, f)) for f in fields if f not in _FORBIDDEN}


def changed(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Chỉ giữ trường có thay đổi – nhật ký ngắn, dễ đọc."""
    keys = [k for k in after if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in keys}, {k: after[k] for k in keys}


def record(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: int | None,
    actor_id: int | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> SysAuditLog:
    for data in (before, after):
        if data and _FORBIDDEN & data.keys():
            raise ValueError(f"Không ghi trường nhạy cảm vào nhật ký: {sorted(_FORBIDDEN & data.keys())}")
    entry = SysAuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        # Chuyển mọi giá trị sang dạng JSON (datetime → ISO 8601 Z…) ở một chỗ, không trông vào nơi gọi.
        before={k: _json_value(v) for k, v in before.items()} if before else None,
        after={k: _json_value(v) for k, v in after.items()} if after else None,
        ip=current_client_ip(),
        request_id=current_request_id(),
    )
    db.add(entry)
    return entry
