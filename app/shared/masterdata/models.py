"""
Danh mục khách hàng, đối tác `md_parties` (01 §4; 06 §2.4, §2.5; Giai đoạn 1).

Chủ ghi: `manager`, `admin` (06 §4). Không xóa cứng – lưu trữ (`archived`). Tài khoản `customer` gắn một party loại
`customer` qua `sys_users.party_id` (02 §9.2).
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class TemplateStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"


class MdProcessTemplate(AuditColumnsMixin, Base):
    """Quy trình nghiệp vụ mẫu (02 §4) – Giai đoạn 11: dùng cho Luồng 2 sinh các bước của lô (nhận EV-111) và phân
    công. Seed `QT-NK-SEA-FCL` v1, `QT-VC-NOIDIA` v1 (migration 0012). Mỗi mã một phiên bản `approved` một lúc."""

    __tablename__ = "md_process_templates"
    __table_args__ = (
        UniqueConstraint("process_code", "version", name="uq_md_process_templates_code_version"),
        CheckConstraint(f"status IN ({_in(TemplateStatus)})", name="status_valid"),
        CheckConstraint("direction IN ('import', 'export', 'domestic')", name="direction_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_md_process_templates_one_approved",
            "process_code",
            unique=True,
            postgresql_where=text("status = 'approved'"),
        ),
    )

    process_code: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    service_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    transport_modes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MdProcessStep(Base):
    """Bước của quy trình mẫu (02 §4 `md_process_steps`). `required_skill` (Giai đoạn 11): năng lực cần để làm bước –
    mã dịch vụ (CUSTOMS_CLEARANCE, ROAD_TRANSPORT…) hoặc DOCS (chứng từ); dùng khi phân công tự động."""

    __tablename__ = "md_process_steps"
    __table_args__ = (
        UniqueConstraint("template_id", "step_code", name="uq_md_process_steps_template_step"),
        UniqueConstraint("template_id", "seq", name="uq_md_process_steps_template_seq"),
        CheckConstraint("standard_duration_hours >= 0", name="duration_non_negative"),
        CheckConstraint("is_checkpoint = (checkpoint_type IS NOT NULL)", name="checkpoint_type_matches"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("md_process_templates.id", ondelete="RESTRICT"), nullable=False
    )
    step_code: Mapped[str] = mapped_column(String(20), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    responsible_role: Mapped[str] = mapped_column(String(32), nullable=False, default="ops_staff")
    required_skill: Mapped[str | None] = mapped_column(String(40))
    standard_duration_hours: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    depends_on: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    is_checkpoint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    checkpoint_type: Mapped[str | None] = mapped_column(String(40))
