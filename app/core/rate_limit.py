"""
Giới hạn tần suất gọi API (06 §3) – bộ đếm cửa sổ cố định lưu trong PostgreSQL (`sys_rate_limit_counters`).

- Lưu trong CSDL, không trong bộ nhớ tiến trình: đúng khi chạy nhiều worker uvicorn / nhiều container.
- Mỗi lần gọi: một câu `INSERT … ON CONFLICT DO UPDATE … RETURNING count` (nguyên tử, không cần khóa ứng dụng).
- Chạy trên **kết nối riêng, commit ngay**: lần gọi bị tính kể cả khi giao dịch nghiệp vụ sau đó rollback
  (vd. đăng nhập sai).
- Cửa sổ cố định: sát ranh giới hai cửa sổ có thể lọt tối đa gấp đôi giới hạn trong một khoảng ngắn – chấp nhận được
  cho chống dò mật khẩu; cần chính xác hơn thì đổi sang cửa sổ trượt.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, Engine, Index, Integer, String, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.errors import AppError, ErrorCode
from app.core.timeutil import now_utc

# Bộ đếm cũ hơn mốc này bị xóa khi có lượt gọi mới (bảng không phình).
_RETENTION = timedelta(hours=1)


class SysRateLimitCounter(Base):
    __tablename__ = "sys_rate_limit_counters"
    __table_args__ = (Index("ix_sys_rate_limit_counters_window_start", "window_start"),)

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


@dataclass(frozen=True)
class Hit:
    count: int
    limit: int
    retry_after: int  # giây tới cửa sổ kế tiếp

    @property
    def exceeded(self) -> bool:
        return self.count > self.limit

    @property
    def first_exceeded(self) -> bool:
        """Lần đầu vượt ngưỡng trong cửa sổ – dùng để ghi nhật ký một lần thay vì mỗi request."""
        return self.count == self.limit + 1


def _window_start(at: datetime, window_sec: int) -> datetime:
    epoch = int(at.timestamp())
    return datetime.fromtimestamp(epoch - epoch % window_sec, UTC)


def hit(engine: Engine, scope: str, key: str, limit: int, window_sec: int, at: datetime | None = None) -> Hit:
    at = at or now_utc()
    start = _window_start(at, window_sec)
    stmt = (
        insert(SysRateLimitCounter)
        .values(scope=scope, key=key[:128], window_start=start, count=1)
        .on_conflict_do_update(
            index_elements=["scope", "key", "window_start"],
            set_={"count": SysRateLimitCounter.count + 1},
        )
        .returning(SysRateLimitCounter.count)
    )
    with engine.begin() as conn:
        count = conn.execute(stmt).scalar_one()
        if count == 1:  # dọn bộ đếm cũ khi mở cửa sổ mới (không phải mỗi request – Giai đoạn 7 đếm mọi API)
            conn.execute(delete(SysRateLimitCounter).where(SysRateLimitCounter.window_start < at - _RETENTION))
    retry_after = max(1, math.ceil((start + timedelta(seconds=window_sec) - at).total_seconds()))
    return Hit(count=count, limit=limit, retry_after=retry_after)


def too_many_requests(retry_after: int) -> AppError:
    return AppError(ErrorCode.RATE_LIMITED, 429, headers={"Retry-After": str(retry_after)})
