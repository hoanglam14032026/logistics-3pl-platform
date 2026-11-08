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

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.pagination import PageParams, order_by_clause, paginate
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
from app.shared.identity.models import SysRefreshToken, SysUser, SysUserRole
from app.shared.identity.schemas import UserCreate, UserUpdate

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


# --- Đăng nhập, làm mới, đăng xuất ------------------------------------------------------------------------


def login(db: Session, username: str, password: str, client: ClientInfo, settings: Settings | None = None):
    settings = settings or get_settings()
    now = now_utc()
    user = db.scalar(select(SysUser).where(SysUser.username == username.lower()).with_for_update())

    if user is not None and user.is_locked(now):
        verify_password(None, password)  # giữ thời gian xử lý như các trường hợp khác
        raise unauthenticated(ACCOUNT_LOCKED)

    if not verify_password(user.password_hash if user else None, password):
        if user is not None:
            user.failed_login_count += 1
            just_locked = user.failed_login_count >= settings.auth_max_failed_logins
            if just_locked:
                user.locked_until = now + timedelta(minutes=settings.auth_lockout_minutes)
                user.failed_login_count = 0  # hết hạn khóa: được thêm đủ N lần thử
            db.commit()
            if just_locked:
                raise unauthenticated(ACCOUNT_LOCKED)
        raise unauthenticated(INVALID_CREDENTIALS)

    if not user.is_active:
        db.rollback()
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


def _like_pattern(text: str) -> str:
    """Tìm gần đúng: escape ký tự đại diện của LIKE trong chuỗi người dùng nhập."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
) -> tuple[list[SysUser], int]:
    stmt = select(SysUser)
    if status == "active":
        stmt = stmt.where(SysUser.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(SysUser.is_active.is_(False))
    if role is not None:
        stmt = stmt.where(SysUser.roles.any(SysUserRole.role == role.value))
    if q:
        pattern = _like_pattern(q)
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
    user = SysUser(
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
    if "password" in fields:
        _set_password(db, user, data.password, actor.id)
    user.updated_by = actor.id
    user.updated_at = now_utc()  # luôn có UPDATE trên sys_users → row_version tăng kể cả khi chỉ đổi roles
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _duplicate_error(exc) from exc
    db.refresh(user)
    return user


def unlock_user(db: Session, user: SysUser, actor: SysUser) -> SysUser:
    user.failed_login_count = 0
    user.locked_until = None
    user.updated_by = actor.id
    user.updated_at = now_utc()
    db.commit()
    db.refresh(user)
    return user
