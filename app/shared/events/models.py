"""
Outbox sự kiện tích hợp `sys_events` (01 §4, N5, N6 – Giai đoạn 10).

Mỗi sự kiện (EV-101/111/112/142, `doc.*`…) là một dòng ghi **cùng giao dịch** với thay đổi nghiệp vụ (commit cùng nhau,
rollback cùng nhau). Bộ phát (app/shared/events/outbox.py) gửi tiếp cho từng luồng nhận (`consumers`) qua rq + Redis;
trạng thái gửi theo từng luồng ở `deliveries`. Nội dung sự kiện (`event_id`, mã, `payload`) không sửa, không xóa được
(trigger) – chỉ trạng thái gửi thay đổi.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.timeutil import now_utc


class EventStatus(StrEnum):
    PENDING = "pending"  # còn luồng nhận chưa gửi được (lỗi tạm thời, luồng nhận đang tắt, chưa có hàng đợi)
    SENT = "sent"  # đã gửi cho mọi luồng nhận
    FAILED = "failed"  # hết lượt thử lại – quản trị gửi lại: python -m app.shared.events.cli dispatch --failed


def _in(values) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class SysEvent(Base):
    __tablename__ = "sys_events"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(EventStatus)})", name="status_valid"),
        CheckConstraint("(status = 'sent') = (dispatched_at IS NOT NULL)", name="sent_has_dispatched_at"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_sys_events_status_id", "status", "id"),
        Index("ix_sys_events_entity", "entity_type", "entity_id"),
        Index("ix_sys_events_code_occurred_at", "event_code", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[Any] = mapped_column(Uuid, nullable=False, unique=True)
    event_code: Mapped[str] = mapped_column(String(32), nullable=False)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Phong bì đầy đủ 01 §5: event_id, event_code, event_name, schema_version, occurred_at, mode, actor_id, data
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    consumers: Mapped[list[str]] = mapped_column(JSONB, nullable=False)  # luồng nhận: control (2), assessment (4)
    # {consumer: {"status": "sent", "at": ISO, "stream_id": "…"} | {"status": "pending", "error": "…"}}
    deliveries: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(1000))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
