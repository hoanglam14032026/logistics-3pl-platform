"""
Hợp đồng dịch vụ `ops_service_contracts` (02 §3.1, §6.1 – Luồng 1).

Giai đoạn 3 làm phần lõi: khách hàng (+ bản chụp tên, MST), hiệu lực, trạng thái, `portal_enabled`, điều khoản dạng
văn bản. Chưa có (làm theo 02 §3.1 khi tới): phạm vi dịch vụ, SLA, biểu giá, phiên bản, file scan; chế độ `training`
(cột `mode` có sẵn, API chỉ nhận `production`).
"""

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditColumnsMixin, Base


class ContractMode(StrEnum):
    PRODUCTION = "production"
    TRAINING = "training"


class ContractStatus(StrEnum):
    """02 §6.1."""

    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    LIQUIDATING = "liquidating"
    CLOSED = "closed"


def _in(values) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class OpsServiceContract(AuditColumnsMixin, Base):
    __tablename__ = "ops_service_contracts"
    __table_args__ = (
        CheckConstraint(f"mode IN ({_in(ContractMode)})", name="mode_valid"),
        CheckConstraint(f"status IN ({_in(ContractStatus)})", name="status_valid"),
        CheckConstraint("valid_from <= valid_to", name="valid_period"),
        CheckConstraint("payment_due_days IS NULL OR payment_due_days >= 0", name="payment_due_days_non_negative"),
        Index("ix_ops_service_contracts_customer_status", "customer_id", "status"),
        Index("ix_ops_service_contracts_status_valid_to", "status", "valid_to"),
    )

    contract_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    customer_contract_ref: Mapped[str | None] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="production", server_default="production")
    # Tham chiếu danh mục dùng chung md_parties (được phép – N3 chỉ cấm khóa ngoại giữa các luồng)
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("md_parties.id", ondelete="RESTRICT"), nullable=False
    )
    # Bản chụp tại thời điểm lập/sửa nháp (02 §3.1): đổi tên/MST trong danh mục sau đó không làm đổi hợp đồng
    customer_name: Mapped[str] = mapped_column(String(300), nullable=False)
    customer_tax_code: Mapped[str | None] = mapped_column(String(32))
    signed_date: Mapped[date | None] = mapped_column(Date)  # bắt buộc khi kích hoạt
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    liability_terms: Mapped[str | None] = mapped_column(Text)
    payment_terms: Mapped[str | None] = mapped_column(Text)
    advance_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    payment_due_days: Mapped[int | None] = mapped_column(Integer)
    portal_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    status_reason: Mapped[str | None] = mapped_column(String(500))  # lý do tạm dừng (02 §6.1)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by: Mapped[int | None] = mapped_column(BigInteger)
