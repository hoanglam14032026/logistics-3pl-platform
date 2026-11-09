"""
Phát sự kiện tích hợp (01 §5).

- Giai đoạn 10: mỗi sự kiện ghi vào **outbox `sys_events`** trong cùng giao dịch với thay đổi nghiệp vụ (commit cùng
  nhau, rollback cùng nhau – không có sự kiện "ma" hay thay đổi thiếu sự kiện); vẫn ghi thêm một dòng nhật ký
  `sys_audit_logs` (`action = "event.<mã>"`, `after` = phong bì) như Giai đoạn 8.
- Sau khi commit thành công: xếp tác vụ gửi tiếp (app/shared/events/outbox.py) – chỉ mang id dòng outbox.

Phong bì 01 §5: `event_id`, `event_code`, `event_name`, `schema_version`, `occurred_at`, `mode`, `actor_id`, `data`.
"""

import uuid
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.core.request_context import current_client_ip, current_request_id
from app.core.timeutil import now_utc, to_utc_iso
from app.shared.audit import service as audit
from app.shared.audit.models import SysAuditLog
from app.shared.events.contracts import EventData
from app.shared.events.models import EventStatus, SysEvent

ACTION_PREFIX = "event."
PENDING_KEY = "outbox_pending"


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


def _row(event: EventData, body: dict, entity_type: str, entity_id: int, actor_id: int | None, mode: str) -> dict:
    return {
        "event_id": uuid.UUID(body["event_id"]),
        "event_code": event.code,
        "event_name": event.name,
        "schema_version": event.schema_version,
        "mode": mode,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "actor_id": actor_id,
        "occurred_at": now_utc(),
        "payload": body,
        "consumers": list(event.consumers),
        "deliveries": {},
        "status": EventStatus.PENDING.value,
    }


def publish(
    db: Session, event: EventData, entity_type: str, entity_id: int, actor_id: int | None, mode: str = "production"
) -> dict[str, Any]:
    """Ghi sự kiện vào phiên `db` (chưa commit – nơi gọi commit cùng thay đổi nghiệp vụ). Trả phong bì."""
    body = envelope(event, mode, actor_id)
    row = SysEvent(**_row(event, body, entity_type, entity_id, actor_id, mode))
    db.add(row)
    db.info.setdefault(PENDING_KEY, []).append(row)
    audit.record(db, ACTION_PREFIX + event.code, entity_type, entity_id, actor_id, after=body)
    return body


def publish_in_flush(
    session: Session, event: EventData, entity_type: str, entity_id: int, actor_id: int | None, mode: str = "production"
) -> dict[str, Any]:
    """Như `publish` nhưng dùng trong sự kiện flush của SQLAlchemy (không `add` đối tượng được): câu lệnh Core trên
    kết nối của phiên – vẫn cùng giao dịch."""
    body = envelope(event, mode, actor_id)
    conn = session.connection()
    new_id = conn.execute(
        insert(SysEvent.__table__)
        .values(**_row(event, body, entity_type, entity_id, actor_id, mode))
        .returning(SysEvent.__table__.c.id)
    ).scalar_one()
    session.info.setdefault(PENDING_KEY, []).append(new_id)
    conn.execute(
        insert(SysAuditLog.__table__).values(
            at=now_utc(),
            actor_id=actor_id,
            action=ACTION_PREFIX + event.code,
            entity_type=entity_type,
            entity_id=entity_id,
            after=body,
            ip=current_client_ip(),
            request_id=current_request_id(),
        )
    )
    return body
