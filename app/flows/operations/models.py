"""
Hợp đồng dịch vụ `ops_service_contracts` (02 §3.1, §6.1 – Luồng 1).

Giai đoạn 3: khách hàng (+ bản chụp tên, MST), hiệu lực, trạng thái, `portal_enabled`, điều khoản dạng văn bản.
Giai đoạn 4: phạm vi dịch vụ, SLA, biểu giá theo `contract_version`; bản chụp từng phiên bản; file đính kèm; thanh lý,
đóng. Chưa có: chế độ `training` (cột `mode` có sẵn, API chỉ nhận `production`).
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditColumnsMixin, Base
from app.core.timeutil import now_utc


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


class ServiceCode(StrEnum):
    """02 §3.1 – phạm vi dịch vụ (danh mục mở rộng được: thêm giá trị ở đây, không có CHECK trong CSDL)."""

    CUSTOMS_CLEARANCE = "CUSTOMS_CLEARANCE"
    SEA_FREIGHT = "SEA_FREIGHT"
    AIR_FREIGHT = "AIR_FREIGHT"
    ROAD_TRANSPORT = "ROAD_TRANSPORT"
    WAREHOUSING = "WAREHOUSING"
    DISTRIBUTION = "DISTRIBUTION"
    CARGO_INSURANCE_ARRANGEMENT = "CARGO_INSURANCE_ARRANGEMENT"
    LICENSE_APPLICATION = "LICENSE_APPLICATION"


class MeasureLevel(StrEnum):
    PER_SHIPMENT = "per_shipment"
    PER_PERIOD = "per_period"


class RateBasis(StrEnum):
    """02 §3.8."""

    PER_SHIPMENT = "per_shipment"
    PER_CONTAINER_20 = "per_container_20"
    PER_CONTAINER_40 = "per_container_40"
    PER_KG = "per_kg"
    PER_CBM = "per_cbm"
    PER_DECLARATION = "per_declaration"
    PER_PALLET_DAY = "per_pallet_day"
    PER_TRIP = "per_trip"
    LUMP_SUM = "lump_sum"


class AttachmentKind(StrEnum):
    SIGNED_CONTRACT = "signed_contract"  # bản scan hợp đồng đã ký – điều kiện kích hoạt
    AMENDMENT = "amendment"  # bản scan phụ lục đã ký – căn cứ tạo phiên bản mới
    LIQUIDATION_MINUTES = "liquidation_minutes"  # bản scan biên bản thanh lý đã ký – điều kiện đóng
    OTHER = "other"


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
    # 02 §3.1: bắt đầu 1; tăng khi có phiên bản mới (phụ lục). Bảng con gắn theo (contract_id, contract_version).
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    liquidation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[int | None] = mapped_column(BigInteger)


def _contract_fk() -> Mapped[int]:
    return mapped_column(BigInteger, ForeignKey("ops_service_contracts.id", ondelete="RESTRICT"), nullable=False)


class OpsContractScope(Base):
    """Phạm vi dịch vụ theo phiên bản (02 §3.1 `ops_contract_scopes`)."""

    __tablename__ = "ops_contract_scopes"
    __table_args__ = (UniqueConstraint("contract_id", "contract_version", "service_code", name="service_per_version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = _contract_fk()
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    service_code: Mapped[str] = mapped_column(String(40), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class OpsContractSla(Base):
    """Cam kết SLA theo phiên bản (02 §3.1 `ops_contract_sla`)."""

    __tablename__ = "ops_contract_sla"
    __table_args__ = (
        UniqueConstraint("contract_id", "contract_version", "sla_code", name="sla_per_version"),
        CheckConstraint(f"measure_level IN ({_in(MeasureLevel)})", name="measure_level_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = _contract_fk()
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    sla_code: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    target_value: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    kpi_code: Mapped[str | None] = mapped_column(String(40))  # danh mục KPI Luồng 4 – tham chiếu mã, không FK (N3)
    measure_level: Mapped[str] = mapped_column(String(16), nullable=False)
    penalty_rule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class OpsContractRate(Base):
    """Biểu giá theo phiên bản (02 §3.8 `ops_contract_rates`). Tiền NUMERIC(18,2) + mã tiền tệ (06 §2.4)."""

    __tablename__ = "ops_contract_rates"
    __table_args__ = (
        UniqueConstraint("contract_id", "contract_version", "rate_code", name="rate_per_version"),
        CheckConstraint(f"basis IN ({_in(RateBasis)})", name="basis_valid"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("min_charge IS NULL OR min_charge >= 0", name="min_charge_non_negative"),
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 100", name="tax_rate_percent"),
        CheckConstraint("valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to", name="valid_period"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = _contract_fk()
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_code: Mapped[str] = mapped_column(String(40), nullable=False)
    service_code: Mapped[str] = mapped_column(String(40), nullable=False)
    charge_name: Mapped[str] = mapped_column(String(200), nullable=False)
    basis: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    min_charge: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)  # %, do người quản lý khai
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class OpsContractAttachment(Base):
    """File đính kèm hợp đồng (bản scan hợp đồng, phụ lục, biên bản thanh lý…) – nội dung ở `sys_files`."""

    __tablename__ = "ops_contract_attachments"
    __table_args__ = (CheckConstraint(f"kind IN ({_in(AttachmentKind)})", name="kind_valid"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = _contract_fk()
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_files.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    # Khách hàng tải được qua cổng (PRT-01) – mặc định chỉ bản đã ký
    customer_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger)


class OpsContractVersion(Base):
    """Bản chụp từng phiên bản hợp đồng (02 §3.1 `ops_contract_versions`): điều khoản + phạm vi + SLA + biểu giá."""

    __tablename__ = "ops_contract_versions"
    __table_args__ = (UniqueConstraint("contract_id", "contract_version", name="one_snapshot_per_version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = _contract_fk()
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    amendment_ref: Mapped[str | None] = mapped_column(String(30))  # vd. PL-01 – tới khi có ops_contract_amendments
    reason: Mapped[str | None] = mapped_column(String(500))
    attachment_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ops_contract_attachments.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger)
