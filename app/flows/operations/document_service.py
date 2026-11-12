"""
Chứng từ lô + kiểm tra chứng từ BR-103 (Giai đoạn 16; 02 §3.3, BR-03, BR-103).

- Tải chứng từ (`ops_staff` phụ trách lô, lô chưa đóng/hủy): file vào `sys_files` (mục đích `shipment_document`), dòng
  `ops_documents` phiên bản mới; cùng loại đã có → bản cũ `superseded` (BR-03, không xóa). Phát EV-121. Chưa có lần
  kiểm tra đang chờ → tạo `ops_document_checks` `pending` (run_no kế tiếp) – lần chờ sẵn có bao cả chứng từ mới.
- Ghi kết quả (`ops_staff` phụ trách lô): mỗi loại chứng từ hiện có phải có một dòng `ok` (đủ) / `invalid` (sai – lý
  do); loại chưa tải lên có thể ghi `missing` (thiếu – lý do). Không còn `missing` / `invalid` → `passed`, ngược lại
  `failed` (`blocking_count` = số dòng không đạt). Chụp phiên bản chứng từ đã kiểm (`document_versions`). Phát EV-122
  (`discrepancies` nguồn `manual`), nhật ký `document_check.complete`.
- Giai đoạn 17: khách hàng gửi chứng từ qua cổng (`portal_upload`, `channel = portal`); chứng từ mới khi lô đang
  `docs_verified` → lô quay về `docs_collecting` (lịch sử + nhật ký `shipment.reopen_documents` + EV-112) và có lần
  kiểm tra mới đang chờ. Quy tắc tự động: loại chứng từ bắt buộc theo `ops_document_requirements` (CSDL) chưa có →
  dòng `missing` `source = auto` khi ghi kết quả (nhân viên không cần gõ tay).
- BR-103: lô chỉ chuyển `docs_verified` khi lần kiểm tra mới nhất đã xong, `passed` và không có lần đang chờ
  (`ensure_documents_verified`).
"""

from typing import Any

from fastapi import UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.timeutil import now_utc
from app.flows.operations.models import (
    TERMINAL_SHIPMENT_STATUSES,
    DocCheckOutcome,
    OpsDocument,
    OpsDocumentCheck,
    OpsDocumentCheckItem,
    OpsDocumentRequirement,
    OpsShipment,
    ShipmentStatus,
)
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import DocumentCheckCompleted, DocumentReceived, ShipmentStatusChanged
from app.shared.files import service as files
from app.shared.files.models import SysFile
from app.shared.identity.models import SysUser

ENTITY = "shipment_document"
CHECK_ENTITY = "document_check"
READ_ROLES = {"ops_staff", "manager", "business_manager", "instructor", "admin"}


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, message, [{"field": field, "issue": "invalid", "message": message}]
    )


def _writable_shipment(db: Session, shipment_id: int, actor: SysUser) -> OpsShipment:
    from app.flows.operations import shipment_service

    shipment = shipment_service.get_shipment(db, shipment_id, for_update=True)
    shipment_service._ensure_writer(shipment, actor)
    if shipment.status in TERMINAL_SHIPMENT_STATUSES:
        raise _conflict(f"Lô hàng đã {shipment.status}; không thay đổi chứng từ được")
    return shipment


def current_documents(db: Session, shipment_id: int) -> dict[str, OpsDocument]:
    rows = db.scalars(
        select(OpsDocument).where(OpsDocument.shipment_id == shipment_id, OpsDocument.status == "current")
    )
    return {d.doc_type: d for d in rows}


def list_documents(db: Session, shipment_id: int, include_superseded: bool = False) -> list[OpsDocument]:
    stmt = select(OpsDocument).where(OpsDocument.shipment_id == shipment_id)
    if not include_superseded:
        stmt = stmt.where(OpsDocument.status == "current")
    return list(db.scalars(stmt.order_by(OpsDocument.doc_type, OpsDocument.version_no)))


def pending_check(db: Session, shipment_id: int) -> OpsDocumentCheck | None:
    return db.scalar(
        select(OpsDocumentCheck).where(
            OpsDocumentCheck.shipment_id == shipment_id, OpsDocumentCheck.status == "pending"
        )
    )


def upload(
    db: Session, shipment_id: int, doc_type: str, upload_file: UploadFile, note: str | None, actor: SysUser
) -> tuple[OpsDocument, OpsDocumentCheck]:
    """Nhân viên (`ops_staff` phụ trách lô) tải chứng từ."""
    shipment = _writable_shipment(db, shipment_id, actor)
    return _store(db, shipment, doc_type, upload_file, note, actor, "staff")


def portal_upload(
    db: Session, party_id: int, shipment_id: int, doc_type: str, upload_file: UploadFile, note: str | None, actor
) -> tuple[OpsDocument, OpsDocumentCheck]:
    """Giai đoạn 17 – khách hàng gửi chứng từ qua cổng: chỉ lô thấy được trên cổng (BR-111 – khác → 404), chưa
    đóng / hủy."""
    from app.flows.operations import shipment_service

    shipment_service.get_portal_shipment(db, party_id, shipment_id)
    shipment = db.scalar(select(OpsShipment).where(OpsShipment.id == shipment_id).with_for_update())
    if shipment.status in TERMINAL_SHIPMENT_STATUSES:
        raise _conflict(f"Lô hàng đã {shipment.status}; không gửi thêm chứng từ được")
    return _store(db, shipment, doc_type, upload_file, note, actor, "portal")


def _reopen_documents(db: Session, shipment: OpsShipment, doc: OpsDocument, actor_id: int) -> None:
    """Giai đoạn 17: chứng từ mới sau `docs_verified` → lô quay về `docs_collecting` (kiểm tra lại – BR-103)."""
    from app.flows.operations import shipment_service

    before = shipment.status
    shipment.status = ShipmentStatus.DOCS_COLLECTING.value
    shipment.updated_by = actor_id
    note = f"Có chứng từ mới ({doc.doc_type} v{doc.version_no}) – kiểm tra lại (BR-103)"
    shipment_service._history(db, shipment, before, actor_id, note)
    audit.record(
        db,
        "shipment.reopen_documents",
        "shipment",
        shipment.id,
        actor_id,
        {"status": before},
        {"status": shipment.status, "document_id": doc.id, "doc_type": doc.doc_type, "channel": doc.channel},
    )
    publisher.publish(
        db,
        ShipmentStatusChanged(shipment_id=shipment.id, from_=before, to=shipment.status),
        "shipment",
        shipment.id,
        actor_id,
        shipment.mode,
    )


def _store(
    db: Session, shipment: OpsShipment, doc_type: str, upload_file: UploadFile, note: str | None, actor, channel: str
) -> tuple[OpsDocument, OpsDocumentCheck]:
    previous = current_documents(db, shipment.id).get(doc_type)
    version_no = (
        db.scalar(
            select(func.max(OpsDocument.version_no)).where(
                OpsDocument.shipment_id == shipment.id, OpsDocument.doc_type == doc_type
            )
        )
        or 0
    ) + 1
    if previous is not None:
        previous.status = "superseded"
        db.flush()
    stored = files.store(db, upload_file, "shipment_document", "operations", ENTITY, 0, actor.id)
    doc = OpsDocument(
        shipment_id=shipment.id,
        doc_type=doc_type,
        version_no=version_no,
        file_id=stored.id,
        note=note,
        channel=channel,
        uploaded_by=actor.id,
    )
    db.add(doc)
    db.flush()
    stored.owner_id = doc.id
    reopened = shipment.status == ShipmentStatus.DOCS_VERIFIED.value
    if reopened:
        _reopen_documents(db, shipment, doc, actor.id)
    check = pending_check(db, shipment.id)
    created = check is None
    if created:
        run_no = (
            db.scalar(select(func.max(OpsDocumentCheck.run_no)).where(OpsDocumentCheck.shipment_id == shipment.id)) or 0
        ) + 1
        check = OpsDocumentCheck(
            shipment_id=shipment.id, run_no=run_no, triggered_by_document_id=doc.id, created_by=actor.id
        )
        db.add(check)
        db.flush()
    audit.record(
        db,
        "document.upload",
        ENTITY,
        doc.id,
        actor.id,
        {"superseded_document_id": previous.id} if previous else None,
        {
            "shipment_id": shipment.id,
            "doc_type": doc_type,
            "version_no": version_no,
            "file_id": stored.id,
            "sha256": stored.sha256,
            "check_id": check.id,
            "check_created": created,
            "channel": channel,
            "shipment_reopened": reopened,
        },
    )
    publisher.publish(
        db,
        DocumentReceived(document_id=doc.id, shipment_id=shipment.id, doc_type=doc_type, version_no=version_no),
        ENTITY,
        doc.id,
        actor.id,
        shipment.mode,
    )
    files.commit_or_discard(db, [stored])
    db.refresh(doc)
    db.refresh(check)
    return doc, check


def get_check(db: Session, check_id: int, for_update: bool = False) -> OpsDocumentCheck:
    stmt = select(OpsDocumentCheck).where(OpsDocumentCheck.id == check_id)
    check = db.scalar(stmt.with_for_update() if for_update else stmt)
    if check is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy lần kiểm tra chứng từ")
    from app.flows.operations import shipment_service

    shipment_service.get_shipment(db, check.shipment_id)  # BR-01: chỉ lô production
    return check


def list_checks(db: Session, shipment_id: int) -> list[OpsDocumentCheck]:
    return list(
        db.scalars(
            select(OpsDocumentCheck)
            .where(OpsDocumentCheck.shipment_id == shipment_id)
            .order_by(OpsDocumentCheck.run_no)
        )
    )


def items_of(db: Session, check_id: int) -> list[OpsDocumentCheckItem]:
    return list(
        db.scalars(
            select(OpsDocumentCheckItem)
            .where(OpsDocumentCheckItem.check_id == check_id)
            .order_by(OpsDocumentCheckItem.doc_type)
        )
    )


def complete_check(
    db: Session, check_id: int, items: list[dict[str, Any]], note: str | None, actor: SysUser
) -> OpsDocumentCheck:
    check = get_check(db, check_id)
    shipment = _writable_shipment(db, check.shipment_id, actor)
    db.refresh(check, with_for_update=True)
    if check.status != "pending":
        raise _conflict("Lần kiểm tra đã ghi kết quả")
    current = current_documents(db, shipment.id)
    by_type = {i["doc_type"]: i for i in items}
    uncovered = sorted(set(current) - set(by_type))
    if uncovered:
        raise _invalid("body.items", f"Chưa ghi kết quả cho chứng từ: {', '.join(uncovered)}")
    auto = {a["doc_type"]: a for a in auto_missing(db, shipment, current) if a["doc_type"] not in by_type}
    if not by_type and not auto:
        raise _invalid("body.items", "Cần ít nhất một kết quả")
    rows = [
        OpsDocumentCheckItem(
            check_id=check.id,
            doc_type=t,
            document_id=None,
            outcome=DocCheckOutcome.MISSING.value,
            reason=a["reason"],
            source="auto",
            rule_id=a["rule_id"],
        )
        for t, a in sorted(auto.items())
    ]
    for doc_type, item in sorted(by_type.items()):
        outcome = item["outcome"]
        doc = current.get(doc_type)
        if outcome == DocCheckOutcome.MISSING.value and doc is not None:
            raise _invalid("body.items", f"'{doc_type}' đã có chứng từ – không ghi 'missing' được (dùng ok / invalid)")
        if outcome != DocCheckOutcome.MISSING.value and doc is None:
            raise _invalid("body.items", f"'{doc_type}' chưa có chứng từ – chỉ ghi 'missing'")
        rows.append(
            OpsDocumentCheckItem(
                check_id=check.id,
                doc_type=doc_type,
                document_id=doc.id if doc else None,
                outcome=outcome,
                reason=item.get("reason"),
            )
        )
    db.add_all(rows)
    failures = [r for r in rows if r.outcome != DocCheckOutcome.OK.value]
    check.status = "completed"
    check.result = "failed" if failures else "passed"
    check.blocking_count = len(failures)
    check.document_versions = {t: {"document_id": d.id, "version_no": d.version_no} for t, d in sorted(current.items())}
    check.note = note
    check.checked_by, check.checked_at = actor.id, now_utc()
    db.flush()
    audit.record(
        db,
        "document_check.complete",
        CHECK_ENTITY,
        check.id,
        actor.id,
        {"status": "pending"},
        {
            "shipment_id": shipment.id,
            "run_no": check.run_no,
            "result": check.result,
            "blocking_count": check.blocking_count,
            "items": [
                {"doc_type": r.doc_type, "outcome": r.outcome, "reason": r.reason, "source": r.source} for r in rows
            ],
            "note": note,
        },
    )
    publisher.publish(
        db,
        DocumentCheckCompleted(
            check_id=check.id,
            shipment_id=shipment.id,
            run_no=check.run_no,
            blocking_count=check.blocking_count,
            warning_count=0,
            discrepancies=[
                {
                    "source": r.source,
                    "rule_code": "DOC_REQUIRED_MISSING" if r.source == "auto" else f"DOC_{r.outcome.upper()}",
                    "doc_types": [r.doc_type],
                    "line_no": None,
                    "field": None,
                    "expected": None,
                    "actual": None,
                    "note": r.reason,
                }
                for r in failures
            ],
        ),
        CHECK_ENTITY,
        check.id,
        actor.id,
        shipment.mode,
    )
    db.commit()
    db.refresh(check)
    return check


def ensure_documents_verified(db: Session, shipment_id: int) -> None:
    """BR-103 – gọi khi chuyển `docs_collecting → docs_verified`."""
    if pending_check(db, shipment_id) is not None:
        raise _conflict("BR-103: còn lần kiểm tra chứng từ chưa ghi kết quả (có chứng từ mới tải lên)")
    latest = db.scalar(
        select(OpsDocumentCheck)
        .where(OpsDocumentCheck.shipment_id == shipment_id)
        .order_by(OpsDocumentCheck.run_no.desc())
        .limit(1)
    )
    if latest is None:
        raise _conflict("BR-103: lô chưa có lần kiểm tra chứng từ nào")
    if latest.result != "passed":
        raise _conflict(f"BR-103: lần kiểm tra chứng từ mới nhất (lần {latest.run_no}) còn {latest.blocking_count} lỗi")


def _can_read_file(db: Session, user, file: SysFile) -> bool:
    doc = db.get(OpsDocument, file.owner_id)
    if doc is None or doc.file_id != file.id or not (READ_ROLES & set(user.role_codes)):
        return False
    shipment = db.get(OpsShipment, doc.shipment_id)
    return shipment is not None and shipment.mode == "production"


def register_file_access() -> None:
    files.ACCESS_CHECKERS[ENTITY] = _can_read_file


# --- Quy tắc tự động cơ bản (Giai đoạn 17) ------------------------------------------------------------------------


def required_rules(db: Session, shipment: OpsShipment) -> dict[str, OpsDocumentRequirement]:
    """Loại chứng từ bắt buộc của lô theo quy tắc đang bật (`transport_mode` / `direction` NULL = mọi giá trị)."""
    r = OpsDocumentRequirement
    rows = db.scalars(
        select(r)
        .where(
            r.is_active.is_(True),
            or_(r.transport_mode.is_(None), r.transport_mode == shipment.transport_mode),
            or_(r.direction.is_(None), r.direction == shipment.direction),
        )
        .order_by(r.doc_type, r.id)
    )
    out: dict[str, OpsDocumentRequirement] = {}
    for rule in rows:
        out.setdefault(rule.doc_type, rule)
    return out


def _rule_label(rule: OpsDocumentRequirement) -> str:
    scope = " / ".join(x for x in (rule.transport_mode, rule.direction) if x) or "mọi lô"
    return f"Thiếu chứng từ bắt buộc '{rule.doc_type}' (quy tắc #{rule.id} – {scope})"


def auto_missing(
    db: Session, shipment: OpsShipment, current: dict[str, OpsDocument] | None = None
) -> list[dict[str, Any]]:
    """Chứng từ bắt buộc chưa có bản hiện hành → `missing` tự động (không cần nhân viên ghi tay)."""
    current = current if current is not None else current_documents(db, shipment.id)
    return [
        {"doc_type": t, "rule_id": rule.id, "reason": _rule_label(rule)}
        for t, rule in sorted(required_rules(db, shipment).items())
        if t not in current
    ]
