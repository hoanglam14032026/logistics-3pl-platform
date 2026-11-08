"""
API danh mục khách hàng, đối tác `/api/v2/md/parties` (06 §1, §4; 01 §4).

Quyền (06 §4 – danh mục `md_*`): đọc – mọi vai trò nội bộ; ghi – `manager`, `admin`; `customer` không truy cập.
Tài khoản `customer` của khách hàng: `manager`, `admin` tạo và xem (06 §4 – "Người dùng, vai trò").
"""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import INTERNAL_ROLES, Role, require_roles
from app.core.versioning import etag, parse_if_match
from app.shared.identity.models import SysUser
from app.shared.identity.schemas import CustomerAccountCreate, InvitationQueued, UserOut
from app.shared.masterdata import service
from app.shared.masterdata.models import PartyType
from app.shared.masterdata.schemas import PartyCreate, PartyOut, PartyUpdate

router = APIRouter(prefix="/api/v2/md/parties", tags=["Master data – Parties"])

_reader = require_roles(*INTERNAL_ROLES)
_writer = require_roles(Role.MANAGER, Role.ADMIN)

_IF_MATCH = Header(None, description="ETag nhận từ GET; lệch → 409 VERSION_CONFLICT")


def _with_etag(response: Response, party) -> PartyOut:
    response.headers["ETag"] = etag(party.row_version)
    return party


@router.get("", response_model=Page[PartyOut], summary="Danh sách khách hàng, đối tác")
def list_parties(
    params: PageParams = Depends(),  # noqa: B008
    q: str | None = Query(None, min_length=1, max_length=100, description="Tìm theo tên, mã số thuế"),
    party_type: PartyType | None = None,
    country_code: str | None = Query(None, pattern=r"^[A-Za-z]{2}$"),
    status_: Literal["active", "archived", "all"] = Query("active", alias="status"),
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_parties(db, params, q, party_type, country_code, status_)
    return Page[PartyOut](
        items=[PartyOut.model_validate(p) for p in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post("", response_model=PartyOut, status_code=status.HTTP_201_CREATED, summary="Thêm khách hàng, đối tác")
def create_party(
    body: PartyCreate,
    response: Response,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    party = service.create_party(db, body, actor.id)
    response.headers["Location"] = f"/api/v2/md/parties/{party.id}"
    return _with_etag(response, party)


@router.get("/{party_id:int}", response_model=PartyOut, summary="Chi tiết khách hàng, đối tác")
def get_party(
    party_id: int,
    response: Response,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _with_etag(response, service.get_party(db, party_id))


@router.patch("/{party_id:int}", response_model=PartyOut, summary="Cập nhật (JSON Merge Patch)")
def update_party(
    party_id: int,
    body: PartyUpdate,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _with_etag(response, service.update_party(db, party_id, body, actor.id, parse_if_match(if_match)))


@router.post("/{party_id:int}:archive", response_model=PartyOut, summary="Lưu trữ (ẩn khỏi danh sách mặc định)")
def archive_party(
    party_id: int,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _with_etag(response, service.archive_party(db, party_id, actor.id, parse_if_match(if_match)))


@router.post("/{party_id:int}:restore", response_model=PartyOut, summary="Khôi phục từ lưu trữ")
def restore_party(
    party_id: int,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _with_etag(response, service.restore_party(db, party_id, actor.id, parse_if_match(if_match)))


@router.get("/{party_id:int}/users", response_model=Page[UserOut], summary="Tài khoản customer của khách hàng")
def list_customer_accounts(
    party_id: int,
    params: PageParams = Depends(),  # noqa: B008
    status_: Literal["active", "inactive", "all"] = Query("all", alias="status"),
    _: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_customer_accounts(db, party_id, params, status_)
    return Page[UserOut](
        items=[UserOut.model_validate(u) for u in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post(
    "/{party_id:int}/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo tài khoản customer, gửi thư mời đặt mật khẩu (vai trò luôn là customer)",
    description="Thư mời gửi nền sau khi trả response; gửi lỗi ghi nhật ký `user.mail_failed`.",
)
def create_customer_account(
    party_id: int,
    body: CustomerAccountCreate,
    response: Response,
    tasks: BackgroundTasks,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    user = service.create_customer_account(db, party_id, body, actor.id, tasks)
    response.headers["Location"] = f"/api/v2/users/{user.id}"
    response.headers["ETag"] = etag(user.row_version)
    return user


@router.post(
    "/{party_id:int}/users/{user_id:int}:resend-invite",
    response_model=InvitationQueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Gửi lại thư mời (tài khoản chưa đặt mật khẩu)",
)
def resend_invitation(
    party_id: int,
    user_id: int,
    tasks: BackgroundTasks,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    service.get_party(db, party_id)
    service.resend_invitation(db, party_id, user_id, actor.id, tasks)
    return InvitationQueued()
