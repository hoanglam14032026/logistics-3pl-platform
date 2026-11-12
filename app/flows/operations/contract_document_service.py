"""
Khai báo + nhập tay giấy tờ, chứng từ theo hợp đồng dịch vụ (Giai đoạn 23) – **không dùng AI / OCR**.

Quy trình (màn hình 2 phần: trái xem file, phải nhập trường chính):
1. Nhân viên **chọn loại giấy tờ từ danh mục** hệ thống lập sẵn (`ops_document_catalog`: khách hàng cung cấp / đối tác
   phát hành; cấp hợp đồng hoặc cấp lô; theo bước chuỗi nghiệp vụ) → `declare`: tạo bản ghi
   `ops_contract_documents` thuộc
   hợp đồng (kèm file tùy chọn, bên gửi, ngày tiếp nhận). Lưu ngay.
2. Mỗi lần nhập / sửa trường → `update` lưu ngay vào CSDL của hợp đồng (tự động lưu, giao diện gọi sau mỗi thay đổi);
   `If-Match` chống ghi đè lẫn nhau.
3. `confirm` (đủ trường bắt buộc) khóa dữ liệu; `reopen` mở lại (ghi lý do); `void` hủy (ghi lý do, không xóa).

Biểu mẫu trường nhập: `doc_entry_schemas/<code>.json` (cùng cấu trúc FieldSpec của docai nên dùng lại kiểm kiểu /
chuẩn hóa).

Quyền: đọc – ops_staff, manager, business_manager, instructor, admin (hợp đồng `production`, BR-01); ghi – ops_staff
và manager với hồ sơ cấp hợp đồng; hồ sơ cấp lô: ops_staff **phụ trách lô** (như tải chứng từ lô). Hợp đồng `closed` →
không khai báo / sửa thêm. Mọi thao tác ghi `audit` trên cùng giao dịch.

Khi xác nhận vận đơn (B/L, AWB) gắn lô: điền chỗ **còn trống** của `ops_shipments` (etd, eta, incoterm, incoterm_place);
giá trị đã có không ghi đè – lệch nhau ghi vào nhật ký + trả về giao diện (`shipment_update.conflicts`).
"""

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.timeutil import now_utc
from app.flows.operations.models import (
    ContractStatus,
    DocType,
    OpsContractDocument,
    OpsDocumentCatalog,
    OpsServiceContract,
    OpsShipment,
)
from app.shared.audit import service as audit
from app.shared.docai.registry import DocTypeSchema, InvalidValue, load_dir, normalize
from app.shared.files import service as files
from app.shared.files.models import SysFile
from app.shared.identity.models import SysUser
from app.shared.masterdata.models import MdParty

ENTITY = "contract_document"
FILE_PURPOSE = "contract_received_document"
SCHEMA_DIR = Path(__file__).resolve().parent / "doc_entry_schemas"
READ_ROLES = {"ops_staff", "manager", "business_manager", "instructor", "admin"}
_TRANSPORT_DOCS = {"bill_of_lading", "air_waybill"}
_SHIPMENT_FILL = ("etd", "eta", "incoterm", "incoterm_place")
# Loại giấy tờ cấp lô trùng loại chứng từ lô (DocType) → xác nhận ghi sang ops_documents (BR-103)
SHIPMENT_DOC_TYPES = frozenset(t.value for t in DocType if t is not DocType.OTHER)
_SHIPMENT_DOC_MESSAGES = {
    "no_file": "Chưa đính file – chưa tính vào kiểm tra chứng từ lô (BR-103); đính file, mở lại rồi xác nhận lại",
    "file_type": "Chứng từ lô chỉ nhận pdf, jpg, png, docx, xlsx – chưa tính vào kiểm tra chứng từ lô (BR-103)",
    "unchanged": "Chứng từ lô đã có đúng file này – không ghi thêm phiên bản",
}


# --- Biểu mẫu ----------------------------------------------------------------------------------------------------


@lru_cache
def all_schemas() -> dict[str, DocTypeSchema]:
    return load_dir(SCHEMA_DIR)


def get_schema(code: str) -> DocTypeSchema | None:
    return all_schemas().get(code)


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, message, [{"field": field, "issue": "invalid", "message": message}]
    )


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def clean_values(schema: DocTypeSchema, values: dict[str, Any]) -> dict[str, Any]:
    """Chuẩn hóa từng trường; trường lạ / sai kiểu → 422 (nêu đúng trường)."""
    out: dict[str, Any] = {}
    for key, raw in values.items():
        spec = schema.field(key)
        if spec is None:
            raise _invalid(f"body.values.{key}", f"Trường '{key}' không có trong biểu mẫu {schema.code}")
        try:
            out[key] = normalize(spec, raw)
        except InvalidValue as exc:
            raise _invalid(f"body.values.{key}", f"{spec.label}: {exc}") from exc
    return out


def missing_required(schema: DocTypeSchema, data: dict[str, Any]) -> list[str]:
    return [f.key for f in schema.fields if f.required and data.get(f.key) is None]


# --- Danh mục giấy tờ ---------------------------------------------------------------------------------------------


def list_catalog(
    db: Session, source: str | None = None, level: str | None = None, include_inactive: bool = False
) -> list[OpsDocumentCatalog]:
    stmt = select(OpsDocumentCatalog).order_by(OpsDocumentCatalog.sort_order, OpsDocumentCatalog.code)
    if source:
        stmt = stmt.where(OpsDocumentCatalog.source == source)
    if level:
        stmt = stmt.where(OpsDocumentCatalog.level == level)
    if not include_inactive:
        stmt = stmt.where(OpsDocumentCatalog.is_active.is_(True))
    return list(db.scalars(stmt))


def get_catalog(db: Session, code: str) -> OpsDocumentCatalog:
    item = db.get(OpsDocumentCatalog, code)
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không có loại giấy tờ này trong danh mục")
    return item


# --- Truy cập ----------------------------------------------------------------------------------------------------


def _contract(db: Session, contract_id: int, for_update: bool = False) -> OpsServiceContract:
    from app.flows.operations import service as contracts

    contract = contracts.get_contract(db, contract_id, for_update=for_update)
    if contract.mode != "production":  # BR-01
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy hợp đồng")
    return contract


def get_doc(db: Session, doc_id: int, for_update: bool = False) -> OpsContractDocument:
    stmt = select(OpsContractDocument).where(OpsContractDocument.id == doc_id)
    doc = db.scalar(stmt.with_for_update() if for_update else stmt)
    if doc is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy giấy tờ")
    _contract(db, doc.contract_id)
    return doc


def _check_version(doc: OpsContractDocument, expected: int | None) -> None:
    if expected is not None and expected != doc.row_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, 409, "Dữ liệu đã bị người khác thay đổi; tải lại rồi thử lại")


def _shipment_for(db: Session, contract: OpsServiceContract, shipment_id: int, actor: SysUser) -> OpsShipment:
    from app.flows.operations import shipment_service

    shipment = shipment_service.get_shipment(db, shipment_id, for_update=True)
    if shipment.contract_id != contract.id:
        raise _invalid("body.shipment_id", "Lô hàng không thuộc hợp đồng này")
    shipment_service._ensure_writer(shipment, actor)  # ops_staff phụ trách lô
    shipment_service._ensure_open(shipment)
    return shipment


def _ensure_writable(db: Session, doc: OpsContractDocument, actor: SysUser) -> OpsServiceContract:
    contract = _contract(db, doc.contract_id)
    if contract.status == ContractStatus.CLOSED.value:
        raise _conflict("Hợp đồng đã đóng; không thay đổi giấy tờ được")
    if doc.shipment_id is not None:
        _shipment_for(db, contract, doc.shipment_id, actor)
    return contract


def is_editable(db: Session, doc: OpsContractDocument, actor: SysUser) -> bool:
    if doc.status != "draft" or not ({"ops_staff", "manager"} & set(actor.role_codes)):
        return False
    contract = db.get(OpsServiceContract, doc.contract_id)
    if contract is None or contract.status == ContractStatus.CLOSED.value:
        return False
    if doc.shipment_id is not None:
        shipment = db.get(OpsShipment, doc.shipment_id)
        return bool(
            shipment
            and "ops_staff" in actor.role_codes
            and shipment.assignee_id == actor.id
            and shipment.status not in ("closed", "cancelled")
        )
    return True


def _snapshot_party(db: Session, catalog: OpsDocumentCatalog, contract: OpsServiceContract, party_id, party_name):
    if catalog.source == "customer":  # khách hàng gửi: luôn là khách hàng của hợp đồng
        return contract.customer_id, contract.customer_name
    if party_id is None:
        return None, (party_name or None)
    party = db.get(MdParty, party_id)
    if party is None or party.status != "active":
        raise _invalid("body.party_id", "Không tìm thấy đối tác (hoặc đã ngừng dùng)")
    if party.id == contract.customer_id:
        raise _invalid("body.party_id", "Giấy tờ của đối tác – không chọn chính khách hàng của hợp đồng")
    return party.id, party.name


# --- Khai báo, nhập, xác nhận ------------------------------------------------------------------------------------


def declare(
    db: Session,
    contract_id: int,
    catalog_code: str,
    shipment_id: int | None,
    party_id: int | None,
    party_name: str | None,
    received_date: date | None,
    note: str | None,
    upload: UploadFile | None,
    actor: SysUser,
) -> OpsContractDocument:
    contract = _contract(db, contract_id)
    if contract.status == ContractStatus.CLOSED.value:
        raise _conflict("Hợp đồng đã đóng; không khai báo thêm giấy tờ được")
    catalog = get_catalog(db, catalog_code)
    schema = get_schema(catalog.code)
    if not catalog.is_active or schema is None:
        raise _invalid("body.catalog_code", "Loại giấy tờ này hiện không dùng")
    if catalog.level == "shipment":
        if shipment_id is None:
            raise _invalid("body.shipment_id", f"'{catalog.name}' gắn với một lô – chọn lô hàng của hợp đồng")
        _shipment_for(db, contract, shipment_id, actor)
    elif shipment_id is not None:
        raise _invalid("body.shipment_id", f"'{catalog.name}' là hồ sơ của cả hợp đồng – không gắn lô")
    party_id, party_label = _snapshot_party(db, catalog, contract, party_id, party_name)
    stored = files.store(db, upload, FILE_PURPOSE, "operations", ENTITY, 0, actor.id) if upload else None
    doc = OpsContractDocument(
        contract_id=contract.id,
        contract_version=contract.contract_version,
        shipment_id=shipment_id,
        catalog_code=catalog.code,
        source=catalog.source,
        party_id=party_id,
        party_name=party_label,
        received_date=received_date,
        note=note,
        file_id=stored.id if stored else None,
        schema_version=schema.version,
        data={},
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(doc)
    db.flush()
    if stored:
        stored.owner_id = doc.id
    audit.record(
        db,
        "contract_document.declare",
        ENTITY,
        doc.id,
        actor.id,
        None,
        {
            "contract_id": contract.id,
            "catalog_code": catalog.code,
            "shipment_id": shipment_id,
            "party_id": party_id,
            "file_id": doc.file_id,
        },
    )
    if stored:
        files.commit_or_discard(db, [stored])
    else:
        db.commit()
    db.refresh(doc)
    return doc


def update(
    db: Session,
    doc_id: int,
    values: dict[str, Any],
    meta: dict[str, Any],
    actor: SysUser,
    expected: int | None,
) -> OpsContractDocument:
    """Tự động lưu: gộp trường gửi lên vào dữ liệu hiện có (rỗng = xóa trường); `meta`: bên gửi, ngày nhận, ghi chú."""
    doc = get_doc(db, doc_id, for_update=True)
    contract = _ensure_writable(db, doc, actor)
    _check_version(doc, expected)
    if doc.status != "draft":
        raise _conflict(
            "Giấy tờ đã xác nhận / hủy – mở lại (reopen) trước khi sửa"
            if doc.status == "confirmed"
            else "Giấy tờ đã hủy"
        )
    schema = get_schema(doc.catalog_code)
    cleaned = clean_values(schema, values)
    before = dict(doc.data)
    merged = {k: v for k, v in {**before, **cleaned}.items() if v is not None}
    changed = {
        k: {"from": before.get(k), "to": merged.get(k)}
        for k in set(before) | set(cleaned)
        if before.get(k) != merged.get(k)
    }
    if "received_date" in meta and meta["received_date"] != doc.received_date:
        old_date, new_date = doc.received_date, meta["received_date"]
        changed["received_date"] = {
            "from": old_date.isoformat() if old_date else None,
            "to": new_date.isoformat() if new_date else None,
        }
        doc.received_date = new_date
    if "note" in meta and (meta["note"] or None) != doc.note:
        changed["note"] = {"from": doc.note, "to": meta["note"] or None}
        doc.note = meta["note"] or None
    if "party_id" in meta or "party_name" in meta:
        catalog = get_catalog(db, doc.catalog_code)
        party_id, label = _snapshot_party(db, catalog, contract, meta.get("party_id"), meta.get("party_name"))
        if (party_id, label) != (doc.party_id, doc.party_name):
            changed["party"] = {"from": doc.party_id, "to": party_id}
            doc.party_id, doc.party_name = party_id, label
    if not changed:
        return doc  # không đổi gì: không tăng phiên bản, không ghi nhật ký
    doc.data = merged
    doc.updated_by = actor.id
    db.flush()
    audit.record(db, "contract_document.save", ENTITY, doc.id, actor.id, None, {"changed": changed})
    db.commit()
    db.refresh(doc)
    return doc


def attach_file(
    db: Session, doc_id: int, upload: UploadFile, actor: SysUser, expected: int | None
) -> OpsContractDocument:
    doc = get_doc(db, doc_id, for_update=True)
    _ensure_writable(db, doc, actor)
    _check_version(doc, expected)
    if doc.status != "draft":
        raise _conflict("Giấy tờ đã xác nhận / hủy – mở lại trước khi đổi file")
    stored = files.store(db, upload, FILE_PURPOSE, "operations", ENTITY, doc.id, actor.id)
    previous = doc.file_id
    doc.file_id = stored.id
    doc.updated_by = actor.id
    db.flush()
    audit.record(
        db,
        "contract_document.file",
        ENTITY,
        doc.id,
        actor.id,
        {"file_id": previous},
        {"file_id": stored.id, "sha256": stored.sha256},
    )
    files.commit_or_discard(db, [stored])
    db.refresh(doc)
    return doc


def _fill_shipment(shipment: OpsShipment, data: dict[str, Any], actor: SysUser) -> dict[str, Any]:
    filled: dict[str, Any] = {}
    conflicts: dict[str, Any] = {}
    for key in _SHIPMENT_FILL:
        value = data.get(key)
        if value is None:
            continue
        if key in ("etd", "eta"):
            value = datetime.fromisoformat(value)
        current = getattr(shipment, key)
        if current is None:
            setattr(shipment, key, value)
            filled[key] = str(value)
        elif current != value:
            conflicts[key] = {"shipment": str(current), "document": str(value)}
    if filled:
        shipment.updated_by = actor.id
    return {"filled": filled, "conflicts": conflicts}


def confirm(db: Session, doc_id: int, actor: SysUser, expected: int | None) -> tuple[OpsContractDocument, dict]:
    doc = get_doc(db, doc_id, for_update=True)
    _ensure_writable(db, doc, actor)
    _check_version(doc, expected)
    if doc.status != "draft":
        raise _conflict("Giấy tờ đã xác nhận" if doc.status == "confirmed" else "Giấy tờ đã hủy")
    schema = get_schema(doc.catalog_code)
    missing = missing_required(schema, doc.data)
    if missing:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            "Thiếu trường bắt buộc: " + ", ".join(schema.field(k).label for k in missing),
            [
                {"field": f"values.{k}", "issue": "required", "message": f"Bắt buộc nhập: {schema.field(k).label}"}
                for k in missing
            ],
        )
    doc.status = "confirmed"
    doc.confirmed_at = now_utc()
    doc.confirmed_by = actor.id
    doc.updated_by = actor.id
    applied: dict[str, Any] = {"filled": {}, "conflicts": {}}
    if doc.shipment_id is not None and doc.catalog_code in _TRANSPORT_DOCS:
        applied = _fill_shipment(db.get(OpsShipment, doc.shipment_id), doc.data, actor)
    db.flush()
    registered, copied = _to_shipment_document(db, doc, actor)
    audit.record(
        db,
        "contract_document.confirm",
        ENTITY,
        doc.id,
        actor.id,
        None,
        {"shipment": applied, "shipment_document": registered},
    )
    if copied is not None:
        files.commit_or_discard(db, [copied])
    else:
        db.commit()
    db.refresh(doc)
    return doc, applied, registered


def _to_shipment_document(
    db: Session, doc: OpsContractDocument, actor: SysUser
) -> tuple[dict[str, Any] | None, SysFile | None]:
    """Giấy tờ cấp lô có loại trùng loại chứng từ lô (`ops_documents.doc_type`) → ghi phiên bản chứng từ lô để kiểm
    tra BR-103, quy tắc chứng từ bắt buộc, EV-121, KPI nhận được. Trả về (kết quả cho giao diện, file mới cần commit).
    Không có file / loại file không nhận ở chứng từ lô → không ghi, nêu lý do (xác nhận vẫn thành công)."""
    if doc.shipment_id is None or doc.catalog_code not in SHIPMENT_DOC_TYPES:
        return None, None
    from app.flows.operations import document_service

    if doc.file_id is None:
        return {"registered": False, "reason": "no_file", "message": _SHIPMENT_DOC_MESSAGES["no_file"]}, None
    source = db.get(SysFile, doc.file_id)
    if not files.allowed_for("shipment_document", source.mime_type):
        return {"registered": False, "reason": "file_type", "message": _SHIPMENT_DOC_MESSAGES["file_type"]}, None
    shipment = db.get(OpsShipment, doc.shipment_id)
    result = document_service.register_from_contract_document(
        db, shipment, doc.catalog_code, source, doc.note, actor, doc.id
    )
    if result is None:
        return {"registered": False, "reason": "unchanged", "message": _SHIPMENT_DOC_MESSAGES["unchanged"]}, None
    shipment_doc, check, stored = result
    return {
        "registered": True,
        "document_id": shipment_doc.id,
        "version_no": shipment_doc.version_no,
        "check_id": check.id,
        "shipment_status": shipment.status,
    }, stored


def reopen(db: Session, doc_id: int, reason: str, actor: SysUser, expected: int | None) -> OpsContractDocument:
    doc = get_doc(db, doc_id, for_update=True)
    _ensure_writable(db, doc, actor)
    _check_version(doc, expected)
    if doc.status != "confirmed":
        raise _conflict("Giấy tờ chưa xác nhận nên không cần mở lại")
    doc.status = "draft"
    doc.confirmed_at = doc.confirmed_by = None
    doc.updated_by = actor.id
    db.flush()
    audit.record(db, "contract_document.reopen", ENTITY, doc.id, actor.id, None, {"reason": reason})
    db.commit()
    db.refresh(doc)
    return doc


def void(db: Session, doc_id: int, reason: str, actor: SysUser, expected: int | None) -> OpsContractDocument:
    doc = get_doc(db, doc_id, for_update=True)
    _ensure_writable(db, doc, actor)
    _check_version(doc, expected)
    if doc.status == "void":
        raise _conflict("Giấy tờ đã hủy")
    if doc.status == "confirmed":
        raise _conflict("Giấy tờ đã xác nhận – mở lại trước khi hủy")
    doc.status = "void"
    doc.void_reason = reason
    doc.updated_by = actor.id
    db.flush()
    audit.record(db, "contract_document.void", ENTITY, doc.id, actor.id, None, {"reason": reason})
    db.commit()
    db.refresh(doc)
    return doc


# --- Đọc ----------------------------------------------------------------------------------------------------------


def list_docs(
    db: Session,
    contract_id: int,
    shipment_id: int | None = None,
    catalog_code: str | None = None,
    status: str | None = None,
) -> list[OpsContractDocument]:
    _contract(db, contract_id)
    stmt = select(OpsContractDocument).where(OpsContractDocument.contract_id == contract_id)
    if shipment_id is not None:
        stmt = stmt.where(OpsContractDocument.shipment_id == shipment_id)
    if catalog_code:
        stmt = stmt.where(OpsContractDocument.catalog_code == catalog_code)
    if status:
        stmt = stmt.where(OpsContractDocument.status == status)
    return list(db.scalars(stmt.order_by(OpsContractDocument.created_at, OpsContractDocument.id)))


def _can_read_file(db: Session, user, file: SysFile) -> bool:
    doc = db.scalar(select(OpsContractDocument).where(OpsContractDocument.file_id == file.id))
    if doc is None or not (READ_ROLES & set(user.role_codes)):
        return False
    contract = db.get(OpsServiceContract, doc.contract_id)
    return contract is not None and contract.mode == "production"


def register_file_access() -> None:
    files.ACCESS_CHECKERS[ENTITY] = _can_read_file
