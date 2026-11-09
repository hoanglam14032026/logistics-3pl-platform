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

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint, Uuid, func
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


class ConsumptionStatus(StrEnum):
    PROCESSED = "processed"  # đã xử lý xong – không xử lý lại (N5)
    SKIPPED = "skipped"  # luồng nhận không có xử lý cho mã sự kiện này
    FAILED = "failed"  # lỗi, còn lượt thử lại
    DEAD = "dead"  # hết lượt thử lại – ghi log + nhật ký `event.consume_failed`; xử lý lại: CLI replay --dead


class SysEventConsumption(Base):
    """Vết xử lý của luồng nhận (01 §4 – Giai đoạn 11): mỗi (luồng nhận, `event_id`) một dòng, ghi **cùng giao dịch**
    với thay đổi của luồng nhận – sự kiện đã `processed` thì không xử lý lại (idempotent theo `event_id`, N5)."""

    __tablename__ = "sys_event_consumptions"
    __table_args__ = (
        UniqueConstraint("consumer", "event_id", name="uq_sys_event_consumptions_consumer_event"),
        CheckConstraint(f"status IN ({_in(ConsumptionStatus)})", name="status_valid"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_sys_event_consumptions_status", "consumer", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    consumer: Mapped[str] = mapped_column(String(32), nullable=False)  # ops | control | assessment …
    event_id: Mapped[Any] = mapped_column(Uuid, nullable=False)
    event_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_error: Mapped[str | None] = mapped_column(String(1000))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
