"""
API danh mục địa điểm `/api/v2/md/locations` (Giai đoạn 16; 01 §4).

Quyền: đọc – vai trò nội bộ (mọi địa điểm), `customer` (chỉ địa điểm đang hoạt động của khách hàng mình, khác → 404);
tạo / sửa / lưu trữ / khôi phục – `admin`, `manager`.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.core.versioning import etag, parse_if_match
from app.shared.identity.models import SysUser
from app.shared.masterdata import location_service as service
from app.shared.masterdata.location_schemas import LocationCreate, LocationOut, LocationUpdate
from app.shared.masterdata.models import LocationType

router = APIRouter(prefix="/api/v2/md/locations", tags=["Master data – Locations"])

_reader = require_roles(*Role)
_writer = require_roles(Role.ADMIN, Role.MANAGER)
_IF_MATCH = Header(None, description="ETag nhận từ GET; lệch → 409 VERSION_CONFLICT")


def _with_etag(response: Response, location) -> LocationOut:
    response.headers["ETag"] = etag(location.row_version)
    return location


@router.get("", response_model=Page[LocationOut], summary="Danh sách địa điểm (customer: địa điểm của mình)")
def list_locations(
    params: PageParams = Depends(),  # noqa: B008
    q: str | None = Query(None, min_length=1, max_length=100, description="Tìm theo mã, tên, địa chỉ"),
    location_type: LocationType | None = None,
    party_id: int | None = Query(None, gt=0),
    status_: Literal["active", "archived", "all"] = Query("active", alias="status"),
    user: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_locations(db, user, params, q, location_type, party_id, status_)
    return Page[LocationOut](
        items=[LocationOut.model_validate(x) for x in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post("", response_model=LocationOut, status_code=status.HTTP_201_CREATED, summary="Thêm địa điểm")
def create_location(
    body: LocationCreate,
    response: Response,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    location = service.create_location(db, body, actor.id)
    response.headers["Location"] = f"/api/v2/md/locations/{location.id}"
    return _with_etag(response, location)


@router.get("/{location_id:int}", response_model=LocationOut, summary="Chi tiết địa điểm")
def get_location(
    location_id: int,
    response: Response,
    user: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _with_etag(response, service.get_location(db, location_id, user))


@router.patch("/{location_id:int}", response_model=LocationOut, summary="Cập nhật (JSON Merge Patch; không đổi mã)")
def update_location(
    location_id: int,
    body: LocationUpdate,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _with_etag(response, service.update_location(db, location_id, body, actor.id, parse_if_match(if_match)))


@router.post("/{location_id:int}:archive", response_model=LocationOut, summary="Lưu trữ địa điểm")
def archive_location(
    location_id: int,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    location = service.set_archived(db, location_id, True, actor.id, parse_if_match(if_match))
    return _with_etag(response, location)


@router.post("/{location_id:int}:restore", response_model=LocationOut, summary="Khôi phục địa điểm")
def restore_location(
    location_id: int,
    response: Response,
    if_match: str | None = _IF_MATCH,
    actor: SysUser = Depends(_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    location = service.set_archived(db, location_id, False, actor.id, parse_if_match(if_match))
    return _with_etag(response, location)
