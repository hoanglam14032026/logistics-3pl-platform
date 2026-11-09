"""
Danh mục khách hàng, đối tác `md_parties` (01 §4; 06 §2.4, §2.5; Giai đoạn 1).

Chủ ghi: `manager`, `admin` (06 §4). Không xóa cứng – lưu trữ (`archived`). Tài khoản `customer` gắn một party loại
`customer` qua `sys_users.party_id` (02 §9.2).
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditColumnsMixin, Base


class PartyType(StrEnum):
    """01 §4: khách hàng (chủ hàng), người bán/người mua nước ngoài, hãng tàu, hãng bay, đại lý, nhà vận tải, kho."""

    CUSTOMER = "customer"
    FOREIGN_SELLER = "foreign_seller"
    FOREIGN_BUYER = "foreign_buyer"
    SHIPPING_LINE = "shipping_line"
    AIRLINE = "airline"
    AGENT = "agent"
    CARRIER = "carrier"
    WAREHOUSE = "warehouse"


class PartyStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


def _in(values) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class MdParty(AuditColumnsMixin, Base):
    __tablename__ = "md_parties"
    __table_args__ = (
        CheckConstraint(f"party_type IN ({_in(PartyType)})", name="party_type_valid"),
        CheckConstraint(f"status IN ({_in(PartyStatus)})", name="status_valid"),
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code_iso2"),
        CheckConstraint("(status = 'archived') = (archived_at IS NOT NULL)", name="archived_at_matches_status"),
    )

    party_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    # Mã số thuế: VN 10 hoặc 13 số (`0101234567-001`); nước ngoài: mã do nước sở tại cấp. Duy nhất (NULL không tính).
    tax_code: Mapped[str | None] = mapped_column(String(32), unique=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="VN", server_default="VN")
    address: Mapped[str | None] = mapped_column(String(500))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(254))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PartyStatus.ACTIVE.value, server_default=PartyStatus.ACTIVE.value
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[int | None] = mapped_column(BigInteger)
    # Job trích xuất chứng từ AI đã duyệt dùng tạo bản ghi (Giai đoạn 9) – NULL: nhập tay. Một job → tối đa một bản ghi
    source_doc_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_doc_jobs.id", ondelete="RESTRICT"), unique=True
    )
