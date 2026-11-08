"""Danh sách, phân trang, sắp xếp (06 §2.3): `page` (mặc định 1), `page_size` (20, tối đa 100), `sort` = `[-]field`."""

from collections.abc import Mapping

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import AppError, ErrorCode

MAX_PAGE_SIZE = 100


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class PageParams:
    def __init__(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
        sort: str | None = Query(None, description="`field` tăng dần, `-field` giảm dần; mặc định `-created_at`"),
    ) -> None:
        self.page = page
        self.page_size = page_size
        self.sort = sort


def order_by_clause(sort: str | None, allowed: Mapping[str, ColumnElement], default: str = "-created_at"):
    """Chuyển `sort` thành ORDER BY; trường không cho phép → 422."""
    value = sort or default
    field = value.removeprefix("-")
    if field not in allowed:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            details=[
                {"field": "query.sort", "issue": "not_allowed", "message": f"Chỉ sắp xếp theo: {', '.join(allowed)}"}
            ],
        )
    column = allowed[field]
    return column.desc() if value.startswith("-") else column.asc()


def paginate(db: Session, stmt: Select, params: PageParams) -> tuple[list, int]:
    """Chạy truy vấn có phân trang; trả (bản ghi của trang, tổng số bản ghi khớp điều kiện)."""
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.scalars(stmt.offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    return list(rows), total
