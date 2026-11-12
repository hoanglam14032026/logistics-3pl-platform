"""Giai đoạn 23 (sửa) – nối giấy tờ nhập tay theo hợp đồng vào chứng từ lô (BR-103).

- ops_documents.source_contract_document_id (FK ops_contract_documents, NULL được): xác nhận một giấy tờ cấp lô có
  loại trùng loại chứng từ lô → ghi phiên bản `ops_documents` (bản sao file) để lần kiểm tra BR-103, quy tắc chứng từ
  bắt buộc, EV-121 và KPI nhận được; cột này truy vết nguồn và chống ghi trùng khi xác nhận lại cùng file.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ops_documents", sa.Column("source_contract_document_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_ops_documents_source_contract_document_id_ops_contract_documents"),
        "ops_documents",
        "ops_contract_documents",
        ["source_contract_document_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_ops_documents_source_contract_document_id_ops_contract_documents"),
        "ops_documents",
        type_="foreignkey",
    )
    op.drop_column("ops_documents", "source_contract_document_id")
