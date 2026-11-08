"""
Sinh mã định danh nghiệp vụ theo năm (06 §2.4): `HD-2026-0001`, `TR-HD-2026-0001`, sau này `PT-2026-00001`…

Bộ đếm `sys_doc_sequences` (prefix, year) → last_value, tăng bằng `INSERT … ON CONFLICT DO UPDATE … RETURNING` trong
**cùng giao dịch** với bản ghi nghiệp vụ: dòng bộ đếm bị khóa tới khi commit → hai giao dịch song song không lấy trùng
số; giao dịch rollback thì số cũng rollback → không bị nhảy số.
"""

from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.db import Base


class SysDocSequence(Base):
    __tablename__ = "sys_doc_sequences"

    prefix: Mapped[str] = mapped_column(String(16), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False)


def next_number(db: Session, prefix: str, year: int, width: int = 4) -> str:
    stmt = (
        insert(SysDocSequence)
        .values(prefix=prefix, year=year, last_value=1)
        .on_conflict_do_update(index_elements=["prefix", "year"], set_={"last_value": SysDocSequence.last_value + 1})
        .returning(SysDocSequence.last_value)
    )
    value = db.execute(stmt).scalar_one()
    return f"{prefix}-{year}-{value:0{width}d}"
