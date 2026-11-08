"""
CSDL dùng chung (07 §1): engine, session, `Base` cho model v2, dependency `get_db`.

Schema do Alembic quản lý (migrations/), không dùng `create_all`. Mã v1 (app/legacy_v1) dùng chung `engine`
và `SessionLocal` này nhưng giữ `Base` riêng (metadata v1 không có quy ước tên ràng buộc).
"""

from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, MetaData, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, declared_attr, mapped_column, sessionmaker

from app.core.config import get_settings
from app.core.timeutil import now_utc

# Quy ước tên ràng buộc cố định: autogenerate của Alembic sinh tên ổn định giữa các môi trường.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AuditColumnsMixin:
    """Cột chung của bảng nghiệp vụ (06 §2.4); `row_version` tăng mỗi lần UPDATE (khóa lạc quan, `If-Match`)."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc, server_default=func.now()
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    @declared_attr.directive
    def __mapper_args__(cls) -> dict:
        # SQLAlchemy tự tăng row_version mỗi lần UPDATE và báo StaleDataError nếu bản ghi đã bị đổi.
        return {"version_id_col": cls.__table__.c.row_version}


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dependency cấp phiên CSDL cho mỗi request; lỗi thì rollback, luôn đóng phiên."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
