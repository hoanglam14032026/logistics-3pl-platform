"""Giai đoạn 9 – AI local trích xuất chứng từ cho mọi tiến trình: ai_models, sys_doc_jobs, sys_doc_extractions.

- ai_models: danh mục model AI local do admin quản lý (provider ollama; endpoint NULL = OLLAMA_URL). Seed 1 model mẫu
  `qwen3.8:27b` (vision, qua OLLAMA_URL). Không xóa cứng (trigger) – "xóa" = deleted_at + tắt.
- sys_doc_jobs: job trích xuất (file qua sys_files, model đã chọn + ảnh chụp mã/tên model, bản nháp + độ tin cậy, duyệt/
  từ chối, bản ghi nghiệp vụ đã nối). Không xóa; job đã duyệt/từ chối/lỗi không đổi nội dung được (chỉ nối bản ghi).
- sys_doc_extractions: kết quả từng lần OCR + AI (văn bản theo trang + bbox, đầu ra thô) – chỉ ghi thêm (trigger).
- md_parties, ops_service_contracts, ops_shipments: thêm cột source_doc_job_id (FK, UNIQUE) – nối với job đã duyệt.
  Không sửa migration 0001–0009.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_models",
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=20), server_default="ollama", nullable=False),
        sa.Column("endpoint", sa.String(length=500), nullable=True),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("model_type", sa.String(length=10), nullable=False),
        sa.Column("supports_pdf", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("supports_image", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("supports_docx", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.BigInteger(), nullable=True),
        sa.Column("is_seeded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("code ~ '^[a-z0-9][a-z0-9._:-]{1,49}$'", name=op.f("ck_ai_models_code_format")),
        sa.CheckConstraint("model_type IN ('vision', 'text', 'ocr')", name=op.f("ck_ai_models_model_type_valid")),
        sa.CheckConstraint("provider IN ('ollama')", name=op.f("ck_ai_models_provider_valid")),
        sa.CheckConstraint("deleted_at IS NULL OR NOT is_active", name=op.f("ck_ai_models_deleted_is_inactive")),
        sa.CheckConstraint(
            "supports_pdf OR supports_image OR supports_docx", name=op.f("ck_ai_models_supports_something")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_models")),
        sa.UniqueConstraint("code", name=op.f("uq_ai_models_code")),
    )
    op.create_table(
        "sys_doc_jobs",
        sa.Column("doc_type", sa.String(length=50), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("ai_model_id", sa.BigInteger(), nullable=False),
        sa.Column("model_code", sa.String(length=50), nullable=False),
        sa.Column("model_ref", sa.String(length=200), nullable=False),
        sa.Column("model_type", sa.String(length=10), nullable=False),
        sa.Column("ocr_engine", sa.String(length=20), nullable=False),
        sa.Column("ocr_model_id", sa.BigInteger(), nullable=True),
        sa.Column("ocr_model_ref", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("overall_confidence", sa.Float(), nullable=True),
        sa.Column("review_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("approved_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("queued_via", sa.String(length=16), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.BigInteger(), nullable=True),
        sa.Column("reject_reason", sa.String(length=500), nullable=True),
        sa.Column("linked_entity_type", sa.String(length=32), nullable=True),
        sa.Column("linked_entity_id", sa.BigInteger(), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_by", sa.BigInteger(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("client_ip", sa.String(length=45), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(status = 'approved') = (approved_at IS NOT NULL)", name=op.f("ck_sys_doc_jobs_approved_matches_status")
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (failed_at IS NOT NULL)", name=op.f("ck_sys_doc_jobs_failed_matches_status")
        ),
        sa.CheckConstraint(
            "(status = 'rejected') = (rejected_at IS NOT NULL AND reject_reason IS NOT NULL)",
            name=op.f("ck_sys_doc_jobs_rejected_matches_status"),
        ),
        sa.CheckConstraint(
            "linked_entity_id IS NULL OR status = 'approved'", name=op.f("ck_sys_doc_jobs_linked_only_when_approved")
        ),
        sa.CheckConstraint(
            "ocr_engine IN ('tesseract', 'paddle', 'ollama')", name=op.f("ck_sys_doc_jobs_ocr_engine_valid")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'ready_for_review', 'needs_review', 'approved', 'rejected', 'failed')",
            name=op.f("ck_sys_doc_jobs_status_valid"),
        ),
        sa.CheckConstraint(
            "(linked_entity_type IS NULL) = (linked_entity_id IS NULL)",
            name=op.f("ck_sys_doc_jobs_linked_entity_together"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_sys_doc_jobs_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["ai_model_id"], ["ai_models.id"], name=op.f("fk_sys_doc_jobs_ai_model_id_ai_models"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["file_id"], ["sys_files.id"], name=op.f("fk_sys_doc_jobs_file_id_sys_files"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ocr_model_id"], ["ai_models.id"], name=op.f("fk_sys_doc_jobs_ocr_model_id_ai_models"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_doc_jobs")),
        sa.UniqueConstraint("file_id", name=op.f("uq_sys_doc_jobs_file_id")),
    )
    op.create_index("ix_sys_doc_jobs_created_by_created_at", "sys_doc_jobs", ["created_by", "created_at"], unique=False)
    op.create_index(
        "ix_sys_doc_jobs_linked_entity", "sys_doc_jobs", ["linked_entity_type", "linked_entity_id"], unique=False
    )
    op.create_index("ix_sys_doc_jobs_status_created_at", "sys_doc_jobs", ["status", "created_at"], unique=False)
    op.create_table(
        "sys_doc_extractions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("run_no", sa.Integer(), nullable=False),
        sa.Column("ocr_engine", sa.String(length=20), nullable=False),
        sa.Column("model_ref", sa.String(length=200), nullable=False),
        sa.Column("pages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("text_chars", sa.Integer(), nullable=False),
        sa.Column("raw_output", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ocr_ms", sa.Integer(), nullable=False),
        sa.Column("llm_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["sys_doc_jobs.id"],
            name=op.f("fk_sys_doc_extractions_job_id_sys_doc_jobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_doc_extractions")),
        sa.UniqueConstraint("job_id", "run_no", name="uq_sys_doc_extractions_job_run"),
    )
    op.add_column("md_parties", sa.Column("source_doc_job_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(op.f("uq_md_parties_source_doc_job_id"), "md_parties", ["source_doc_job_id"])
    op.create_foreign_key(
        op.f("fk_md_parties_source_doc_job_id_sys_doc_jobs"),
        "md_parties",
        "sys_doc_jobs",
        ["source_doc_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("ops_service_contracts", sa.Column("source_doc_job_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(
        op.f("uq_ops_service_contracts_source_doc_job_id"), "ops_service_contracts", ["source_doc_job_id"]
    )
    op.create_foreign_key(
        op.f("fk_ops_service_contracts_source_doc_job_id_sys_doc_jobs"),
        "ops_service_contracts",
        "sys_doc_jobs",
        ["source_doc_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("ops_shipments", sa.Column("source_doc_job_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(op.f("uq_ops_shipments_source_doc_job_id"), "ops_shipments", ["source_doc_job_id"])
    op.create_foreign_key(
        op.f("fk_ops_shipments_source_doc_job_id_sys_doc_jobs"),
        "ops_shipments",
        "sys_doc_jobs",
        ["source_doc_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    _seed()
    _triggers()


SEED_DESCRIPTION = (
    "Model mẫu (Giai đoạn 9): Qwen 3.8 27B chạy trên Ollama nội bộ – đọc được ảnh trang + văn bản OCR. "
    "Admin thêm/sửa/tắt model trong danh mục này, không sửa mã."
)


def _seed() -> None:
    ai_models = sa.table(
        "ai_models",
        sa.column("code", sa.String),
        sa.column("display_name", sa.String),
        sa.column("provider", sa.String),
        sa.column("endpoint", sa.String),
        sa.column("model_id", sa.String),
        sa.column("model_type", sa.String),
        sa.column("supports_pdf", sa.Boolean),
        sa.column("supports_image", sa.Boolean),
        sa.column("supports_docx", sa.Boolean),
        sa.column("is_active", sa.Boolean),
        sa.column("description", sa.Text),
        sa.column("is_seeded", sa.Boolean),
    )
    op.bulk_insert(
        ai_models,
        [
            {
                "code": "qwen3.8-27b",
                "display_name": "Qwen 3.8 27B (Ollama nội bộ)",
                "provider": "ollama",
                "endpoint": None,  # dùng OLLAMA_URL
                "model_id": "qwen3.8:27b",
                "model_type": "vision",
                "supports_pdf": True,
                "supports_image": True,
                "supports_docx": True,
                "is_active": True,
                "description": SEED_DESCRIPTION,
                "is_seeded": True,
            }
        ],
    )


def _triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION docai_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Không xóa bản ghi bảng % (job/model cũ cần cho truy vết)', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END $$;
        CREATE TRIGGER ai_models_no_delete BEFORE DELETE ON ai_models
            FOR EACH ROW EXECUTE FUNCTION docai_no_delete();

        CREATE FUNCTION sys_doc_jobs_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Không xóa job trích xuất %', OLD.id USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.doc_type IS DISTINCT FROM NEW.doc_type OR OLD.file_id IS DISTINCT FROM NEW.file_id
               OR OLD.ai_model_id IS DISTINCT FROM NEW.ai_model_id OR OLD.model_code IS DISTINCT FROM NEW.model_code
               OR OLD.model_ref IS DISTINCT FROM NEW.model_ref OR OLD.created_by IS DISTINCT FROM NEW.created_by THEN
                RAISE EXCEPTION 'Job trích xuất %: không đổi chứng từ, model đã chọn, người tạo', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status IN ('approved', 'rejected', 'failed') AND (
                   OLD.status IS DISTINCT FROM NEW.status OR OLD.fields IS DISTINCT FROM NEW.fields
                   OR OLD.approved_data IS DISTINCT FROM NEW.approved_data
                   OR OLD.reject_reason IS DISTINCT FROM NEW.reject_reason
                   OR (OLD.linked_entity_id IS NOT NULL AND (
                       OLD.linked_entity_id IS DISTINCT FROM NEW.linked_entity_id
                       OR OLD.linked_entity_type IS DISTINCT FROM NEW.linked_entity_type))) THEN
                RAISE EXCEPTION 'Job trích xuất % đã %; không thay đổi được', OLD.id, OLD.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER sys_doc_jobs_guard BEFORE UPDATE OR DELETE ON sys_doc_jobs
            FOR EACH ROW EXECUTE FUNCTION sys_doc_jobs_guard();

        CREATE FUNCTION sys_doc_extractions_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Bảng % chỉ ghi thêm; không sửa, không xóa', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END $$;
        CREATE TRIGGER sys_doc_extractions_append_only BEFORE UPDATE OR DELETE ON sys_doc_extractions
            FOR EACH ROW EXECUTE FUNCTION sys_doc_extractions_append_only();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER sys_doc_extractions_append_only ON sys_doc_extractions;
        DROP TRIGGER sys_doc_jobs_guard ON sys_doc_jobs;
        DROP TRIGGER ai_models_no_delete ON ai_models;
        DROP FUNCTION sys_doc_extractions_append_only();
        DROP FUNCTION sys_doc_jobs_guard();
        DROP FUNCTION docai_no_delete();
        """
    )
    op.drop_constraint(op.f("fk_ops_shipments_source_doc_job_id_sys_doc_jobs"), "ops_shipments", type_="foreignkey")
    op.drop_constraint(op.f("uq_ops_shipments_source_doc_job_id"), "ops_shipments", type_="unique")
    op.drop_column("ops_shipments", "source_doc_job_id")
    op.drop_constraint(
        op.f("fk_ops_service_contracts_source_doc_job_id_sys_doc_jobs"), "ops_service_contracts", type_="foreignkey"
    )
    op.drop_constraint(op.f("uq_ops_service_contracts_source_doc_job_id"), "ops_service_contracts", type_="unique")
    op.drop_column("ops_service_contracts", "source_doc_job_id")
    op.drop_constraint(op.f("fk_md_parties_source_doc_job_id_sys_doc_jobs"), "md_parties", type_="foreignkey")
    op.drop_constraint(op.f("uq_md_parties_source_doc_job_id"), "md_parties", type_="unique")
    op.drop_column("md_parties", "source_doc_job_id")
    op.drop_table("sys_doc_extractions")
    op.drop_index("ix_sys_doc_jobs_status_created_at", table_name="sys_doc_jobs")
    op.drop_index("ix_sys_doc_jobs_linked_entity", table_name="sys_doc_jobs")
    op.drop_index("ix_sys_doc_jobs_created_by_created_at", table_name="sys_doc_jobs")
    op.drop_table("sys_doc_jobs")
    op.drop_table("ai_models")
