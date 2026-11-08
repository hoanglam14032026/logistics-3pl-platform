"""Giai đoạn 1 – danh mục khách hàng, đối tác md_parties; tài khoản customer gắn khách hàng (01 §4; 02 §9.2).

- md_parties: cột chung 06 §2.4; party_type (8 loại 01 §4, CHECK); tax_code duy nhất (NULL không tính);
  country_code ISO 3166-1 alpha-2; status active/archived, archived_at khớp status (06 §2.5 – không xóa cứng).
- sys_users: party_id → md_parties (RESTRICT – không xóa được party còn tài khoản), is_customer_admin;
  CHECK is_customer_admin → có party_id. Ràng buộc "customer ↔ có party" kiểm tra trong service (khác bảng).
  CHECK thêm vào bảng có sẵn: autogenerate không phát hiện – viết tay.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "md_parties",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("party_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("tax_code", sa.String(length=32), nullable=True),
        sa.Column("country_code", sa.String(length=2), server_default="VN", nullable=False),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("contact_name", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL)", name=op.f("ck_md_parties_archived_at_matches_status")
        ),
        sa.CheckConstraint("country_code ~ '^[A-Z]{2}$'", name=op.f("ck_md_parties_country_code_iso2")),
        sa.CheckConstraint(
            "party_type IN ('customer', 'foreign_seller', 'foreign_buyer', 'shipping_line', "
            "'airline', 'agent', 'carrier', 'warehouse')",
            name=op.f("ck_md_parties_party_type_valid"),
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_md_parties_status_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_md_parties")),
        sa.UniqueConstraint("tax_code", name=op.f("uq_md_parties_tax_code")),
    )
    op.create_index(op.f("ix_md_parties_party_type"), "md_parties", ["party_type"], unique=False)
    op.add_column("sys_users", sa.Column("party_id", sa.BigInteger(), nullable=True))
    op.add_column("sys_users", sa.Column("is_customer_admin", sa.Boolean(), server_default="false", nullable=False))
    op.create_index(op.f("ix_sys_users_party_id"), "sys_users", ["party_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_sys_users_party_id_md_parties"), "sys_users", "md_parties", ["party_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_check_constraint(
        op.f("ck_sys_users_customer_admin_has_party"), "sys_users", "NOT is_customer_admin OR party_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_sys_users_customer_admin_has_party"), "sys_users", type_="check")
    op.drop_constraint(op.f("fk_sys_users_party_id_md_parties"), "sys_users", type_="foreignkey")
    op.drop_index(op.f("ix_sys_users_party_id"), table_name="sys_users")
    op.drop_column("sys_users", "is_customer_admin")
    op.drop_column("sys_users", "party_id")
    op.drop_index(op.f("ix_md_parties_party_type"), table_name="md_parties")
    op.drop_table("md_parties")
