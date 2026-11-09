"""
Phát sự kiện tích hợp (01 §5) – Giai đoạn 8: **ghi vào `sys_audit_logs`** trong cùng giao dịch với thay đổi nghiệp vụ
(commit cùng nhau, rollback cùng nhau – không có sự kiện "ma" hay thay đổi thiếu sự kiện).

Mỗi sự kiện là một dòng nhật ký: `action = "event.<mã>"` (vd. `event.EV-111`), `entity_type`/`entity_id` = đối tượng
của sự kiện, `actor_id`, `after` = phong bì đầy đủ theo 01 §5 (`event_id`, `event_code`, `event_name`,
`schema_version`, `occurred_at`, `mode`, `actor_id`, `data`). Bảng nhật ký chỉ ghi thêm (trigger) – sự kiện không sửa,
không xóa được.

Outbox `sys_events` + bộ phát cho Luồng 2, 4 (01 §4, 07 §1 `outbox.py`, `dispatcher.py`): chưa làm – khi làm, chuyển
`publish` sang ghi `sys_events` (cùng phong bì) và giữ dòng nhật ký.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.timeutil import now_utc, to_utc_iso
from app.shared.audit import service as audit
from app.shared.events.contracts import EventData

ACTION_PREFIX = "event."


def envelope(event: EventData, mode: str, actor_id: int | None) -> dict[str, Any]:
    data = event.model_dump(mode="json", by_alias=True)
    return {
        "event_id": str(uuid.uuid4()),
        "event_code": event.code,
        "event_name": event.name,
        "schema_version": event.schema_version,
        "occurred_at": to_utc_iso(now_utc()),
        "mode": mode,
        "actor_id": actor_id,
        "data": data,
    }


def publish(
    db: Session, event: EventData, entity_type: str, entity_id: int, actor_id: int | None, mode: str = "production"
) -> dict[str, Any]:
    """Ghi sự kiện vào phiên `db` (chưa commit – nơi gọi commit cùng thay đổi nghiệp vụ). Trả phong bì."""
    body = envelope(event, mode, actor_id)
    audit.record(db, ACTION_PREFIX + event.code, entity_type, entity_id, actor_id, after=body)
    return body
