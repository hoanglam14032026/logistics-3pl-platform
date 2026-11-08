"""
Danh mục khách hàng, đối tác (01 §4; 06 §2.3–2.5, §4) và tài khoản `customer` gắn khách hàng (02 §9.2).

- Không xóa cứng: `:archive` / `:restore` (06 §2.5). Party đã lưu trữ không sửa được, không tạo tài khoản mới được.
- Không lưu trữ được khách hàng còn tài khoản `customer` đang hoạt động (vô hiệu hóa tài khoản trước) – tài khoản của
  khách hàng đã lưu trữ không được dùng cổng. Kiểm tra và tạo tài khoản khóa dòng party (`FOR UPDATE` / `FOR SHARE`)
  để hai thao tác song song không lọt.
- Không đổi `party_type` khỏi `customer` khi đã có tài khoản gắn vào.
"""

from collections.abc import Callable

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.pagination import PageParams, like_pattern, order_by_clause, paginate
from app.core.rbac import Role
from app.core.timeutil import now_utc
from app.shared.audit import service as audit
from app.shared.identity import service as identity_service
from app.shared.identity.models import SysUser
from app.shared.identity.schemas import CustomerAccountCreate, UserCreate
from app.shared.masterdata.models import MdParty, PartyStatus, PartyType
from app.shared.masterdata.schemas import PartyCreate, PartyUpdate, check_tax_code

_SORTABLE = {
    "created_at": MdParty.created_at,
    "name": MdParty.name,
    "party_type": MdParty.party_type,
    "tax_code": MdParty.tax_code,
}


# --- Điểm móc cho luồng nghiệp vụ ----------------------------------------------------------------------------
# `shared` không import `flows` (01 §3, N4). Luồng sở hữu dữ liệu liên quan tới party (vd. hợp đồng – Luồng 1) đăng ký
# hàm kiểm tra ở đây; hàm trả thông báo lỗi (→ 409) hoặc None nếu cho phép.
PartyGuard = Callable[[Session, MdParty], str | None]
ARCHIVE_GUARDS: list[PartyGuard] = []  # trước khi lưu trữ party
TYPE_CHANGE_GUARDS: list[PartyGuard] = []  # trước khi đổi party_type khỏi customer
CUSTOMER_ACCOUNT_GUARDS: list[PartyGuard] = []  # trước khi manager/customer admin tạo tài khoản customer


def _run_guards(db: Session, guards: list[PartyGuard], party: MdParty, status_code: int = 409) -> None:
    for guard in guards:
        message = guard(db, party)
        if message:
            if status_code == 409:
                raise _conflict(message)
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                422,
                message,
                [{"field": "path.party_id", "issue": "not_allowed", "message": message}],
            )


PARTY_AUDIT_FIELDS = (
    "party_type",
    "name",
    "tax_code",
    "country_code",
    "address",
    "contact_name",
    "phone",
    "email",
    "note",
    "status",
)


def _audit(db: Session, action: str, party: MdParty, actor_id: int, before=None, after=None) -> None:
    audit.record(db, action, "party", party.id, actor_id, before, after)


def _not_found() -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy khách hàng/đối tác")


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _duplicate(exc: IntegrityError) -> AppError:
    if "tax_code" in str(exc.orig):
        message = "Mã số thuế đã có trong danh mục"
        return AppError(
            ErrorCode.DUPLICATE, 409, message, [{"field": "body.tax_code", "issue": "duplicate", "message": message}]
        )
    raise exc


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _duplicate(exc) from exc


def _check_version(party: MdParty, expected_version: int | None) -> None:
    if expected_version is not None and expected_version != party.row_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, 409, "Bản ghi đã bị người khác thay đổi; tải lại rồi thử lại")


def get_party(db: Session, party_id: int, for_update: bool = False) -> MdParty:
    stmt = select(MdParty).where(MdParty.id == party_id)
    party = db.scalar(stmt.with_for_update() if for_update else stmt)
    if party is None:
        raise _not_found()
    return party


def list_parties(
    db: Session,
    params: PageParams,
    q: str | None = None,
    party_type: PartyType | None = None,
    country_code: str | None = None,
    status: str = "active",
) -> tuple[list[MdParty], int]:
    stmt = select(MdParty)
    if status != "all":
        stmt = stmt.where(MdParty.status == status)
    if party_type is not None:
        stmt = stmt.where(MdParty.party_type == party_type.value)
    if country_code is not None:
        stmt = stmt.where(MdParty.country_code == country_code.upper())
    if q:
        pattern = like_pattern(q)
        stmt = stmt.where(or_(MdParty.name.ilike(pattern, escape="\\"), MdParty.tax_code.ilike(pattern, escape="\\")))
    stmt = stmt.order_by(order_by_clause(params.sort, _SORTABLE), MdParty.id)
    return paginate(db, stmt, params)


def create_party(db: Session, data: PartyCreate, actor_id: int) -> MdParty:
    party = MdParty(
        **data.model_dump(mode="json"),
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(party)
    try:
        db.flush()  # lấy id cho nhật ký; trùng mã số thuế báo lỗi tại đây
    except IntegrityError as exc:
        db.rollback()
        raise _duplicate(exc) from exc
    _audit(db, "party.create", party, actor_id, after=audit.snapshot(party, PARTY_AUDIT_FIELDS))
    _commit(db)
    return party


def _has_accounts(db: Session, party_id: int, only_active: bool = False):
    cond = [SysUser.party_id == party_id]
    if only_active:
        cond.append(SysUser.is_active.is_(True))
    return db.scalar(select(exists().where(*cond)))


def update_party(db: Session, party_id: int, data: PartyUpdate, actor_id: int, expected_version: int | None) -> MdParty:
    party = get_party(db, party_id, for_update=True)
    _check_version(party, expected_version)
    if party.status == PartyStatus.ARCHIVED.value:
        raise _conflict("Khách hàng/đối tác đã lưu trữ; khôi phục trước khi sửa")
    changes = data.model_dump(mode="json", exclude_unset=True)
    new_type = changes.get("party_type", party.party_type)
    if party.party_type == PartyType.CUSTOMER.value and new_type != party.party_type:
        if _has_accounts(db, party.id):
            raise _conflict("Khách hàng đã có tài khoản customer; không đổi được loại đối tác")
        _run_guards(db, TYPE_CHANGE_GUARDS, party)
    try:
        check_tax_code(changes.get("tax_code", party.tax_code), changes.get("country_code", party.country_code))
    except ValueError as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            str(exc),
            [{"field": "body.tax_code", "issue": "invalid", "message": str(exc)}],
        ) from exc
    before = audit.snapshot(party, PARTY_AUDIT_FIELDS)
    for key, value in changes.items():
        setattr(party, key, value)
    party.updated_by = actor_id
    party.updated_at = now_utc()
    old, new = audit.changed(before, audit.snapshot(party, PARTY_AUDIT_FIELDS))
    if new:
        _audit(db, "party.update", party, actor_id, old, new)
    _commit(db)
    db.refresh(party)
    return party


def archive_party(db: Session, party_id: int, actor_id: int, expected_version: int | None) -> MdParty:
    party = get_party(db, party_id, for_update=True)
    _check_version(party, expected_version)
    if party.status == PartyStatus.ARCHIVED.value:
        raise _conflict("Khách hàng/đối tác đã lưu trữ")
    if _has_accounts(db, party.id, only_active=True):
        raise _conflict("Khách hàng còn tài khoản customer đang hoạt động; vô hiệu hóa tài khoản trước khi lưu trữ")
    _run_guards(db, ARCHIVE_GUARDS, party)
    party.status = PartyStatus.ARCHIVED.value
    party.archived_at = now_utc()
    party.archived_by = actor_id
    party.updated_by = actor_id
    party.updated_at = now_utc()
    _audit(db, "party.archive", party, actor_id, {"status": "active"}, {"status": "archived"})
    _commit(db)
    db.refresh(party)
    return party


def restore_party(db: Session, party_id: int, actor_id: int, expected_version: int | None) -> MdParty:
    party = get_party(db, party_id, for_update=True)
    _check_version(party, expected_version)
    if party.status == PartyStatus.ACTIVE.value:
        raise _conflict("Khách hàng/đối tác đang hoạt động")
    party.status = PartyStatus.ACTIVE.value
    party.archived_at = None
    party.archived_by = None
    party.updated_by = actor_id
    party.updated_at = now_utc()
    _audit(db, "party.restore", party, actor_id, {"status": "archived"}, {"status": "active"})
    _commit(db)
    db.refresh(party)
    return party


# --- Tài khoản customer của khách hàng (02 §9.2; 06 §4: manager tạo tài khoản customer) -----------------------


def create_customer_account(db: Session, party_id: int, data: CustomerAccountCreate, actor_id: int) -> SysUser:
    """Manager (06 §4) và customer admin (02 §9.2) tạo tài khoản customer. `admin` dùng `/api/v2/users` – không qua
    `CUSTOMER_ACCOUNT_GUARDS` (06 §4: `admin` toàn quyền người dùng)."""
    party = get_party(db, party_id)  # 404 nếu không có
    # Loại, trạng thái party trước (thông báo đúng nguyên nhân); identity_service kiểm lại khi khóa dòng.
    if party.party_type == PartyType.CUSTOMER.value and party.status == PartyStatus.ACTIVE.value:
        _run_guards(db, CUSTOMER_ACCOUNT_GUARDS, party, status_code=422)
    user_data = UserCreate(
        username=data.username,
        email=data.email,
        full_name=data.full_name,
        password=data.password,
        roles=[Role.CUSTOMER],
        party_id=party_id,
        is_customer_admin=data.is_customer_admin,
    )
    return identity_service.create_user(db, user_data, actor_id)


def list_customer_accounts(db: Session, party_id: int, params: PageParams, status: str) -> tuple[list[SysUser], int]:
    get_party(db, party_id)
    return identity_service.list_users(db, params, party_id=party_id, status=status)
