"""Nhật ký thao tác `sys_audit_logs` (06 §5). Chỉ thêm: trigger CSDL chặn UPDATE, DELETE (migration 0004)."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.timeutil import now_utc


class SysAuditLog(Base):
    __tablename__ = "sys_audit_logs"
    __table_args__ = (
        Index("ix_sys_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_sys_audit_logs_actor_at", "actor_id", "at"),
        Index("ix_sys_audit_logs_action_at", "action", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    # NULL: hệ thống (khóa tạm tự động) hoặc người chưa đăng nhập (đăng nhập thất bại). Không FK: nhật ký độc lập.
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(45))
    request_id: Mapped[str | None] = mapped_column(String(64))
