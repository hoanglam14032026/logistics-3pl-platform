"""
Hợp đồng dịch vụ `ops_service_contracts` (02 §3.1, §6.1 – Luồng 1).

Giai đoạn 3: khách hàng (+ bản chụp tên, MST), hiệu lực, trạng thái, `portal_enabled`, điều khoản dạng văn bản.
Giai đoạn 4: phạm vi dịch vụ, SLA, biểu giá theo `contract_version`; bản chụp từng phiên bản; file đính kèm; thanh lý,
đóng. Chưa có: chế độ `training` (cột `mode` có sẵn, API chỉ nhận `production`).
Giai đoạn 8: lô hàng `ops_shipments`, lịch sử trạng thái, biên bản giao hàng.
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
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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
    # Job trích xuất chứng từ AI đã duyệt dùng tạo bản ghi (Giai đoạn 9) – NULL: nhập tay. Một job → tối đa một bản ghi
    source_doc_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_doc_jobs.id", ondelete="RESTRICT"), unique=True
    )


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


# --- Lô hàng / vận đơn (02 §3.2, §6.2 – Giai đoạn 8) ----------------------------------------------------------------


class ShipmentStatus(StrEnum):
    """02 §6.2: draft → docs_collecting → docs_verified → in_execution → delivered → closed; cancelled từ mọi trạng
    thái trước delivered (có lý do)."""

    DRAFT = "draft"
    DOCS_COLLECTING = "docs_collecting"
    DOCS_VERIFIED = "docs_verified"
    IN_EXECUTION = "in_execution"
    DELIVERED = "delivered"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class Direction(StrEnum):
    IMPORT = "import"
    EXPORT = "export"
    DOMESTIC = "domestic"


class TransportMode(StrEnum):
    SEA_FCL = "sea_fcl"
    SEA_LCL = "sea_lcl"
    AIR = "air"
    ROAD = "road"
    RAIL = "rail"
    MULTIMODAL = "multimodal"


class Priority(StrEnum):
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


# Incoterms 2020 (02 §3.2 → md_incoterms – tới khi có danh mục, kiểm bằng danh sách cố định)
INCOTERMS_2020 = ("EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF")
TERMINAL_SHIPMENT_STATUSES = (ShipmentStatus.CLOSED.value, ShipmentStatus.CANCELLED.value)


def _party_fk(nullable: bool = False) -> Mapped:
    return mapped_column(BigInteger, ForeignKey("md_parties.id", ondelete="RESTRICT"), nullable=nullable)


class OpsShipment(AuditColumnsMixin, Base):
    """Lô hàng (02 §3.2 `ops_shipments`). Thuộc hợp đồng `active` lúc tạo; giữ `contract_version` lúc tạo (02 §3.7).

    Chưa có (làm cùng các phần tương ứng của 02): quy trình mẫu (`process_code`, `process_version`), `md_locations`
    (điểm đi/đến tạm là văn bản), `md_incoterms`, yêu cầu dịch vụ (`service_request_id`), bước `ops_tasks`."""

    __tablename__ = "ops_shipments"
    __table_args__ = (
        CheckConstraint(f"mode IN ({_in(ContractMode)})", name="mode_valid"),
        CheckConstraint(f"status IN ({_in(ShipmentStatus)})", name="status_valid"),
        CheckConstraint(f"direction IN ({_in(Direction)})", name="direction_valid"),
        CheckConstraint(f"transport_mode IN ({_in(TransportMode)})", name="transport_mode_valid"),
        CheckConstraint(f"priority IN ({_in(Priority)})", name="priority_valid"),
        CheckConstraint(
            "incoterm IS NULL OR incoterm IN (" + ", ".join(f"'{t}'" for t in INCOTERMS_2020) + ")",
            name="incoterm_valid",
        ),
        # 02 §3.2: Incoterms bắt buộc khi nhập/xuất khẩu
        CheckConstraint("direction = 'domestic' OR incoterm IS NOT NULL", name="incoterm_required_cross_border"),
        CheckConstraint("etd IS NULL OR eta IS NULL OR etd <= eta", name="etd_before_eta"),
        CheckConstraint("status <> 'cancelled' OR status_reason IS NOT NULL", name="cancel_has_reason"),
        Index("ix_ops_shipments_contract_status", "contract_id", "status"),
        Index("ix_ops_shipments_customer_status", "customer_id", "status"),
        Index("ix_ops_shipments_assignee_status", "assignee_id", "status"),
    )

    shipment_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    contract_id: Mapped[int] = _contract_fk()
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # kế thừa từ hợp đồng
    customer_id: Mapped[int] = _party_fk()  # = khách hàng của hợp đồng (lọc cổng khách hàng – BR-111)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    transport_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    incoterm: Mapped[str | None] = mapped_column(String(3))
    incoterm_place: Mapped[str | None] = mapped_column(String(200))
    shipper_id: Mapped[int] = _party_fk()
    shipper_name: Mapped[str] = mapped_column(String(300), nullable=False)
    consignee_id: Mapped[int] = _party_fk()
    consignee_name: Mapped[str] = mapped_column(String(300), nullable=False)
    notify_party_id: Mapped[int | None] = _party_fk(nullable=True)
    notify_party_name: Mapped[str | None] = mapped_column(String(300))
    origin_location: Mapped[str] = mapped_column(String(200), nullable=False)  # Giai đoạn 17: bản chụp "mã – tên"
    destination_location: Mapped[str] = mapped_column(String(200), nullable=False)
    # Giai đoạn 17: điểm đi / đến theo `md_locations` – bắt buộc với lô tạo mới (trigger khi INSERT); lô cũ có thể NULL
    origin_location_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("md_locations.id", ondelete="RESTRICT")
    )
    destination_location_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("md_locations.id", ondelete="RESTRICT")
    )
    cargo_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    etd: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    required_delivery_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # hạn SLA
    claims_preferential_tariff: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal", server_default="normal")
    assignee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    status_reason: Mapped[str | None] = mapped_column(String(500))  # lý do hủy
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Job trích xuất chứng từ AI đã duyệt dùng tạo bản ghi (Giai đoạn 9) – NULL: nhập tay. Một job → tối đa một bản ghi
    source_doc_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_doc_jobs.id", ondelete="RESTRICT"), unique=True
    )


class OpsShipmentStatusHistory(Base):
    """Lịch sử trạng thái lô – theo dõi tiến độ (nhân viên: đủ; khách hàng: mốc + thời điểm, không người/ghi chú)."""

    __tablename__ = "ops_shipment_status_history"
    __table_args__ = (Index("ix_ops_shipment_status_history_shipment", "shipment_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ops_shipments.id", ondelete="RESTRICT"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(20))  # NULL: lúc tạo
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    changed_by: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(500))


class OpsDeliveryRecord(Base):
    """Biên bản giao hàng (02 §3 `ops_delivery_records`) – điều kiện `in_execution → delivered`. Chưa có `pod_file_id`
    (file biên bản/POD – làm cùng chứng từ)."""

    __tablename__ = "ops_delivery_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ops_shipments.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    receiver_name: Mapped[str] = mapped_column(String(200), nullable=False)
    in_full: Mapped[bool] = mapped_column(Boolean, nullable=False)
    damage_free: Mapped[bool] = mapped_column(Boolean, nullable=False)
    remarks: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger)


class OpsCommercialInvoice(AuditColumnsMixin, Base):
    """Hóa đơn thương mại (Giai đoạn 10) – gắn hợp đồng (bắt buộc) và lô hàng (tùy chọn; có lô thì hợp đồng = hợp đồng
    của lô). Tạo tay hoặc từ job trích xuất AI loại `commercial_invoice` **đã duyệt** (`source_doc_job_id`). Không sửa,
    không xóa qua API (trigger chặn xóa; dòng hàng chỉ ghi thêm)."""

    __tablename__ = "ops_commercial_invoices"
    __table_args__ = (
        CheckConstraint("total_amount >= 0", name="total_amount_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        CheckConstraint(
            f"incoterm IS NULL OR incoterm IN ({', '.join(repr(i) for i in INCOTERMS_2020)})", name="incoterm_valid"
        ),
        UniqueConstraint(
            "contract_id", "seller_name", "invoice_no", name="uq_ops_commercial_invoices_contract_seller_no"
        ),
        Index("ix_ops_commercial_invoices_shipment", "shipment_id"),
        Index("ix_ops_commercial_invoices_contract_date", "contract_id", "invoice_date"),
    )

    contract_id: Mapped[int] = _contract_fk()
    shipment_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ops_shipments.id", ondelete="RESTRICT"))
    invoice_no: Mapped[str] = mapped_column(String(100), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    seller_name: Mapped[str] = mapped_column(String(300), nullable=False)
    buyer_name: Mapped[str] = mapped_column(String(300), nullable=False)
    incoterm: Mapped[str | None] = mapped_column(String(3))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000))
    source_doc_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sys_doc_jobs.id", ondelete="RESTRICT"), unique=True
    )


class OpsCommercialInvoiceItem(Base):
    """Dòng hàng của hóa đơn thương mại (Giai đoạn 10) – chỉ ghi thêm."""

    __tablename__ = "ops_commercial_invoice_items"
    __table_args__ = (
        UniqueConstraint("invoice_id", "line_no", name="uq_ops_commercial_invoice_items_line"),
        CheckConstraint("line_no >= 1", name="line_no_positive"),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="quantity_non_negative"),
        CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("amount IS NULL OR amount >= 0", name="amount_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ops_commercial_invoices.id", ondelete="RESTRICT"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    hs_code: Mapped[str | None] = mapped_column(String(12))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    unit: Mapped[str | None] = mapped_column(String(20))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))


class OpsTaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CLOSED = "closed"


class OpsTask(Base):
    """Bước thực hiện của lô ở Luồng 1 (02 `ops_tasks` – Giai đoạn 13): bản đồng bộ của `ctl_shipment_tasks` (Luồng 2 –
    nguồn chuẩn, `revision`). Người phụ trách lô thao tác được ở đây (→ EV-131/132/133 → Luồng 2 xác nhận); thay đổi ở
    Luồng 2 đến qua `ctl.task.synced`. `pending_local`: thay đổi ở Luồng 1 chưa được Luồng 2 xác nhận."""

    __tablename__ = "ops_tasks"
    __table_args__ = (
        UniqueConstraint("shipment_id", "step_code", name="uq_ops_tasks_shipment_step"),
        CheckConstraint(f"status IN ({_in(OpsTaskStatus)})", name="status_valid"),
        CheckConstraint("progress_pct BETWEEN 0 AND 100", name="progress_range"),
        CheckConstraint("revision >= 0", name="revision_non_negative"),
        Index("ix_ops_tasks_assignee_status", "assignee_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ops_shipments.id", ondelete="RESTRICT"), nullable=False
    )
    # Bước tương ứng ở Luồng 2 – tham chiếu qua sự kiện, không FK (N2)
    ctl_task_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    step_code: Mapped[str] = mapped_column(String(20), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    responsible_role: Mapped[str] = mapped_column(String(32), nullable=False)
    depends_on: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_checkpoint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    planned_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_reason: Mapped[str | None] = mapped_column(String(500))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_local: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[int | None] = mapped_column(BigInteger)


# --- Giai đoạn 16: chứng từ lô + kiểm tra chứng từ (BR-103), điểm kiểm soát ------------------------------------------


class DocType(StrEnum):
    """Loại chứng từ của lô (02 §3.3 – danh mục rút gọn; CẦN XÁC NHẬN danh mục đầy đủ)."""

    BILL_OF_LADING = "bill_of_lading"
    AIR_WAYBILL = "air_waybill"
    COMMERCIAL_INVOICE = "commercial_invoice"
    PACKING_LIST = "packing_list"
    CERTIFICATE_OF_ORIGIN = "certificate_of_origin"
    CUSTOMS_DECLARATION = "customs_declaration"
    DELIVERY_ORDER = "delivery_order"
    SALES_CONTRACT = "sales_contract"
    OTHER = "other"


class OpsDocument(Base):
    """Chứng từ của lô (02 §3.3 `ops_documents`). Không xóa; tải lại cùng loại → phiên bản mới, bản cũ `superseded`
    (BR-03). File ở `sys_files` (owner_type `shipment_document`)."""

    __tablename__ = "ops_documents"
    __table_args__ = (
        CheckConstraint(f"doc_type IN ({_in(DocType)})", name="doc_type_valid"),
        CheckConstraint("status IN ('current', 'superseded')", name="status_valid"),
        CheckConstraint("channel IN ('staff', 'portal')", name="channel_valid"),
        CheckConstraint("version_no >= 1", name="version_positive"),
        UniqueConstraint("shipment_id", "doc_type", "version_no", name="uq_ops_documents_version"),
        Index(
            "uq_ops_documents_current",
            "shipment_id",
            "doc_type",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
    )
    bill_image_path: Mapped[str | None] = mapped_column(String, nullable=True)
    ai_parsed: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ops_shipments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_files.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="current", server_default="current")
    channel: Mapped[str] = mapped_column(String(8), nullable=False, default="staff", server_default="staff")  # G17
    note: Mapped[str | None] = mapped_column(String(500))
    uploaded_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )


class DocCheckOutcome(StrEnum):
    OK = "ok"  # đủ, đúng
    MISSING = "missing"  # thiếu
    INVALID = "invalid"  # sai


class OpsDocumentCheck(Base):
    """Lần kiểm tra bộ chứng từ (02 §3.3 `ops_document_checks`) – tạo `pending` khi tải chứng từ lên (chưa có lần
    đang chờ); `ops_staff` phụ trách lô ghi kết quả từng loại chứng từ → `completed` (`passed` / `failed`), EV-122."""

    __tablename__ = "ops_document_checks"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'completed')", name="status_valid"),
        CheckConstraint("result IS NULL OR result IN ('passed', 'failed')", name="result_valid"),
        CheckConstraint("(status = 'completed') = (result IS NOT NULL AND checked_at IS NOT NULL)", name="completed"),
        UniqueConstraint("shipment_id", "run_no", name="uq_ops_document_checks_run"),
        Index(
            "uq_ops_document_checks_pending",
            "shipment_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ops_shipments.id", ondelete="RESTRICT"), nullable=False
    )
    run_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    result: Mapped[str | None] = mapped_column(String(16))
    blocking_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    document_versions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # doc_type → {document_id, version_no}
    note: Mapped[str | None] = mapped_column(String(1000))
    triggered_by_document_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ops_documents.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    checked_by: Mapped[int | None] = mapped_column(BigInteger)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OpsDocumentCheckItem(Base):
    """Kết quả một loại chứng từ trong lần kiểm tra: đủ (`ok`) / thiếu (`missing`) / sai (`invalid` – kèm lý do)."""

    __tablename__ = "ops_document_check_items"
    __table_args__ = (
        CheckConstraint(f"doc_type IN ({_in(DocType)})", name="doc_type_valid"),
        CheckConstraint(f"outcome IN ({_in(DocCheckOutcome)})", name="outcome_valid"),
        CheckConstraint("outcome = 'ok' OR reason IS NOT NULL", name="reason_required"),
        CheckConstraint("(outcome = 'missing') = (document_id IS NULL)", name="document_matches_outcome"),
        CheckConstraint("source IN ('manual', 'auto')", name="source_valid"),
        CheckConstraint("source = 'manual' OR (rule_id IS NOT NULL AND outcome = 'missing')", name="auto_from_rule"),
        UniqueConstraint("check_id", "doc_type", name="uq_ops_document_check_items_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ops_document_checks.id", ondelete="RESTRICT"), nullable=False
    )
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    document_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ops_documents.id", ondelete="RESTRICT"))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    # Giai đoạn 17: `auto` – thiếu chứng từ bắt buộc theo quy tắc `ops_document_requirements` (không do người gõ)
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="manual", server_default="manual")
    rule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ops_document_requirements.id", ondelete="RESTRICT")
    )


class OpsDocumentRequirement(AuditColumnsMixin, Base):
    """Quy tắc kiểm tra tự động cơ bản (Giai đoạn 17): loại chứng từ bắt buộc theo loại hình vận chuyển / hướng.
    `transport_mode` / `direction` NULL = mọi giá trị. Lưu trong CSDL (seed ở migration 0018), `admin` / `manager` thêm,
    bật / tắt qua API – không đặt cứng trong mã."""

    __tablename__ = "ops_document_requirements"
    __table_args__ = (
        CheckConstraint(f"doc_type IN ({_in(DocType)})", name="doc_type_valid"),
        CheckConstraint(
            f"transport_mode IS NULL OR transport_mode IN ({_in(TransportMode)})", name="transport_mode_valid"
        ),
        CheckConstraint(f"direction IS NULL OR direction IN ({_in(Direction)})", name="direction_valid"),
        UniqueConstraint(
            "transport_mode",
            "direction",
            "doc_type",
            name="uq_ops_document_requirements_rule",
            postgresql_nulls_not_distinct=True,
        ),
    )

    transport_mode: Mapped[str | None] = mapped_column(String(16))
    direction: Mapped[str | None] = mapped_column(String(16))
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    note: Mapped[str | None] = mapped_column(String(500))


class CheckpointType(StrEnum):
    CARGO_RECEIPT = "cargo_receipt"  # nhận hàng
    CUSTOMS_CLEARANCE = "customs_clearance"  # thông quan
    WAREHOUSE_RELEASE = "warehouse_release"  # xuất kho
    DELIVERY = "delivery"  # giao hàng


CHECKPOINT_SEQ = {t: i for i, t in enumerate(CheckpointType, start=1)}


class CustomsChannel(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class OpsCheckpoint(Base):
    """Điểm kiểm soát của lô (Giai đoạn 16): nhận hàng, thông quan, xuất kho, giao hàng. `ops_staff` phụ trách lô xác
    nhận đạt / không đạt (ghi thời điểm thực tế); không đạt → xác nhận lại (lần sau). Đã đạt → khóa."""

    __tablename__ = "ops_checkpoints"
    __table_args__ = (
        CheckConstraint(f"checkpoint_type IN ({_in(CheckpointType)})", name="checkpoint_type_valid"),
        CheckConstraint("status IN ('pending', 'passed', 'failed')", name="status_valid"),
        CheckConstraint("(status = 'pending') = (attempts = 0)", name="attempts_match_status"),
        UniqueConstraint("shipment_id", "checkpoint_type", name="uq_ops_checkpoints_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ops_shipments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    checkpoint_type: Mapped[str] = mapped_column(String(24), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    location_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("md_locations.id", ondelete="RESTRICT"))
    planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    actual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # lần xác nhận gần nhất
    evaluated_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger)


class OpsCheckpointEvaluation(Base):
    """Một lần xác nhận điểm kiểm soát – chỉ thêm (trigger). Thông quan đạt: số tờ khai, luồng, thời điểm nộp."""

    __tablename__ = "ops_checkpoint_evaluations"
    __table_args__ = (
        CheckConstraint("result IN ('passed', 'failed')", name="result_valid"),
        CheckConstraint("result = 'passed' OR note IS NOT NULL", name="failed_has_note"),
        CheckConstraint(f"customs_channel IS NULL OR customs_channel IN ({_in(CustomsChannel)})", name="channel_valid"),
        CheckConstraint(
            "customs_submitted_at IS NULL OR customs_submitted_at <= actual_at", name="customs_submitted_before"
        ),
        UniqueConstraint("checkpoint_id", "attempt_no", name="uq_ops_checkpoint_evaluations_attempt"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    checkpoint_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ops_checkpoints.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[str] = mapped_column(String(8), nullable=False)
    actual_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000))
    declaration_no: Mapped[str | None] = mapped_column(String(40))
    customs_channel: Mapped[str | None] = mapped_column(String(8))
    customs_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evaluated_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )


# --- Giai đoạn 19: phụ lục (duyệt 2 người), chấm dứt trước hạn, quyết toán BR-110 ---------------------------------


class AmendmentStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"  # chờ người duyệt thứ nhất
    PENDING_SECOND_APPROVAL = "pending_second_approval"
    EFFECTIVE = "effective"  # duyệt đủ 2 người – đã áp vào hợp đồng (phiên bản mới)
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


OPEN_AMENDMENT_STATUSES = (AmendmentStatus.PENDING_APPROVAL.value, AmendmentStatus.PENDING_SECOND_APPROVAL.value)


class OpsContractAmendment(AuditColumnsMixin, Base):
    """Phụ lục hợp đồng (02 §3.7, rút gọn): thay đổi phạm vi / SLA / biểu giá / thời hạn / điều khoản. Duyệt 2 người
    như hợp đồng gốc (Giai đoạn 10): người thứ 2 khác người lập và khác người duyệt đầu. Duyệt đủ → `effective`:
    `contract_version + 1`, bản chụp phiên bản mới (bản cũ giữ nguyên), EV-103."""

    __tablename__ = "ops_contract_amendments"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(AmendmentStatus)})", name="status_valid"),
        CheckConstraint(
            "second_approved_by IS NULL "
            "OR (second_approved_by <> first_approved_by AND second_approved_by <> created_by)",
            name="four_eyes",
        ),
        CheckConstraint(
            "status <> 'effective' OR (second_approved_by IS NOT NULL AND applied_version IS NOT NULL)",
            name="effective_has_two_approvals",
        ),
        UniqueConstraint("contract_id", "amendment_no", name="uq_ops_contract_amendments_no"),
        Index("ix_ops_contract_amendments_contract_status", "contract_id", "status"),
    )

    contract_id: Mapped[int] = _contract_fk()
    amendment_no: Mapped[str] = mapped_column(String(10), nullable=False)  # PL-01, PL-02…
    base_contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    attachment_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ops_contract_attachments.id"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_approval")
    first_approved_by: Mapped[int | None] = mapped_column(BigInteger)
    first_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    second_approved_by: Mapped[int | None] = mapped_column(BigInteger)
    second_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    applied_version: Mapped[int | None] = mapped_column(Integer)


class TerminationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class OpsContractTermination(Base):
    """Đề xuất chấm dứt trước hạn (02 §3.9.2 – phê duyệt `CONTRACT_TERMINATION`): `manager` đề xuất kèm lý do,
    `business_manager` duyệt / từ chối. Duyệt → lô chưa bắt đầu thực hiện bị hủy, lô đang thực hiện / đã giao phải
    hoàn tất trước khi đóng (BR-110); hợp đồng → `liquidating`."""

    __tablename__ = "ops_contract_terminations"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(TerminationStatus)})", name="status_valid"),
        CheckConstraint("decided_by IS NULL OR decided_by <> requested_by", name="decider_not_requester"),
        CheckConstraint("status = 'pending' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)", name="decided"),
        Index(
            "uq_ops_contract_terminations_pending",
            "contract_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = _contract_fk()
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    requested_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    cancelled_shipment_ids: Mapped[list[int] | None] = mapped_column(JSONB)
    open_shipment_ids: Mapped[list[int] | None] = mapped_column(JSONB)  # còn phải hoàn tất trước khi đóng


class SettlementStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"


class SettlementType(StrEnum):
    FINAL = "final"  # quyết toán thanh lý hợp đồng (Giai đoạn 19)
    PERIODIC_STATEMENT = "periodic_statement"  # bảng kê đối soát định kỳ – SOA theo khách hàng + tháng (Giai đoạn 20)


class StatementStatus(StrEnum):
    """Trạng thái bảng kê đối soát `periodic_statement` (Giai đoạn 20)."""

    DRAFT = "draft"
    SENT = "sent"
    CONFIRMED = "confirmed"  # khóa hóa đơn thương mại của kỳ
    DISPUTED = "disputed"  # không khóa; phát hành lại = bản mới (previous_id), bản này giữ nguyên
    CLOSED = "closed"


LOCKING_STATEMENT_STATUSES = (StatementStatus.CONFIRMED.value, StatementStatus.CLOSED.value)


class OpsContractSettlement(AuditColumnsMixin, Base):
    """Quyết toán hợp đồng `final` (02 §3.9, rút gọn – Giai đoạn 19): tổng hợp hóa đơn thương mại của hợp đồng (đồng
    tiền quyết toán; đồng tiền khác liệt kê riêng, không quy đổi) + bản chụp KPI / mục tiêu từ Luồng 4
    (`assessment.public` – BR-05); giảm trừ SLA, bồi thường, đã thanh toán nhập tay. `manager` lập, `business_manager`
    duyệt / trả lại. Điều kiện lập (BR-110): hợp đồng hết hạn / đang thanh lý, mọi lô đóng / hủy, không còn phụ lục,
    đề xuất chấm dứt đang xử lý. Hợp đồng chỉ đóng khi quyết toán đã duyệt."""

    __tablename__ = "ops_contract_settlements"
    __table_args__ = (
        CheckConstraint(
            f"(settlement_type = 'final' AND status IN ({_in(SettlementStatus)})) OR "
            f"(settlement_type = 'periodic_statement' AND status IN ({_in(StatementStatus)}))",
            name="status_valid",
        ),
        CheckConstraint(f"settlement_type IN ({_in(SettlementType)})", name="type_valid"),
        # final: theo hợp đồng; periodic_statement (SOA): theo khách hàng + tháng, không gắn một hợp đồng
        CheckConstraint(
            "(settlement_type = 'final' AND contract_id IS NOT NULL AND customer_id IS NULL AND opening_balance = 0)"
            " OR (settlement_type = 'periodic_statement' AND contract_id IS NULL AND customer_id IS NOT NULL)",
            name="type_shape",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        CheckConstraint(
            "invoice_total >= 0 AND sla_deduction >= 0 AND compensation >= 0 "
            "AND (paid_amount >= 0 OR settlement_type = 'periodic_statement')",
            name="amounts_valid",
        ),
        CheckConstraint(
            "balance_due = opening_balance + invoice_total - sla_deduction - compensation - paid_amount",
            name="balance_formula",
        ),
        CheckConstraint("sla_deduction = 0 OR deduction_note IS NOT NULL", name="deduction_has_note"),
        CheckConstraint("approved_by IS NULL OR approved_by <> prepared_by", name="approver_not_preparer"),
        CheckConstraint(
            "status <> 'disputed' OR (dispute_reason IS NOT NULL AND responded_at IS NOT NULL)",
            name="dispute_has_reason",
        ),
        CheckConstraint(
            "status NOT IN ('confirmed', 'closed') OR settlement_type = 'final' OR responded_at IS NOT NULL",
            name="confirmed_has_response",
        ),
        CheckConstraint("response_via IS NULL OR response_via IN ('customer', 'staff')", name="response_via_valid"),
        UniqueConstraint("contract_id", "settlement_type", name="uq_ops_contract_settlements_type"),
        # Một bảng kê "đang sống" mỗi khách hàng + tháng; bản `disputed` đứng ngoài để phát hành lại bản mới
        Index(
            "uq_ops_contract_settlements_statement_period",
            "customer_id",
            "period_from",
            unique=True,
            postgresql_where=text("settlement_type = 'periodic_statement' AND status <> 'disputed'"),
        ),
    )

    settlement_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)  # QT-yyyy-nnnn / BK-yyyy-nnnn
    contract_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ops_service_contracts.id", ondelete="RESTRICT")
    )
    settlement_type: Mapped[str] = mapped_column(String(20), nullable=False, default="final", server_default="final")
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    invoice_count: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    other_currency_totals: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sla_deduction: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0, server_default="0")
    deduction_note: Mapped[str | None] = mapped_column(String(1000))
    compensation: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0, server_default="0")
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0, server_default="0")
    balance_due: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    kpi_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # null: Luồng 4 tắt (N6) – D nhập tay
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    prepared_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    # --- Giai đoạn 20: bảng kê đối soát `periodic_statement` (SOA) ---
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("md_parties.id", ondelete="RESTRICT"))
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0, server_default="0")
    statement_lines: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # bản chụp hóa đơn + phiếu của kỳ
    previous_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ops_contract_settlements.id", ondelete="RESTRICT"), unique=True
    )  # bản `disputed` được phát hành lại bằng bản này
    sent_by: Mapped[int | None] = mapped_column(BigInteger)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    customer_confirmed_by: Mapped[int | None] = mapped_column(BigInteger)
    customer_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_by: Mapped[int | None] = mapped_column(BigInteger)  # người bấm xác nhận / khiếu nại
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_via: Mapped[str | None] = mapped_column(String(16))  # customer: tự bấm; staff: manager ghi nhận thay
    response_note: Mapped[str | None] = mapped_column(String(1000))
    dispute_reason: Mapped[str | None] = mapped_column(String(1000))
    closed_by: Mapped[int | None] = mapped_column(BigInteger)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Giai đoạn 20: sổ thu – chi theo hợp đồng (02 §3.10), tạm ứng nhân viên ---------------------------------------


class CashDirection(StrEnum):
    RECEIPT = "receipt"  # thu
    PAYMENT = "payment"  # chi


class ResultClass(StrEnum):
    """Khoản mục tính vào kết quả hợp đồng thế nào (02 §3.10.1)."""

    REVENUE = "revenue"  # T01 – doanh thu thực thu
    OTHER_INCOME = "other_income"  # T04
    DIRECT_COST = "direct_cost"  # C02–C06
    NONE = "none"  # không tính: chi hộ / thu hồi chi hộ, tạm ứng, hoàn trả


class MdCashbookCategory(Base):
    """Khoản mục thu – chi (02 §3.10.1). Khoản mục gốc (T01–T05, C01–C08) do hệ thống seed; `manager` thêm khoản mục
    con (kế thừa chiều, cách tính kết quả của khoản mục cha), không xóa khoản mục đã có phiếu."""

    __tablename__ = "md_cashbook_categories"
    __table_args__ = (
        CheckConstraint(f"direction IN ({_in(CashDirection)})", name="direction_valid"),
        CheckConstraint(f"result_class IN ({_in(ResultClass)})", name="result_class_valid"),
        CheckConstraint("code ~ '^[TC][0-9]{2}(\\.[0-9]{1,2})?$'", name="code_format"),
        CheckConstraint("(parent_code IS NULL) = is_system", name="system_is_root"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    result_class: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(
        String(10), ForeignKey("md_cashbook_categories.code", ondelete="RESTRICT")
    )
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger)


class VoucherStatus(StrEnum):
    """02 §3.10.2."""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"  # phiếu chi đã duyệt (BR-114) – phiếu thu ghi sổ thẳng từ `submitted`
    RECORDED = "recorded"  # đã ghi sổ: không sửa, không xóa (BR-112)
    VOID = "void"


class PaymentMethod(StrEnum):
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    OFFSET = "offset"  # bù trừ với tạm ứng – chỉ hệ thống sinh khi hoàn ứng


class CounterpartyType(StrEnum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    STAFF = "staff"


class OpsCashVoucher(AuditColumnsMixin, Base):
    """Phiếu thu / phiếu chi (02 §3.10.2). Mọi phiếu thuộc một hợp đồng. Số dư quỹ chỉ tính phiếu `recorded`.
    Phiếu `recorded` chỉ được chuyển `void` (kèm lý do) – trigger CSDL chặn sửa / xóa (BR-112)."""

    __tablename__ = "ops_cash_vouchers"
    __table_args__ = (
        CheckConstraint(f"voucher_type IN ({_in(CashDirection)})", name="type_valid"),
        CheckConstraint(f"status IN ({_in(VoucherStatus)})", name="status_valid"),
        CheckConstraint(f"payment_method IN ({_in(PaymentMethod)})", name="method_valid"),
        CheckConstraint(f"counterparty_type IN ({_in(CounterpartyType)})", name="counterparty_type_valid"),
        CheckConstraint("amount > 0 AND exchange_rate > 0", name="amounts_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        CheckConstraint("approved_by IS NULL OR approved_by <> prepared_by", name="approver_not_preparer"),
        CheckConstraint(
            "status NOT IN ('approved', 'recorded') OR voucher_type = 'receipt' OR approved_by IS NOT NULL",
            name="payment_approved",  # BR-114
        ),
        CheckConstraint(
            "status <> 'recorded' OR (recorded_by IS NOT NULL AND recorded_at IS NOT NULL)", name="recorded_has_actor"
        ),
        CheckConstraint("status <> 'void' OR void_reason IS NOT NULL", name="void_has_reason"),
        CheckConstraint("payment_method <> 'offset' OR advance_id IS NOT NULL", name="offset_has_advance"),
        Index("ix_ops_cash_vouchers_contract_date", "contract_id", "voucher_date"),
        Index("ix_ops_cash_vouchers_advance", "advance_id"),
        Index("ix_ops_cash_vouchers_status", "status"),
    )

    voucher_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)  # PT-/PC-yyyy-nnnnn
    voucher_type: Mapped[str] = mapped_column(String(10), nullable=False)
    contract_id: Mapped[int] = _contract_fk()
    shipment_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ops_shipments.id", ondelete="RESTRICT"))
    category_code: Mapped[str] = mapped_column(
        String(10), ForeignKey("md_cashbook_categories.code", ondelete="RESTRICT"), nullable=False
    )
    counterparty_type: Mapped[str] = mapped_column(String(10), nullable=False)
    counterparty_id: Mapped[int | None] = mapped_column(BigInteger)  # md_parties.id / sys_users.id theo loại
    counterparty_name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    voucher_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=1, server_default="1")
    amount_base: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # quy đổi VND
    payment_method: Mapped[str] = mapped_column(String(16), nullable=False)
    supporting_file_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    external_invoice_no: Mapped[str | None] = mapped_column(String(100))
    advance_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ops_staff_advances.id", ondelete="RESTRICT"))
    settlement_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ops_contract_settlements.id", ondelete="RESTRICT")
    )
    replaces_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ops_cash_vouchers.id", ondelete="RESTRICT"), unique=True
    )  # phiếu thay thế cho phiếu đã `void`
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    prepared_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[int | None] = mapped_column(BigInteger)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    void_reason: Mapped[str | None] = mapped_column(String(500))
    voided_by: Mapped[int | None] = mapped_column(BigInteger)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdvanceStatus(StrEnum):
    """02 §3.10.3."""

    REQUESTED = "requested"
    APPROVED = "approved"
    DISBURSED = "disbursed"  # đã chi (phiếu chi C08)
    SETTLING = "settling"  # đã khai hoàn ứng, chờ duyệt
    SETTLED = "settled"
    REJECTED = "rejected"


OPEN_ADVANCE_STATUSES = (
    AdvanceStatus.REQUESTED.value,
    AdvanceStatus.APPROVED.value,
    AdvanceStatus.DISBURSED.value,
    AdvanceStatus.SETTLING.value,
)


class OpsStaffAdvance(AuditColumnsMixin, Base):
    """Tạm ứng – hoàn ứng của nhân viên (02 §3.10.3). BR-115: tối đa `OPS_ADVANCE_MAX_OPEN` khoản chưa hoàn ứng."""

    __tablename__ = "ops_staff_advances"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(AdvanceStatus)})", name="status_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        CheckConstraint("approved_by IS NULL OR approved_by <> staff_id", name="approver_not_staff"),
        CheckConstraint("settled_by IS NULL OR settled_by <> staff_id", name="settler_not_staff"),
        CheckConstraint("status <> 'rejected' OR decision_note IS NOT NULL", name="reject_has_note"),
        Index("ix_ops_staff_advances_staff_status", "staff_id", "status"),
        Index("ix_ops_staff_advances_status_due", "status", "due_date"),
    )

    advance_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)  # TU-yyyy-nnnn
    staff_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"), nullable=False)
    contract_id: Mapped[int] = _contract_fk()
    shipment_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ops_shipments.id", ondelete="RESTRICT"))
    purpose: Mapped[str] = mapped_column(String(1000), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="VND", server_default="VND")
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested", server_default="requested")
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    disbursed_by: Mapped[int | None] = mapped_column(BigInteger)
    disbursed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settlement_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    spent_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    settled_by: Mapped[int | None] = mapped_column(BigInteger)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
