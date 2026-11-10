"""
API sổ thu – chi theo hợp đồng – Giai đoạn 20 (02 §3.10).

- Khoản mục `/api/v2/ops/cashbook-categories`: đọc – nội bộ; thêm khoản mục con, xóa khoản mục con chưa có phiếu –
  `manager`.
- Phiếu `/api/v2/ops/cash-vouchers`: lập nháp, tải chứng từ, gửi – `ops_staff`, `manager`; duyệt phiếu chi, ghi sổ,
  trả lại, hủy (void) – `manager`, `business_manager` (khác người lập).
- Số dư quỹ `/api/v2/ops/cashbook/balance`, sổ chi tiết theo hợp đồng `/api/v2/ops/cashbook/ledger` (BC-TC-01).
Đọc: `ops_staff`, `manager`, `business_manager`, `admin`.
"""

from datetime import date

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.flows.operations import cashbook_service as cb
from app.flows.operations.cashbook_schemas import (
    BalanceOut,
    CategoryCreate,
    CategoryOut,
    FileOut,
    LedgerOut,
    NoteIn,
    ReasonIn,
    VoucherCreate,
    VoucherOut,
)
from app.flows.operations.models import CashDirection, VoucherStatus
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops", tags=["Ops – Cashbook"])

_reader = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.ADMIN)
_drafter = require_roles(Role.OPS_STAFF, Role.MANAGER)
_approver = require_roles(Role.MANAGER, Role.BUSINESS_MANAGER)
_manager = require_roles(Role.MANAGER)


@router.get("/cashbook-categories", response_model=list[CategoryOut], summary="Khoản mục thu – chi")
def list_categories(_: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    return cb.list_categories(db)


@router.post(
    "/cashbook-categories",
    response_model=CategoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Thêm khoản mục con",
)
def create_category(body: CategoryCreate, actor: SysUser = Depends(_manager), db: Session = Depends(get_db)):  # noqa: B008
    return cb.create_category(db, body, actor)


@router.delete(
    "/cashbook-categories/{code}", status_code=status.HTTP_204_NO_CONTENT, summary="Xóa khoản mục con chưa có phiếu"
)
def delete_category(code: str, actor: SysUser = Depends(_manager), db: Session = Depends(get_db)):  # noqa: B008
    cb.delete_category(db, code, actor)


@router.get("/cash-vouchers", response_model=Page[VoucherOut], summary="Danh sách phiếu thu / chi")
def list_vouchers(
    params: PageParams = Depends(),  # noqa: B008
    contract_id: int | None = Query(None, gt=0),
    status_: VoucherStatus | None = Query(None, alias="status"),
    voucher_type: CashDirection | None = None,
    category_code: str | None = Query(None, max_length=10),
    advance_id: int | None = Query(None, gt=0),
    date_from: date | None = None,
    date_to: date | None = None,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = cb.list_vouchers(
        db,
        params,
        contract_id,
        status_.value if status_ else None,
        voucher_type.value if voucher_type else None,
        category_code,
        advance_id,
        date_from,
        date_to,
    )
    return Page[VoucherOut](
        items=[VoucherOut.model_validate(v) for v in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post("/cash-vouchers", response_model=VoucherOut, status_code=status.HTTP_201_CREATED, summary="Lập phiếu nháp")
def create_voucher(body: VoucherCreate, actor: SysUser = Depends(_drafter), db: Session = Depends(get_db)):  # noqa: B008
    return cb.create_voucher(db, body, actor)


@router.get("/cash-vouchers/{voucher_id:int}", response_model=VoucherOut, summary="Chi tiết phiếu")
def get_voucher(voucher_id: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    return cb.get_voucher(db, voucher_id)


@router.post(
    "/cash-vouchers/{voucher_id:int}/files",
    response_model=FileOut,
    status_code=status.HTTP_201_CREATED,
    summary="Tải chứng từ gốc (PDF / ảnh) – phiếu nháp",
)
def attach_file(
    voucher_id: int,
    file: UploadFile = File(...),  # noqa: B008
    actor: SysUser = Depends(_drafter),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return cb.attach_file(db, voucher_id, file, actor)


@router.post("/cash-vouchers/{voucher_id:int}:submit", response_model=VoucherOut, summary="Gửi phiếu (người lập)")
def submit(voucher_id: int, actor: SysUser = Depends(_drafter), db: Session = Depends(get_db)):  # noqa: B008
    return cb.submit(db, voucher_id, actor)


@router.post("/cash-vouchers/{voucher_id:int}:approve", response_model=VoucherOut, summary="Duyệt phiếu chi (BR-114)")
def approve(
    voucher_id: int,
    body: NoteIn | None = None,
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return cb.approve(db, voucher_id, body.note if body else None, actor)


@router.post("/cash-vouchers/{voucher_id:int}:record", response_model=VoucherOut, summary="Ghi sổ")
def record(voucher_id: int, actor: SysUser = Depends(_approver), db: Session = Depends(get_db)):  # noqa: B008
    return cb.record(db, voucher_id, actor)


@router.post("/cash-vouchers/{voucher_id:int}:reject", response_model=VoucherOut, summary="Trả lại phiếu về nháp")
def reject(
    voucher_id: int,
    body: ReasonIn,
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return cb.reject(db, voucher_id, body.reason, actor)


@router.post("/cash-vouchers/{voucher_id:int}:void", response_model=VoucherOut, summary="Hủy phiếu kèm lý do (BR-112)")
def void(
    voucher_id: int,
    body: ReasonIn,
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return cb.void(db, voucher_id, body.reason, actor)


@router.get("/cashbook/balance", response_model=BalanceOut, summary="Số dư quỹ (phiếu đã ghi sổ)")
def balance(
    contract_id: int | None = Query(None, gt=0),
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return cb.balance(db, contract_id)


@router.get("/cashbook/ledger", response_model=LedgerOut, summary="BC-TC-01 – sổ chi tiết thu – chi theo hợp đồng")
def ledger(
    contract_id: int = Query(..., gt=0),
    shipment_id: int | None = Query(None, gt=0),
    date_from: date | None = None,
    date_to: date | None = None,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return cb.ledger(db, contract_id, shipment_id, date_from, date_to)
