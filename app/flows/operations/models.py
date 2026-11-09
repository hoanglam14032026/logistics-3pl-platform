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
    origin_location: Mapped[str] = mapped_column(String(200), nullable=False)
    destination_location: Mapped[str] = mapped_column(String(200), nullable=False)
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
