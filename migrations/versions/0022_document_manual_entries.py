"""Giai đoạn 23 – khai báo + nhập tay giấy tờ, chứng từ theo hợp đồng dịch vụ (thay AI đọc bill).

- Gỡ 4 cột `ai_*` / `bill_image_path` của ops_documents (migration 6662ce09ad81 – AI đọc bill, đã bỏ).
- ops_document_catalog: danh mục giấy tờ, chứng từ nghiệp vụ lập sẵn (khách hàng cung cấp / đối tác phát hành; cấp hợp
  đồng / cấp lô; bước trong chuỗi nghiệp vụ). `code` trùng tên file biểu mẫu `doc_entry_schemas/<code>.json`.
- ops_contract_documents: giấy tờ đã khai báo của từng hợp đồng dịch vụ, kèm dữ liệu nhập tay (JSONB), tự động lưu
  từng bước; draft / confirmed / void. Không xóa (trigger).

Revision ID: 0022
Revises: 6662ce09ad81
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | Sequence[str] | None = "6662ce09ad81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S1 = "Hồ sơ pháp lý & yêu cầu dịch vụ"
S2 = "Hồ sơ hàng hóa từ chủ hàng"
S3 = "Vận tải quốc tế"
S5 = "Nhận & giao hàng"
S6 = "Chi phí & thanh toán"
# (code, tên, nguồn, loại đối tác gợi ý, cấp, bước, nhãn bước, mô tả)
CATALOG = [
    (
        "customer_business_license",
        "Giấy đăng ký kinh doanh của khách hàng",
        "customer",
        None,
        "contract",
        1,
        S1,
        "Hồ sơ pháp lý của chủ hàng",
    ),
    ("power_of_attorney", "Giấy ủy quyền", "customer", None, "contract", 1, S1, "Ủy quyền cho công ty làm thủ tục"),
    (
        "service_request",
        "Yêu cầu dịch vụ / lệnh làm hàng",
        "customer",
        None,
        "shipment",
        1,
        S1,
        "Khách hàng yêu cầu thực hiện dịch vụ cho lô",
    ),
    (
        "sales_contract",
        "Hợp đồng mua bán",
        "customer",
        "foreign_seller",
        "shipment",
        2,
        S2,
        "Hợp đồng giữa người bán và người mua",
    ),
    ("commercial_invoice", "Hóa đơn thương mại", "customer", "foreign_seller", "shipment", 2, S2, None),
    ("packing_list", "Phiếu đóng gói (Packing List)", "customer", "foreign_seller", "shipment", 2, S2, None),
    ("certificate_of_origin", "Giấy chứng nhận xuất xứ (C/O)", "customer", None, "shipment", 2, S2, None),
    (
        "special_permit",
        "Giấy phép / kiểm tra chuyên ngành",
        "customer",
        None,
        "shipment",
        2,
        S2,
        "Giấy phép nhập khẩu, kiểm dịch…",
    ),
    (
        "booking_confirmation",
        "Xác nhận đặt chỗ (Booking)",
        "partner",
        "shipping_line",
        "shipment",
        3,
        S3,
        "Hãng tàu / hãng bay xác nhận booking",
    ),
    ("bill_of_lading", "Vận đơn đường biển (B/L)", "partner", "shipping_line", "shipment", 3, S3, None),
    ("air_waybill", "Vận đơn hàng không (AWB)", "partner", "airline", "shipment", 3, S3, None),
    (
        "customs_declaration",
        "Tờ khai hải quan",
        "partner",
        "agent",
        "shipment",
        4,
        "Thông quan",
        "Đại lý hải quan khai",
    ),
    ("delivery_order", "Lệnh giao hàng (D/O)", "partner", "shipping_line", "shipment", 5, S5, None),
    ("warehouse_receipt", "Phiếu nhập / xuất kho", "partner", "warehouse", "shipment", 5, S5, None),
    ("proof_of_delivery", "Biên bản giao nhận hàng (POD)", "partner", "carrier", "shipment", 5, S5, None),
    ("partner_invoice", "Hóa đơn của đối tác", "partner", None, "shipment", 6, S6, "Phí cảng, hãng tàu, vận tải, kho…"),
    (
        "payment_proof",
        "Chứng từ thanh toán của khách hàng",
        "customer",
        None,
        "contract",
        6,
        S6,
        "Ủy nhiệm chi, biên lai",
    ),
    ("other_document", "Giấy tờ khác", "partner", None, "contract", 7, "Khác", "Chưa có biểu mẫu riêng"),
]


def upgrade() -> None:
    for column in ("ai_raw", "ai_confidence", "ai_parsed", "bill_image_path"):
        op.drop_column("ops_documents", column)

    catalog = op.create_table(
        "ops_document_catalog",
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("partner_type", sa.String(length=32), nullable=True),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("step_label", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.CheckConstraint("source IN ('customer', 'partner')", name=op.f("ck_ops_document_catalog_source_valid")),
        sa.CheckConstraint("level IN ('contract', 'shipment')", name=op.f("ck_ops_document_catalog_level_valid")),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_ops_document_catalog")),
    )
    op.bulk_insert(
        catalog,
        [
            {
                "code": c,
                "name": n,
                "source": s,
                "partner_type": p,
                "level": lv,
                "step_no": st,
                "step_label": sl,
                "sort_order": i * 10,
                "description": d,
            }
            for i, (c, n, s, p, lv, st, sl, d) in enumerate(CATALOG, start=1)
        ],
    )

    op.create_table(
        "ops_contract_documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=True),
        sa.Column("catalog_code", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("party_id", sa.BigInteger(), nullable=True),
        sa.Column("party_name", sa.String(length=300), nullable=True),
        sa.Column("received_date", sa.Date(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("file_id", sa.BigInteger(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("status", sa.String(length=12), server_default="draft", nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.BigInteger(), nullable=True),
        sa.Column("void_reason", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'void')", name=op.f("ck_ops_contract_documents_status_valid")
        ),
        sa.CheckConstraint(
            "(status = 'confirmed') = (confirmed_at IS NOT NULL AND confirmed_by IS NOT NULL)",
            name=op.f("ck_ops_contract_documents_confirmed_matches_status"),
        ),
        sa.CheckConstraint(
            "(status = 'void') = (void_reason IS NOT NULL)", name=op.f("ck_ops_contract_documents_void_matches_status")
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["ops_service_contracts.id"],
            name=op.f("fk_ops_contract_documents_contract_id_ops_service_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["ops_shipments.id"],
            name=op.f("fk_ops_contract_documents_shipment_id_ops_shipments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["catalog_code"],
            ["ops_document_catalog.code"],
            name=op.f("fk_ops_contract_documents_catalog_code_ops_document_catalog"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["party_id"],
            ["md_parties.id"],
            name=op.f("fk_ops_contract_documents_party_id_md_parties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"], ["sys_files.id"], name=op.f("fk_ops_contract_documents_file_id_sys_files"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_contract_documents")),
    )
    op.create_index("ix_ops_contract_documents_contract", "ops_contract_documents", ["contract_id", "catalog_code"])
    op.create_index("ix_ops_contract_documents_shipment", "ops_contract_documents", ["shipment_id"])
    op.execute(
        "CREATE TRIGGER ops_contract_documents_no_delete BEFORE DELETE ON ops_contract_documents "
        "FOR EACH ROW EXECUTE FUNCTION ops_g16_no_delete()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ops_contract_documents_no_delete ON ops_contract_documents")
    op.drop_index("ix_ops_contract_documents_shipment", table_name="ops_contract_documents")
    op.drop_index("ix_ops_contract_documents_contract", table_name="ops_contract_documents")
    op.drop_table("ops_contract_documents")
    op.drop_table("ops_document_catalog")
    op.add_column("ops_documents", sa.Column("bill_image_path", sa.String(), nullable=True))
    op.add_column("ops_documents", sa.Column("ai_parsed", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("ops_documents", sa.Column("ai_confidence", sa.Float(), nullable=True))
    op.add_column("ops_documents", sa.Column("ai_raw", sa.Text(), nullable=True))
