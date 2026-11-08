"""Schema API đọc nhật ký thao tác."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.timeutil import UtcDatetime


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: UtcDatetime
    actor_id: int | None
    action: str
    entity_type: str
    entity_id: int | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip: str | None
    request_id: str | None
