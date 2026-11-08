"""
Người dùng, vai trò, refresh token (01 §4: `sys_users`, `sys_user_roles`; 06 §3; bước 0.6).

2FA: chỉ có sẵn cột `totp_secret` (NULL) và `is_2fa_enabled` (`false`); luồng xác thực 2 lớp làm cùng cổng
khách hàng (Giai đoạn 2). `totp_secret` không bao giờ trả ra API.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import AuditColumnsMixin, Base
from app.core.rbac import Role
from app.core.timeutil import now_utc
from app.shared.masterdata.models import MdParty  # noqa: F401  (đăng ký bảng md_parties trước khóa ngoại)

_ROLE_VALUES = ", ".join(f"'{r.value}'" for r in Role)


class SysUser(AuditColumnsMixin, Base):
    __tablename__ = "sys_users"
    __table_args__ = (
        CheckConstraint("username = lower(username)", name="username_lowercase"),
        CheckConstraint("email IS NULL OR email = lower(email)", name="email_lowercase"),
        CheckConstraint("failed_login_count >= 0", name="failed_login_count_non_negative"),
        CheckConstraint("NOT is_customer_admin OR party_id IS NOT NULL", name="customer_admin_has_party"),
        # totp_secret chỉ chứa bản mã Fernet (Giai đoạn 6) – chặn ghi nhầm dạng rõ
        CheckConstraint("totp_secret IS NULL OR totp_secret LIKE 'gAAAAA%'", name="totp_secret_encrypted"),
    )

    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Access token phát trước thời điểm này bị từ chối (đổi/đặt lại mật khẩu).
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    # Khóa tạm sau nhiều lần đăng nhập sai (06 §3)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Tài khoản `customer` (02 §9.2): gắn đúng một khách hàng; `is_customer_admin` quản lý tài khoản cùng khách hàng.
    # Tài khoản nội bộ: party_id NULL. Ràng buộc vai trò ↔ party kiểm tra trong service (khác bảng, không CHECK được).
    party_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("md_parties.id", ondelete="RESTRICT"), index=True
    )
    is_customer_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    # Xác thực 2 lớp TOTP (Giai đoạn 5). totp_secret: bản mã Fernet của secret Base32 (Giai đoạn 6,
    # `app.core.secretbox`, khóa TOTP_ENCRYPTION_KEY), có giá trị từ lúc thiết lập (chưa bật). Không bao giờ trả ra API
    # (secret dạng rõ chỉ trả một lần lúc thiết lập).
    totp_secret: Mapped[str | None] = mapped_column(String(255))
    is_2fa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Chu kỳ 30 giây của mã đã dùng gần nhất – chặn dùng lại cùng một mã
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)

    roles: Mapped[list["SysUserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin", order_by="SysUserRole.role"
    )

    @property
    def role_codes(self) -> list[str]:
        return sorted(r.role for r in self.roles)

    def is_locked(self, at: datetime) -> bool:
        return self.locked_until is not None and self.locked_until > at


class SysUserRole(Base):
    """Một người dùng có thể có nhiều vai trò (00 §4)."""

    __tablename__ = "sys_user_roles"
    __table_args__ = (CheckConstraint(f"role IN ({_ROLE_VALUES})", name="role_valid"),)

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger)

    user: Mapped[SysUser] = relationship(back_populates="roles")


class SysRefreshToken(Base):
    """Refresh token (06 §3: 7 ngày, thu hồi được). Lưu SHA-256, không lưu token gốc.

    Xoay vòng: mỗi lần làm mới, token cũ bị thu hồi (`rotated`) và token mới cùng `family_id`. Dùng lại token đã
    xoay vòng (dấu hiệu bị đánh cắp) → thu hồi cả họ token (`reuse_detected`).
    """

    __tablename__ = "sys_refresh_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # rotated | logout | reuse_detected | password_changed | deactivated
    revoked_reason: Mapped[str | None] = mapped_column(String(32))
    replaced_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_refresh_tokens.id"))
    created_ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    # Phiên đã qua xác thực 2 lớp – access token cấp lại khi làm mới giữ `amr` = pwd + otp
    mfa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    user: Mapped[SysUser] = relationship()


class TokenPurpose(StrEnum):
    INVITE = "invite"  # thư mời: đặt mật khẩu lần đầu
    PASSWORD_RESET = "password_reset"  # quên mật khẩu


class SysUserToken(Base):
    """Token dùng một lần gửi qua email: chỉ lưu SHA-256, có hạn, `used_at` khi đã dùng. Cấp token mới cùng mục đích
    → token cũ chưa dùng bị vô hiệu (`revoked_at`)."""

    __tablename__ = "sys_user_tokens"
    __table_args__ = (
        CheckConstraint(f"purpose IN ({', '.join(repr(p.value) for p in TokenPurpose)})", name="purpose_valid"),
        Index("ix_sys_user_tokens_user_purpose", "user_id", "purpose"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_users.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[SysUser] = relationship()
