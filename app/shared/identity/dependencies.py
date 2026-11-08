"""Dependency xác thực: lấy người dùng hiện tại từ header `Authorization: Bearer <access token>`."""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import decode_access_token, unauthenticated
from app.shared.identity.models import SysUser
from app.shared.identity.service import password_stamp

# auto_error=False: thiếu header thì tự trả lỗi theo định dạng thống nhất (06 §2.2), không dùng lỗi mặc định.
_bearer = HTTPBearer(auto_error=False, description="Access token từ POST /api/v2/auth/login")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> SysUser:
    """Kiểm tra token rồi đọc lại người dùng từ CSDL ở mỗi request: tài khoản bị vô hiệu hóa, đổi mật khẩu
    có hiệu lực ngay, không chờ access token hết hạn. Vai trò lấy từ CSDL (không tin claim `roles` trong token)."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthenticated()
    claims = decode_access_token(credentials.credentials)
    user = db.get(SysUser, int(claims["sub"]))
    if user is None or not user.is_active:
        raise unauthenticated()
    # Khớp chính xác (micro giây) – không so `iat` (giây nguyên: token phát cùng giây trước khi đổi vẫn lọt).
    if claims.get("pwd_at") != password_stamp(user):
        raise unauthenticated("Mật khẩu đã thay đổi; đăng nhập lại")
    return user
