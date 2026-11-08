"""Thời gian (06 §2.4): lưu UTC có múi giờ; không dùng `datetime.utcnow()`."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_utc_iso(value: datetime) -> str:
    """ISO 8601 UTC có hậu tố `Z` (06 §2.4), bất kể múi giờ phiên CSDL."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# Kiểu dùng trong schema đầu ra: `created_at: UtcDatetime`
UtcDatetime = Annotated[datetime, PlainSerializer(to_utc_iso, return_type=str)]
