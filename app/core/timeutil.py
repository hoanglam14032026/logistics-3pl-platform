"""Thời gian (06 §2.4): lưu UTC có múi giờ; không dùng `datetime.utcnow()`."""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Annotated

from pydantic import PlainSerializer


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_utc_iso(value: datetime) -> str:
    """ISO 8601 UTC có hậu tố `Z` (06 §2.4), bất kể múi giờ phiên CSDL."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# Kiểu dùng trong schema đầu ra: `created_at: UtcDatetime`
UtcDatetime = Annotated[datetime, PlainSerializer(to_utc_iso, return_type=str)]


# Giờ Việt Nam cố định UTC+7 (không có giờ mùa hè) – không phụ thuộc gói tzdata trong image Docker.
VN_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")


def today_vn() -> date:
    """Ngày nghiệp vụ hôm nay theo giờ Việt Nam (06 §2.4 – giao diện, ngày hiệu lực theo Asia/Ho_Chi_Minh)."""
    return now_utc().astimezone(VN_TZ).date()
