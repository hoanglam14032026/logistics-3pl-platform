"""
API Luồng 2 – kế hoạch, phân công `/api/v2/control/...` (03 §6; Giai đoạn 11).

Quyền: `manager` – mọi kế hoạch, phân công tay / chạy lại tự động, khối lượng việc, hồ sơ năng lực; `business_manager`,
`admin` – đọc; `ops_staff` – **kế hoạch của mình** (là thành viên / có lô mình phụ trách), lô và bước mình phụ trách.
Vai trò khác, `customer` → 403. Không thấy → 404.
"""

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.core.versioning import etag
from app.flows.control import service
from app.flows.control.models import CtlStaffProfile
from app.flows.control.schemas import (
    AssignIn,
    AssignmentOut,
    AutoAssignIn,
    KpiTargetOut,
    MemberOut,
    PlanDetailOut,
    PlanOut,
    ProfileIn,
    ProfileOut,
    ShipmentPlanDetailOut,
    ShipmentPlanOut,
    TaskOut,
    WorkloadOut,
)
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/control", tags=["Control – Plans & assignments"])

_reader = require_roles(Role.MANAGER, Role.BUSINESS_MANAGER, Role.ADMIN, Role.OPS_STAFF)
_manager = require_roles(Role.MANAGER)
_manager_view = require_roles(Role.MANAGER, Role.BUSINESS_MANAGER, Role.ADMIN)


def _sp_detail(db: Session, sp) -> ShipmentPlanDetailOut:
    return ShipmentPlanDetailOut.model_validate(sp).model_copy(
        update={
            "tasks": [TaskOut.model_validate(t) for t in service.tasks_of(db, sp)],
            "assignments": [AssignmentOut.model_validate(a) for a in service.assignments_of(db, sp.shipment_id)],
        }
    )


@router.get("/contract-plans", response_model=Page[PlanOut], summary="Kế hoạch triển khai hợp đồng")
def list_plans(
    params: PageParams = Depends(),  # noqa: B008
    status_: str | None = Query(None, alias="status", max_length=20),
    user: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_plans(db, user, params, status_)
    return Page[PlanOut](
        items=[PlanOut.model_validate(p) for p in items], total=total, page=params.page, page_size=params.page_size
    )


@router.get("/contract-plans/{plan_id:int}", response_model=PlanDetailOut, summary="Kế hoạch + thành viên, KPI, lô")
def get_plan(
    plan_id: int,
    response: Response,
    user: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    plan = service.get_plan(db, plan_id, user)
    children = service.plan_children(db, plan, user)
    response.headers["ETag"] = etag(plan.row_version)
    return PlanDetailOut.model_validate(plan).model_copy(
        update={
            "members": [MemberOut.model_validate(m) for m in children["members"]],
            "kpi_targets": [KpiTargetOut.model_validate(k) for k in children["kpi_targets"]],
            "shipments": [ShipmentPlanOut.model_validate(s) for s in children["shipments"]],
        }
    )


@router.get("/shipment-plans", response_model=Page[ShipmentPlanOut], summary="Kế hoạch lô (ops_staff: lô của mình)")
def list_shipment_plans(
    params: PageParams = Depends(),  # noqa: B008
    contract_id: int | None = Query(None, gt=0),
    assignee_id: int | None = Query(None, gt=0),
    status_: str | None = Query(None, alias="status", max_length=16),
    user: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items, total = service.list_shipment_plans(db, user, params, contract_id, assignee_id, status_)
    return Page[ShipmentPlanOut](
        items=[ShipmentPlanOut.model_validate(s) for s in items],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get(
    "/shipment-plans/{shipment_id:int}",
    response_model=ShipmentPlanDetailOut,
    summary="Kế hoạch một lô: các bước từ quy trình mẫu, lịch sử phân công",
)
def get_shipment_plan(
    shipment_id: int,
    user: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _sp_detail(db, service.visible_shipment_plan(db, shipment_id, user))


@router.get("/my-tasks", response_model=list[TaskOut], summary="Bước đang mở được giao cho tôi")
def my_tasks(user: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    return service.my_tasks(db, user)


@router.get("/assignments", response_model=list[AssignmentOut], summary="Lịch sử phân công")
def list_assignments(
    shipment_id: int | None = Query(None, gt=0),
    assignee_id: int | None = Query(None, gt=0),
    current: bool | None = None,
    user: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return service.list_assignments(db, user, shipment_id, assignee_id, current)


@router.post(
    "/assignments",
    response_model=ShipmentPlanDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Manager phân công / điều chuyển lô (có lý do) → EV-202",
)
def assign(body: AssignIn, actor: SysUser = Depends(_manager), db: Session = Depends(get_db)):  # noqa: B008
    sp = service.reassign(db, body.shipment_id, body.assignee_id, body.reason, actor)
    return _sp_detail(db, sp)


@router.post(
    "/assignments:auto",
    response_model=ShipmentPlanDetailOut,
    summary="Chạy lại phân công tự động cho một lô (theo vai trò, năng lực, tải hiện tại)",
)
def auto_assign(body: AutoAssignIn, actor: SysUser = Depends(_manager), db: Session = Depends(get_db)):  # noqa: B008
    return _sp_detail(db, service.rerun_auto(db, body.shipment_id, actor))


@router.get("/workload", response_model=list[WorkloadOut], summary="Khối lượng việc theo nhân viên ops_staff")
def workload(_: SysUser = Depends(_manager_view), db: Session = Depends(get_db)):  # noqa: B008
    return service.workload(db)


@router.get("/staff-profiles", response_model=list[ProfileOut], summary="Hồ sơ năng lực, sức chứa")
def list_profiles(_: SysUser = Depends(_manager_view), db: Session = Depends(get_db)):  # noqa: B008
    return db.scalars(select(CtlStaffProfile).order_by(CtlStaffProfile.user_id)).all()


@router.put("/staff-profiles/{user_id:int}", response_model=ProfileOut, summary="Đặt năng lực, sức chứa (manager)")
def put_profile(
    user_id: int,
    body: ProfileIn,
    response: Response,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    prof = service.upsert_profile(db, user_id, body.skills, body.max_load_hours, body.is_available, body.note, actor)
    response.headers["ETag"] = etag(prof.row_version)
    return prof
