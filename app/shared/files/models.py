"""Metadata file `sys_files` (01 §4, 06 §2.6). Nội dung nằm trên kho lưu trữ; CSDL chỉ lưu `storage_key` tương đối."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.timeutil import now_utc


class SysFile(Base):
    __tablename__ = "sys_files"
    __table_args__ = (Index("ix_sys_files_owner", "owner_type", "owner_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # <module>/<yyyy>/<mm>/<uuid4> – không chứa tên người dùng nhập (06 §2.6)
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)  # xác định theo nội dung, không theo đuôi file
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    # Thực thể sở hữu – quyết định ai được tải (06 §2.6: kiểm quyền theo thực thể sở hữu file)
    owner_type: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger)
