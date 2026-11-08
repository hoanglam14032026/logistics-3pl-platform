"""
Nghiệp vụ xác thực và quản lý người dùng (06 §3, §4; bước 0.6).

Đăng nhập:
- Sai mật khẩu → tăng `failed_login_count`; tới `AUTH_MAX_FAILED_LOGINS` (5) lần liên tiếp → khóa tạm
  `AUTH_LOCKOUT_MINUTES` (15) phút. Đang khóa thì mật khẩu đúng cũng bị từ chối. Đăng nhập đúng → đặt lại bộ đếm.
- Dòng `sys_users` được khóa `SELECT … FOR UPDATE` trong lúc kiểm tra: nhiều request song song không vượt được ngưỡng.
- Tài khoản không tồn tại và sai mật khẩu trả cùng thông báo, cùng thời gian xử lý (chống dò tài khoản).

Refresh token: xoay vòng mỗi lần làm mới; dùng lại token đã xoay vòng → thu hồi cả họ token.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.config import Settings, get_settings
from app.core.db import SessionLocal, engine
from app.core.errors import AppError, ErrorCode
from app.core.pagination import PageParams, like_pattern, order_by_clause, paginate
from app.core.rbac import Role, forbidden
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    password_needs_rehash,
    unauthenticated,
    verify_password,
)
from app.core.timeutil import now_utc
from app.shared.audit import service as audit
from app.shared.identity.models import SysRefreshToken, SysUser, SysUserRole
from app.shared.identity.schemas import UserCreate, UserUpdate
from app.shared.masterdata.models import MdParty, PartyStatus, PartyType

INVALID_CREDENTIALS = "Sai tên đăng nhập hoặc mật khẩu"
ACCOUNT_LOCKED = "Tài khoản tạm khóa do đăng nhập sai nhiều lần; thử lại sau"
ACCOUNT_DISABLED = "Tài khoản đã bị vô hiệu hóa"


@dataclass(frozen=True)
class ClientInfo:
    ip: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_in: int


# --- Token ------------------------------------------------------------------------------------------------


def password_stamp(user: SysUser) -> int:
    """Dấu thời gian đổi mật khẩu (micro giây) – ghi vào access token, phải khớp CSDL khi dùng token."""
    return int(user.password_changed_at.timestamp() * 1_000_000)


def _issue_tokens(
    db: Session,
    user: SysUser,
    client: ClientInfo,
    settings: Settings,
    family_id: uuid.UUID | None = None,
) -> tuple[IssuedTokens, SysRefreshToken]:
    access, expires_in = create_access_token(
        user.id, user.role_codes, settings, extra_claims={"pwd_at": password_stamp(user)}
    )
    raw_refresh = generate_refresh_token()
    ttl = timedelta(days=settings.jwt_refresh_ttl_days)
    record = SysRefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(raw_refresh),
        family_id=family_id or uuid.uuid4(),
        expires_at=now_utc() + ttl,
        created_ip=client.ip,
        user_agent=(client.user_agent or "")[:255] or None,
    )
    db.add(record)
    db.flush()
    return IssuedTokens(access, expires_in, raw_refresh, int(ttl.total_seconds())), record


def _revoke(db: Session, reason: str, *conditions) -> None:
    db.execute(
        update(SysRefreshToken)
        .where(SysRefreshToken.revoked_at.is_(None), *conditions)
        .values(revoked_at=now_utc(), revoked_reason=reason)
    )


def revoke_all_user_tokens(db: Session, user_id: int, reason: str) -> None:
    _revoke(db, reason, SysRefreshToken.user_id == user_id)


# Trường của người dùng được ghi vào nhật ký (không có password_hash, totp_secret).
USER_AUDIT_FIELDS = ("username", "email", "full_name", "role_codes", "is_active", "party_id", "is_customer_admin")


def _audit_user(db: Session, action: str, user: SysUser, actor_id: int | None, before=None, after=None) -> None:
    audit.record(db, action, "user", user.id, actor_id, before, after)


# --- Giới hạn tần suất, đăng nhập, làm mới, đăng xuất ------------------------------------------------------


def check_login_rate(username: str, ip: str | None, settings: Settings | None = None) -> None:
    """Giới hạn POST /api/v2/auth/login theo IP và theo tên đăng nhập (06 §3) – kiểm tra TRƯỚC khi băm mật khẩu
    (chặn cả việc làm quá tải CPU bằng Argon2). Lần đầu vượt ngưỡng trong cửa sổ được ghi nhật ký."""
    settings = settings or get_settings()
    window = settings.auth_login_rate_window_sec
    hits = [("login_ip", ip or "unknown", settings.auth_login_rate_limit_per_ip)]
    hits.append(("login_username", username.strip().lower(), settings.auth_login_rate_limit_per_username))
    results = [(scope, key, rate_limit.hit(engine, scope, key, limit, window)) for scope, key, limit in hits]
    exceeded = [(scope, key, h) for scope, key, h in results if h.exceeded]
    if not exceeded:
        return
    first = [(scope, key) for scope, key, h in exceeded if h.first_exceeded]
    if first:
        with SessionLocal() as log_db:
            for scope, key in first:
                audit.record(log_db, "auth.rate_limited", "rate_limit", None, None, after={"scope": scope, "key": key})
            log_db.commit()
    raise rate_limit.too_many_requests(max(h.retry_after for _, _, h in exceeded))


def _login_failed(db: Session, user: SysUser | None, username: str, reason: str) -> None:
    audit.record(
        db, "auth.login_failed", "user", user.id if user else None, None, after={"username": username, "reason": reason}
    )
    db.commit()


def login(db: Session, username: str, password: str, client: ClientInfo, settings: Settings | None = None):
    settings = settings or get_settings()
    now = now_utc()
    username = username.lower()
    user = db.scalar(select(SysUser).where(SysUser.username == username).with_for_update())

    if user is not None and user.is_locked(now):
        verify_password(None, password)  # giữ thời gian xử lý như các trường hợp khác
        _login_failed(db, user, username, "locked")
        raise unauthenticated(ACCOUNT_LOCKED)

    if not verify_password(user.password_hash if user else None, password):
        just_locked = False
        if user is not None:
            user.failed_login_count += 1
            just_locked = user.failed_login_count >= settings.auth_max_failed_logins
            if just_locked:
                user.locked_until = now + timedelta(minutes=settings.auth_lockout_minutes)
                user.failed_login_count = 0  # hết hạn khóa: được thêm đủ N lần thử
                _audit_user(
                    db,
                    "user.lock",
                    user,
                    None,
                    after={"locked_until": user.locked_until, "failed_attempts": settings.auth_max_failed_logins},
                )
        _login_failed(db, user, username, "invalid_password" if user else "unknown_user")
        raise unauthenticated(ACCOUNT_LOCKED if just_locked else INVALID_CREDENTIALS)

    if not user.is_active:
        _login_failed(db, user, username, "disabled")
        raise forbidden(ACCOUNT_DISABLED)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    tokens, _ = _issue_tokens(db, user, client, settings)
    db.commit()
    return tokens


def refresh(db: Session, raw_token: str, client: ClientInfo, settings: Settings | None = None) -> IssuedTokens:
    settings = settings or get_settings()
    now = now_utc()
    record = db.scalar(
        select(SysRefreshToken).where(SysRefreshToken.token_hash == hash_refresh_token(raw_token)).with_for_update()
    )
    if record is None:
        raise unauthenticated()

    if record.revoked_at is not None:
        if record.revoked_reason == "rotated":
            # Token đã đổi sang token mới mà vẫn bị dùng lại: có thể đã bị đánh cắp – thu hồi cả họ.
            _revoke(db, "reuse_detected", SysRefreshToken.family_id == record.family_id)
            audit.record(db, "auth.refresh_reuse_detected", "user", record.user_id, None)
            db.commit()
        raise unauthenticated()

    if record.expires_at <= now:
        raise unauthenticated("Phiên đăng nhập đã hết hạn")

    user = record.user
    if not user.is_active:
        _revoke(db, "deactivated", SysRefreshToken.family_id == record.family_id)
        db.commit()
        raise unauthenticated()

    tokens, new_record = _issue_tokens(db, user, client, settings, family_id=record.family_id)
    record.revoked_at = now
    record.revoked_reason = "rotated"
    record.replaced_by_id = new_record.id
    db.commit()
    return tokens


def logout(db: Session, raw_token: str) -> None:
    """Thu hồi phiên (cả họ token). Token không tồn tại/đã thu hồi: không báo lỗi (idempotent, không lộ thông tin)."""
    record = db.scalar(select(SysRefreshToken).where(SysRefreshToken.token_hash == hash_refresh_token(raw_token)))
    if record is not None:
        _revoke(db, "logout", SysRefreshToken.family_id == record.family_id)
        db.commit()


def _set_password(db: Session, user: SysUser, new_password: str, actor_id: int | None) -> None:
    user.password_hash = hash_password(new_password)
    user.password_changed_at = now_utc()
    user.updated_by = actor_id
    revoke_all_user_tokens(db, user.id, "password_changed")


def change_password(db: Session, user: SysUser, current_password: str, new_password: str) -> None:
    if not verify_password(user.password_hash, current_password):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            "Mật khẩu hiện tại không đúng",
            [{"field": "body.current_password", "issue": "invalid", "message": "Mật khẩu hiện tại không đúng"}],
        )
    _set_password(db, user, new_password, user.id)
    _audit_user(db, "user.password_change", user, user.id)
    db.commit()


# --- Quản lý người dùng (admin) ---------------------------------------------------------------------------

_SORTABLE = {
    "created_at": SysUser.created_at,
    "username": SysUser.username,
    "full_name": SysUser.full_name,
    "last_login_at": SysUser.last_login_at,
}


def _duplicate_error(exc: IntegrityError) -> AppError:
    text = str(exc.orig)
    field = "email" if "email" in text else "username"
    message = "Email đã được dùng" if field == "email" else "Tên đăng nhập đã tồn tại"
    return AppError(
        ErrorCode.DUPLICATE, 409, message, [{"field": f"body.{field}", "issue": "duplicate", "message": message}]
    )


def _field_error(field: str, message: str, issue: str = "invalid") -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, message, [{"field": f"body.{field}", "issue": issue, "message": message}]
    )


def _check_customer_link(db: Session, roles: list[str], party_id: int | None, is_customer_admin: bool) -> None:
    """02 §9.2: tài khoản `customer` gắn đúng một khách hàng (party loại `customer`, đang hoạt động) và không kèm
    vai trò nội bộ (BR-111 – tách dữ liệu khách hàng); tài khoản nội bộ không gắn party.

    Party được khóa `FOR SHARE` tới hết giao dịch: không thể lưu trữ party trong lúc đang tạo tài khoản cho nó.
    """
    if Role.CUSTOMER.value not in roles:
        if party_id is not None:
            raise _field_error("party_id", "Chỉ tài khoản customer mới gắn khách hàng (party_id)")
        if is_customer_admin:
            raise _field_error("is_customer_admin", "Chỉ tài khoản customer mới có is_customer_admin")
        return
    if len(roles) > 1:
        raise _field_error("roles", "Tài khoản customer không được kèm vai trò khác")
    if party_id is None:
        raise _field_error("party_id", "Tài khoản customer phải gắn một khách hàng (party_id)", "missing")
    party = db.scalar(select(MdParty).where(MdParty.id == party_id).with_for_update(read=True))
    if party is None:
        raise _field_error("party_id", "Không tìm thấy khách hàng", "not_found")
    if party.party_type != PartyType.CUSTOMER.value:
        raise _field_error("party_id", "Đối tác không phải khách hàng (party_type ≠ customer)")
    if party.status != PartyStatus.ACTIVE.value:
        raise _field_error("party_id", "Khách hàng đã lưu trữ")


def get_user(db: Session, user_id: int) -> SysUser:
    user = db.get(SysUser, user_id)
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy người dùng")
    return user


def list_users(
    db: Session,
    params: PageParams,
    q: str | None = None,
    role: Role | None = None,
    status: str = "active",
    party_id: int | None = None,
) -> tuple[list[SysUser], int]:
    stmt = select(SysUser)
    if party_id is not None:
        stmt = stmt.where(SysUser.party_id == party_id)
    if status == "active":
        stmt = stmt.where(SysUser.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(SysUser.is_active.is_(False))
    if role is not None:
        stmt = stmt.where(SysUser.roles.any(SysUserRole.role == role.value))
    if q:
        pattern = like_pattern(q)
        stmt = stmt.where(
            or_(
                SysUser.username.ilike(pattern, escape="\\"),
                SysUser.email.ilike(pattern, escape="\\"),
                SysUser.full_name.ilike(pattern, escape="\\"),
            )
        )
    stmt = stmt.order_by(order_by_clause(params.sort, _SORTABLE), SysUser.id)
    return paginate(db, stmt, params)


def create_user(db: Session, data: UserCreate, actor_id: int | None) -> SysUser:
    _check_customer_link(db, [r.value for r in data.roles], data.party_id, data.is_customer_admin)
    user = SysUser(
        party_id=data.party_id,
        is_customer_admin=data.is_customer_admin,
        username=data.username,
        email=data.email,
        full_name=data.full_name,
        password_hash=hash_password(data.password),
        is_active=data.is_active,
        created_by=actor_id,
        updated_by=actor_id,
        roles=[SysUserRole(role=r.value, created_by=actor_id) for r in data.roles],
    )
    db.add(user)
    try:
        db.flush()  # lấy id cho nhật ký; trùng username/email báo lỗi tại đây
        _audit_user(db, "user.create", user, actor_id, after=audit.snapshot(user, USER_AUDIT_FIELDS))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _duplicate_error(exc) from exc
    return user


def _check_version(user: SysUser, expected_version: int | None) -> None:
    if expected_version is not None and expected_version != user.row_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, 409, "Bản ghi đã bị người khác thay đổi; tải lại rồi thử lại")


def update_user(db: Session, user: SysUser, data: UserUpdate, actor: SysUser, expected_version: int | None) -> SysUser:
    _check_version(user, expected_version)
    fields = data.model_fields_set
    is_self = user.id == actor.id
    if is_self and "is_active" in fields and data.is_active is False:
        raise forbidden("Không thể tự vô hiệu hóa tài khoản của mình")
    if is_self and "roles" in fields and Role.ADMIN not in data.roles:
        raise forbidden("Không thể tự bỏ vai trò admin của mình")
    is_customer = Role.CUSTOMER.value in user.role_codes
    if "roles" in fields and (Role.CUSTOMER in data.roles) != is_customer:
        raise _field_error("roles", "Không đổi được tài khoản nội bộ ↔ customer; tạo tài khoản mới")
    if "roles" in fields and is_customer and len(data.roles) > 1:
        raise _field_error("roles", "Tài khoản customer không được kèm vai trò khác")
    if "is_customer_admin" in fields and not is_customer:
        raise _field_error("is_customer_admin", "Chỉ tài khoản customer mới có is_customer_admin")

    before = audit.snapshot(user, USER_AUDIT_FIELDS)
    if "email" in fields:
        user.email = data.email
    if "full_name" in fields:
        user.full_name = data.full_name
    if "roles" in fields:
        current = {r.role: r for r in user.roles}
        wanted = {r.value for r in data.roles}
        user.roles = [current.get(code) or SysUserRole(role=code, created_by=actor.id) for code in sorted(wanted)]
    if "is_active" in fields and data.is_active != user.is_active:
        user.is_active = data.is_active
        if not data.is_active:
            revoke_all_user_tokens(db, user.id, "deactivated")
    if "is_customer_admin" in fields:
        user.is_customer_admin = data.is_customer_admin
    if "password" in fields:
        _set_password(db, user, data.password, actor.id)
    user.updated_by = actor.id
    user.updated_at = now_utc()  # luôn có UPDATE trên sys_users → row_version tăng kể cả khi chỉ đổi roles
    _audit_user_update(db, user, actor.id, before, password_reset="password" in fields)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _duplicate_error(exc) from exc
    db.refresh(user)
    return user


def _audit_user_update(db: Session, user: SysUser, actor_id: int, before: dict, password_reset: bool) -> None:
    """Tách hành động có ý nghĩa riêng (khóa/mở tài khoản, đặt lại mật khẩu) khỏi sửa thông tin thường."""
    old, new = audit.changed(before, audit.snapshot(user, USER_AUDIT_FIELDS))
    if "is_active" in new:
        action = "user.activate" if new.pop("is_active") else "user.deactivate"
        _audit_user(db, action, user, actor_id, {"is_active": old.pop("is_active")}, {"is_active": user.is_active})
    if new:
        _audit_user(db, "user.update", user, actor_id, old, new)
    if password_reset:
        _audit_user(db, "user.password_reset", user, actor_id)


def deactivate_user(db: Session, user: SysUser, actor_id: int) -> SysUser:
    """Vô hiệu hóa tài khoản và thu hồi mọi phiên (dùng chung: admin, customer admin)."""
    if user.is_active:
        user.is_active = False
        user.updated_by = actor_id
        user.updated_at = now_utc()
        revoke_all_user_tokens(db, user.id, "deactivated")
        _audit_user(db, "user.deactivate", user, actor_id, {"is_active": True}, {"is_active": False})
        db.commit()
        db.refresh(user)
    return user


def unlock_user(db: Session, user: SysUser, actor: SysUser) -> SysUser:
    _audit_user(
        db,
        "user.unlock",
        user,
        actor.id,
        audit.snapshot(user, ("locked_until", "failed_login_count")),
        {"locked_until": None, "failed_login_count": 0},
    )
    user.failed_login_count = 0
    user.locked_until = None
    user.updated_by = actor.id
    user.updated_at = now_utc()
    db.commit()
    db.refresh(user)
    return user
