"""
Customer admin quản lý tài khoản của **chính khách hàng mình** qua cổng: `/api/v2/portal/users` (02 §9.2; 06 §1, §4 –
`is_customer_admin`: tài khoản cùng khách hàng).

- Chỉ tài khoản `customer` có `is_customer_admin` (khác → 403; kể cả admin nội bộ: cổng chỉ cho `customer`).
- Mời (tạo) tài khoản `customer` cùng khách hàng; không cấp được `is_customer_admin` (chỉ manager/admin).
- Khóa (vô hiệu hóa) tài khoản **khác** cùng khách hàng – thu hồi mọi phiên của tài khoản đó.
- Tài khoản của khách hàng khác, tài khoản nội bộ: trả 404 như không tồn tại (06 §2.1 – không lộ sự tồn tại).
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, forbidden, require_roles
from app.core.versioning import etag
from app.shared.identity import service
from app.shared.identity.models import SysUser
from app.shared.identity.schemas import (
    CustomerAccountCreate,
    InvitationResult,
    InvitedUserOut,
    PortalAccountCreate,
    UserOut,
)

router = APIRouter(prefix="/api/v2/portal/users", tags=["Portal – Users"])

_customer = require_roles(Role.CUSTOMER)


def get_customer_admin(user: SysUser = Depends(_customer)) -> SysUser:  # noqa: B008
    if not user.is_customer_admin or user.party_id is None:
        raise forbidden("Chỉ tài khoản quản trị của khách hàng (is_customer_admin) được quản lý tài khoản")
    return user


def _same_party_account(db: Session, actor: SysUser, user_id: int) -> SysUser:
    user = db.get(SysUser, user_id)
    if user is None or user.party_id != actor.party_id or Role.CUSTOMER.value not in user.role_codes:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy người dùng")
    return user


@router.get("", response_model=Page[UserOut], summary="Tài khoản của khách hàng mình")
def list_accounts(
    params: PageParams = Depends(),  # noqa: B008
    status_: Literal["active", "inactive", "all"] = Query("all", alias="status"),
    actor: SysUser = Depends(get_customer_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_users(db, params, party_id=actor.party_id, status=status_)
    return Page[UserOut](
        items=[UserOut.model_validate(u) for u in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post(
    "",
    response_model=InvitedUserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Mời tài khoản mới (thư mời qua email)",
)
def invite_account(
    body: PortalAccountCreate,
    response: Response,
    actor: SysUser = Depends(get_customer_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    from app.shared.masterdata import service as md_service  # masterdata phụ thuộc identity – import trong hàm

    data = CustomerAccountCreate(**body.model_dump(), is_customer_admin=False)
    user, sent = md_service.create_customer_account(db, actor.party_id, data, actor.id)
    response.headers["Location"] = f"/api/v2/portal/users/{user.id}"
    response.headers["ETag"] = etag(user.row_version)
    return InvitedUserOut.model_validate(user).model_copy(update={"invitation_sent": sent})


@router.post("/{user_id:int}:resend-invite", response_model=InvitationResult, summary="Gửi lại thư mời")
def resend_invitation(
    user_id: int,
    actor: SysUser = Depends(get_customer_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    from app.shared.masterdata import service as md_service

    _same_party_account(db, actor, user_id)
    return InvitationResult(invitation_sent=md_service.resend_invitation(db, actor.party_id, user_id, actor.id))


@router.post("/{user_id:int}:deactivate", response_model=UserOut, summary="Khóa tài khoản khác cùng khách hàng")
def deactivate_account(
    user_id: int,
    actor: SysUser = Depends(get_customer_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    user = _same_party_account(db, actor, user_id)
    if user.id == actor.id:
        raise forbidden("Không thể tự khóa tài khoản của mình")
    return service.deactivate_user(db, user, actor.id)
