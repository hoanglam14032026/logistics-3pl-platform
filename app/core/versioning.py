"""Khóa lạc quan qua HTTP (06 §2.4): `ETag` = `row_version`; client gửi lại trong `If-Match`, lệch → 409."""

from app.core.errors import AppError, ErrorCode


def etag(row_version: int) -> str:
    return f'"{row_version}"'


def parse_if_match(if_match: str | None) -> int | None:
    if if_match is None:
        return None
    value = if_match.strip().removeprefix("W/").strip('"')
    if not value.isdigit():
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            details=[{"field": "header.If-Match", "issue": "invalid", "message": "If-Match phải là ETag của bản ghi"}],
        )
    return int(value)
