"""
Nối job trích xuất **đã duyệt** với bản ghi nghiệp vụ được tạo từ nó (Giai đoạn 9): tham số `source_doc_job_id` của API
tạo khách hàng/đối tác, hợp đồng, lô hàng – **không sửa logic tạo cũ**, chỉ thêm nối.

`attach(db, job_id, target, model_cls, actor)` gọi ở router **trước** service tạo bản ghi:
1. Khóa dòng job (`FOR UPDATE` – tới khi service commit): phải tồn tại, người gọi xem được, `approved`, đúng loại đích
   (`target_type`), chưa nối bản ghi nào. Sai → 404 / 409 / 422, chưa tạo gì.
2. Gắn sự kiện cho phiên CSDL: lần flush có bản ghi mới kiểu `model_cls` → đặt `source_doc_job_id` (cùng câu INSERT);
   sau flush (đã có id) → cập nhật job `linked_entity_type`/`linked_entity_id` + ghi nhật ký `doc_job.linked` (kèm các
   trường người tạo đổi khác dữ liệu đã duyệt) trên **cùng giao dịch**. Service commit → cùng lưu; lỗi → cùng mất.
Cột `source_doc_job_id` UNIQUE: một job tạo tối đa một bản ghi.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import event, insert, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.request_context import current_client_ip, current_request_id
from app.core.timeutil import now_utc, to_utc_iso
from app.shared.audit.models import SysAuditLog
from app.shared.audit.service import _json_value
from app.shared.docai import extract, registry
from app.shared.docai import service as docs
from app.shared.docai.models import JobStatus, SysDocJob

QUERY_DESCRIPTION = (
    "Job trích xuất chứng từ (AI local) **đã duyệt** dùng làm nguồn – nối bản ghi với job để truy vết. "
    "Job chưa duyệt / khác loại / đã dùng → lỗi, không tạo bản ghi."
)


def _error(status: int, code: ErrorCode, message: str) -> AppError:
    detail = {"field": "query.source_doc_job_id", "issue": "invalid", "message": message}
    return AppError(code, status, message, [detail])


def _comparable(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and "T" in value:
        try:
            return to_utc_iso(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            pass
    value = _json_value(value)
    return str(value).lower() if isinstance(value, bool) else str(value)


def attach(db: Session, job_id: int, target: str, model_cls: type, actor) -> None:
    job = db.scalar(select(SysDocJob).where(SysDocJob.id == job_id).with_for_update())
    if job is None or not docs.can_view(actor, job):
        raise _error(404, ErrorCode.NOT_FOUND, "Không tìm thấy job trích xuất (source_doc_job_id)")
    if job.status != JobStatus.APPROVED.value:
        raise _error(422, ErrorCode.VALIDATION_ERROR, "Job trích xuất chưa được duyệt – duyệt trước khi tạo bản ghi")
    if job.target_type != target:
        raise _error(422, ErrorCode.VALIDATION_ERROR, f"Job loại '{job.doc_type}' không dùng để tạo '{target}'")
    if job.linked_entity_id is not None:
        raise _error(
            409,
            ErrorCode.DUPLICATE,
            f"Job đã được dùng để tạo {job.linked_entity_type} #{job.linked_entity_id}",
        )
    schema = registry.get_schema(job.doc_type)
    approved_form = extract.form_payload(schema, job.approved_data or {}) if schema else {}
    state: dict[str, Any] = {"obj": None, "done": False}

    def _before_flush(session: Session, _ctx, _instances) -> None:
        if state["obj"] is not None:
            return
        for obj in session.new:
            if isinstance(obj, model_cls) and getattr(obj, "source_doc_job_id", None) is None:
                obj.source_doc_job_id = job_id
                state["obj"] = obj
                return

    def _after_flush(session: Session, _ctx) -> None:
        obj = state["obj"]
        if obj is None or state["done"] or obj.id is None:
            return
        state["done"] = True
        conn = session.connection()
        result = conn.execute(
            update(SysDocJob.__table__)
            .where(
                SysDocJob.id == job_id,
                SysDocJob.status == JobStatus.APPROVED.value,
                SysDocJob.linked_entity_id.is_(None),
            )
            .values(
                linked_entity_type=target,
                linked_entity_id=obj.id,
                linked_at=now_utc(),
                linked_by=actor.id,
                row_version=SysDocJob.row_version + 1,
            )
        )
        if result.rowcount != 1:
            raise _error(409, ErrorCode.DUPLICATE, "Job đã được dùng để tạo bản ghi khác")
        overrides = {}
        for key, approved in approved_form.items():
            saved = getattr(obj, key, None)
            if _comparable(saved) != _comparable(approved):
                overrides[key] = {"approved": approved, "saved": _json_value(saved)}
        conn.execute(
            insert(SysAuditLog.__table__).values(
                at=now_utc(),
                actor_id=actor.id,
                action="doc_job.linked",
                entity_type=docs.ENTITY,
                entity_id=job_id,
                after={
                    "linked_entity_type": target,
                    "linked_entity_id": obj.id,
                    "overrides": docs._short(overrides),
                },
                ip=current_client_ip(),
                request_id=current_request_id(),
            )
        )

    event.listen(db, "before_flush", _before_flush)
    event.listen(db, "after_flush", _after_flush)

    def _cleanup(*_args) -> None:
        for name, fn in (("before_flush", _before_flush), ("after_flush", _after_flush)):
            if event.contains(db, name, fn):
                event.remove(db, name, fn)

    event.listen(db, "after_commit", _cleanup, once=True)
    event.listen(db, "after_soft_rollback", _cleanup, once=True)
