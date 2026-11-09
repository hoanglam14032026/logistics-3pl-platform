"""
Danh mục địa điểm `md_locations` (Giai đoạn 16; 01 §4) – cảng, sân bay, ICD, kho, cửa khẩu, điểm giao hàng.

- Ghi: `admin`, `manager`; không xóa cứng – `:archive` / `:restore` (06 §2.5); địa điểm lưu trữ không sửa được. Mã
  (`code`) duy nhất, không đổi sau khi tạo. Nhật ký `location.create|update|archive|restore`.
- Đọc: vai trò nội bộ – mọi địa điểm; `customer` – chỉ địa điểm đang hoạt động gắn khách hàng của mình
  (`party_id = sys_users.party_id`), địa điểm khác → 404.
- `party_id` phải là khách hàng / đối tác đang hoạt động.
"""

from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.pagination import PageParams, like_pattern, order_by_clause, paginate
from app.core.rbac import Role
from app.core.timeutil import now_utc
from app.shared.audit import service as audit
from app.shared.identity.models import SysUser
from app.shared.masterdata.location_schemas import LocationCreate, LocationUpdate
from app.shared.masterdata.models import LocationType, MdLocation, MdParty, PartyStatus

ENTITY = "location"
AUDIT_FIELDS = (
    "code",
    "name",
    "location_type",
    "country_code",
    "address",
    "latitude",
    "longitude",
    "party_id",
    "status",
)
_SORTABLE = {
    "created_at": MdLocation.created_at,
    "code": MdLocation.code,
    "name": MdLocation.name,
    "location_type": MdLocation.location_type,
}


def _snap(location: MdLocation) -> dict:
    snap = audit.snapshot(location, AUDIT_FIELDS)
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in snap.items()}


def _not_found() -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy địa điểm")


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _duplicate() -> AppError:
    message = "Mã địa điểm đã có trong danh mục"
    return AppError(
        ErrorCode.DUPLICATE, 409, message, [{"field": "body.code", "issue": "duplicate", "message": message}]
    )


def _flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if "uq_md_locations_code" in str(exc.orig):
            raise _duplicate() from exc
        raise


def _check_party(db: Session, party_id: int | None) -> None:
    if party_id is None:
        return
    party = db.scalar(select(MdParty).where(MdParty.id == party_id).with_for_update(read=True))
    if party is None or party.status != PartyStatus.ACTIVE.value:
        message = "Khách hàng / đối tác không tồn tại hoặc đã lưu trữ"
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            message,
            [{"field": "body.party_id", "issue": "invalid", "message": message}],
        )


def _check_version(location: MdLocation, expected_version: int | None) -> None:
    if expected_version is not None and expected_version != location.row_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, 409, "Bản ghi đã bị người khác thay đổi; tải lại rồi thử lại")


def is_customer(user: SysUser) -> bool:
    return Role.CUSTOMER.value in user.role_codes


def _visible(stmt, user: SysUser):
    if is_customer(user):
        return stmt.where(MdLocation.party_id == user.party_id, MdLocation.status == PartyStatus.ACTIVE.value)
    return stmt


def get_location(db: Session, location_id: int, user: SysUser | None = None, for_update: bool = False) -> MdLocation:
    stmt = select(MdLocation).where(MdLocation.id == location_id)
    if user is not None:
        stmt = _visible(stmt, user)
    location = db.scalar(stmt.with_for_update() if for_update else stmt)
    if location is None:
        raise _not_found()
    return location


def list_locations(
    db: Session,
    user: SysUser,
    params: PageParams,
    q: str | None = None,
    location_type: LocationType | None = None,
    party_id: int | None = None,
    status: str = "active",
) -> tuple[list[MdLocation], int]:
    stmt = _visible(select(MdLocation), user)
    if status != "all":
        stmt = stmt.where(MdLocation.status == status)
    if location_type is not None:
        stmt = stmt.where(MdLocation.location_type == location_type.value)
    if party_id is not None:
        stmt = stmt.where(MdLocation.party_id == party_id)
    if q:
        pattern = like_pattern(q)
        stmt = stmt.where(
            or_(
                MdLocation.code.ilike(pattern, escape="\\"),
                MdLocation.name.ilike(pattern, escape="\\"),
                MdLocation.address.ilike(pattern, escape="\\"),
            )
        )
    stmt = stmt.order_by(order_by_clause(params.sort, _SORTABLE, default="code"), MdLocation.id)
    return paginate(db, stmt, params)


def create_location(db: Session, data: LocationCreate, actor_id: int) -> MdLocation:
    _check_party(db, data.party_id)
    location = MdLocation(**data.model_dump(), created_by=actor_id, updated_by=actor_id)
    db.add(location)
    _flush(db)
    audit.record(db, "location.create", ENTITY, location.id, actor_id, after=_snap(location))
    db.commit()
    return location


def update_location(
    db: Session, location_id: int, data: LocationUpdate, actor_id: int, expected_version: int | None
) -> MdLocation:
    location = get_location(db, location_id, for_update=True)
    _check_version(location, expected_version)
    if location.status == PartyStatus.ARCHIVED.value:
        raise _conflict("Địa điểm đã lưu trữ; khôi phục trước khi sửa")
    changes = data.model_dump(exclude_unset=True)
    if "party_id" in changes:
        _check_party(db, changes["party_id"])
    before = _snap(location)
    for key, value in changes.items():
        setattr(location, key, value)
    location.updated_by = actor_id
    _flush(db)
    old, new = audit.changed(before, _snap(location))
    if new:
        audit.record(db, "location.update", ENTITY, location.id, actor_id, old, new)
    db.commit()
    db.refresh(location)
    return location


def set_archived(
    db: Session, location_id: int, archived: bool, actor_id: int, expected_version: int | None
) -> MdLocation:
    location = get_location(db, location_id, for_update=True)
    _check_version(location, expected_version)
    target = PartyStatus.ARCHIVED.value if archived else PartyStatus.ACTIVE.value
    if location.status == target:
        raise _conflict("Địa điểm đã lưu trữ" if archived else "Địa điểm đang hoạt động")
    before = location.status
    location.status = target
    location.archived_at = now_utc() if archived else None
    location.archived_by = actor_id if archived else None
    location.updated_by = actor_id
    action = "location.archive" if archived else "location.restore"
    audit.record(db, action, ENTITY, location.id, actor_id, {"status": before}, {"status": target})
    db.commit()
    db.refresh(location)
    return location
