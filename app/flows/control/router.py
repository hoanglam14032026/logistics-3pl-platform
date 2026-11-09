"""
API Luồng 2 – kế hoạch, phân công `/api/v2/control/...` (03 §6; Giai đoạn 11).

Quyền: `manager` – mọi kế hoạch, phân công tay / chạy lại tự động, khối lượng việc, hồ sơ năng lực; `business_manager`,
`admin` – đọc; `ops_staff` – **kế hoạch của mình** (là thành viên / có lô mình phụ trách), lô và bước mình phụ trách.
Vai trò khác, `customer` → 403. Không thấy → 404.
"""

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.core.pagination import Page, PageParams
from app.core.rbac import Role, require_roles
from app.core.timeutil import now_utc
from app.core.versioning import etag, parse_if_match
from app.flows.control import plan_approval, service, tracking
from app.flows.control.models import AlertType, CtlAlert, CtlShipmentPlan, CtlShipmentTask, CtlStaffProfile
from app.flows.control.schemas import (
    AlertOut,
    AssignIn,
    AssignmentOut,
    AutoAssignIn,
    HealthOut,
    KpiTargetOut,
    KpiTargetsIn,
    MemberOut,
    PlanDetailOut,
    PlanOut,
    PlanTrackOut,
    ProfileIn,
    ProfileOut,
    RejectIn,
    ResolveIn,
    ShipmentPlanDetailOut,
    ShipmentPlanOut,
    ShipmentTrackDetailOut,
    ShipmentTrackOut,
    TaskActionIn,
    TaskBlockIn,
    TaskDetailOut,
    TaskOut,
    TaskProgressIn,
    TaskTrackOut,
    TaskUpdateOut,
    WorkloadOut,
)
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/control", tags=["Control – Plans & assignments"])

_reader = require_roles(Role.MANAGER, Role.BUSINESS_MANAGER, Role.ADMIN, Role.OPS_STAFF)
_manager = require_roles(Role.MANAGER)
_manager_view = require_roles(Role.MANAGER, Role.BUSINESS_MANAGER, Role.ADMIN)
_approver = require_roles(Role.BUSINESS_MANAGER)


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


# --- Mục tiêu KPI + duyệt kế hoạch (Giai đoạn 15) → EV-201 ---------------------------------------------------------


def _plan_detail(db: Session, plan, user: SysUser, response: Response) -> PlanDetailOut:
    children = service.plan_children(db, plan, user)
    response.headers["ETag"] = etag(plan.row_version)
    return PlanDetailOut.model_validate(plan).model_copy(
        update={
            "members": [MemberOut.model_validate(m) for m in children["members"]],
            "kpi_targets": [KpiTargetOut.model_validate(k) for k in children["kpi_targets"]],
            "shipments": [ShipmentPlanOut.model_validate(s) for s in children["shipments"]],
        }
    )


@router.put(
    "/contract-plans/{plan_id:int}/kpi-targets",
    response_model=PlanDetailOut,
    summary="Đặt mục tiêu KPI của kế hoạch (manager; nháp / đang sửa)",
)
def put_kpi_targets(
    plan_id: int,
    body: KpiTargetsIn,
    response: Response,
    if_match: str | None = Header(None),
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items = [t.model_dump() for t in body.kpi_targets]
    plan = plan_approval.set_targets(db, plan_id, items, actor, parse_if_match(if_match))
    return _plan_detail(db, plan, actor, response)


@router.post("/contract-plans/{plan_id:int}:submit", response_model=PlanDetailOut, summary="Gửi duyệt kế hoạch")
def submit_plan(
    plan_id: int,
    response: Response,
    if_match: str | None = Header(None),
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _plan_detail(db, plan_approval.submit(db, plan_id, actor, parse_if_match(if_match)), actor, response)


@router.post("/contract-plans/{plan_id:int}:approve", response_model=PlanDetailOut, summary="Duyệt kế hoạch → EV-201")
def approve_plan(
    plan_id: int,
    response: Response,
    if_match: str | None = Header(None),
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _plan_detail(db, plan_approval.approve(db, plan_id, actor, parse_if_match(if_match)), actor, response)


@router.post("/contract-plans/{plan_id:int}:reject", response_model=PlanDetailOut, summary="Trả kế hoạch về nháp")
def reject_plan(
    plan_id: int,
    body: RejectIn,
    response: Response,
    if_match: str | None = Header(None),
    actor: SysUser = Depends(_approver),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    plan = plan_approval.reject(db, plan_id, body.reason, actor, parse_if_match(if_match))
    return _plan_detail(db, plan, actor, response)


@router.post(
    "/contract-plans/{plan_id:int}:revise",
    response_model=PlanDetailOut,
    summary="Sửa kế hoạch đã duyệt (phiên bản mới)",
)
def revise_plan(
    plan_id: int,
    response: Response,
    if_match: str | None = Header(None),
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _plan_detail(db, plan_approval.revise(db, plan_id, actor, parse_if_match(if_match)), actor, response)


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
    prof = service.upsert_profile(
        db, user_id, body.skills, body.max_load_hours, body.is_available, body.note, actor, body.zalo_user_id
    )
    response.headers["ETag"] = etag(prof.row_version)
    return prof


# --- Theo dõi thực thi (Giai đoạn 12) ---------------------------------------------------------------------------


def _task_track(task, now) -> TaskTrackOut:
    end = task.actual_end or now
    delay = max(round((end - task.planned_end).total_seconds() / 3600, 2), 0.0) if task.status != "closed" else 0.0
    return TaskTrackOut.model_validate(task).model_copy(update={"delay_hours": delay})


def _track(db: Session, sp, now, with_tasks: bool = False):
    tasks = service.tasks_of(db, sp)
    base = ShipmentPlanOut.model_validate(sp).model_dump()
    info = HealthOut(**tracking.health(sp, tasks, now))
    if not with_tasks:
        return ShipmentTrackOut(**base, tracking=info)
    return ShipmentTrackDetailOut(**base, tracking=info, tasks=[_task_track(t, now) for t in tasks])


@router.get(
    "/tracking/shipments",
    response_model=Page[ShipmentTrackOut],
    summary="Theo dõi lô: trạng thái, % tiến độ, bước hiện tại, dự kiến xong, tình trạng (ops_staff: lô của mình)",
)
def track_shipments(
    params: PageParams = Depends(),  # noqa: B008
    contract_id: int | None = Query(None, gt=0),
    assignee_id: int | None = Query(None, gt=0),
    health: str | None = Query(None, pattern=r"^(on_track|at_risk|late|blocked|completed|closed)$"),
    user: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    now = now_utc()
    if health is None:
        items, total = service.list_shipment_plans(db, user, params, contract_id, assignee_id, None)
        return Page[ShipmentTrackOut](
            items=[_track(db, sp, now) for sp in items], total=total, page=params.page, page_size=params.page_size
        )
    everything = PageParams(page=1, page_size=100_000, sort=params.sort)
    all_items, _ = service.list_shipment_plans(db, user, everything, contract_id, assignee_id, None)
    rows = [r for r in (_track(db, sp, now) for sp in all_items) if r.tracking.health == health]
    start = (params.page - 1) * params.page_size
    return Page[ShipmentTrackOut](
        items=rows[start : start + params.page_size], total=len(rows), page=params.page, page_size=params.page_size
    )


@router.get(
    "/tracking/shipments/{shipment_id:int}",
    response_model=ShipmentTrackDetailOut,
    summary="Theo dõi một lô: kế hoạch – thực tế từng bước, trễ",
)
def track_shipment(shipment_id: int, user: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    return _track(db, service.visible_shipment_plan(db, shipment_id, user), now_utc(), with_tasks=True)


@router.get(
    "/tracking/contract-plans/{plan_id:int}",
    response_model=PlanTrackOut,
    summary="Theo dõi kế hoạch hợp đồng: số lô theo tình trạng, tiến độ chung, cảnh báo đang mở",
)
def track_plan(plan_id: int, user: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    plan = service.get_plan(db, plan_id, user)
    shipments = service.plan_children(db, plan, user)["shipments"]
    now = now_utc()
    rows = [_track(db, sp, now) for sp in shipments]
    by_health: dict[str, int] = {}
    for r in rows:
        by_health[r.tracking.health] = by_health.get(r.tracking.health, 0) + 1
    open_alerts = db.scalar(
        select(func.count()).select_from(CtlAlert).where(CtlAlert.plan_id == plan.id, CtlAlert.status != "resolved")
    )
    return PlanTrackOut(
        plan_id=plan.id,
        contract_id=plan.contract_id,
        contract_no=plan.contract_no,
        shipments=len(rows),
        by_health=by_health,
        progress_pct=round(sum(r.tracking.progress_pct for r in rows) / len(rows), 1) if rows else 0.0,
        open_alerts=open_alerts or 0,
    )


def _task_detail(db: Session, task) -> TaskDetailOut:
    base = _task_track(task, now_utc())
    return TaskDetailOut(
        **base.model_dump(), updates=[TaskUpdateOut.model_validate(u) for u in tracking.updates_of(db, task.id)]
    )


@router.get(
    "/tasks/{task_id:int}", response_model=TaskDetailOut, summary="Một bước: kế hoạch, thực tế, lịch sử cập nhật"
)
def get_task(task_id: int, user: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    task = db.get(CtlShipmentTask, task_id)
    sp = db.get(CtlShipmentPlan, task.shipment_plan_id) if task else None
    if task is None or (not service.is_manager_view(user) and user.id not in (task.assignee_id, sp.assignee_id)):
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy bước")
    return _task_detail(db, task)


_actor = require_roles(Role.OPS_STAFF, Role.MANAGER)
# BR-205: business_manager xử lý cảnh báo đã leo thang (kiểm trong tracking)
_alert_handler = require_roles(Role.MANAGER, Role.BUSINESS_MANAGER)


@router.post("/tasks/{task_id:int}:start", response_model=TaskDetailOut, summary="Bắt đầu bước → ctl.task.started")
def start_task(
    task_id: int,
    body: TaskActionIn | None = None,
    actor: SysUser = Depends(_actor),  # noqa: B008
    db: Session = Depends(get_db),
):  # noqa: B008
    return _task_detail(db, tracking.start(db, task_id, body.note if body else None, actor))


@router.patch("/tasks/{task_id:int}", response_model=TaskDetailOut, summary="Cập nhật % tiến độ thực tế")
def progress_task(
    task_id: int,
    body: TaskProgressIn,
    actor: SysUser = Depends(_actor),  # noqa: B008
    db: Session = Depends(get_db),
):  # noqa: B008
    return _task_detail(db, tracking.progress(db, task_id, body.progress_pct, body.note, actor))


@router.post(
    "/tasks/{task_id:int}:block",
    response_model=TaskDetailOut,
    summary="Báo bước bị chặn / lỗi (có lý do) → ctl.task.blocked + cảnh báo manager",
)
def block_task(
    task_id: int,
    body: TaskBlockIn,
    actor: SysUser = Depends(_actor),  # noqa: B008
    db: Session = Depends(get_db),
):  # noqa: B008
    return _task_detail(db, tracking.block(db, task_id, body.reason, actor))


@router.post("/tasks/{task_id:int}:unblock", response_model=TaskDetailOut, summary="Gỡ chặn")
def unblock_task(
    task_id: int,
    body: TaskActionIn | None = None,
    actor: SysUser = Depends(_actor),  # noqa: B008
    db: Session = Depends(get_db),
):  # noqa: B008
    return _task_detail(db, tracking.unblock(db, task_id, body.note if body else None, actor))


@router.post(
    "/tasks/{task_id:int}:complete",
    response_model=TaskDetailOut,
    summary="Hoàn thành bước → ctl.task.completed (kèm số giờ trễ)",
)
def complete_task(
    task_id: int,
    body: TaskActionIn | None = None,
    actor: SysUser = Depends(_actor),  # noqa: B008
    db: Session = Depends(get_db),
):  # noqa: B008
    return _task_detail(db, tracking.complete(db, task_id, body.note if body else None, actor))


# --- Cảnh báo cho manager (Giai đoạn 12) ------------------------------------------------------------------------


@router.get("/alerts", response_model=list[AlertOut], summary="Cảnh báo chậm / lỗi")
def list_alerts(
    status_: str | None = Query(None, alias="status", pattern=r"^(open|acknowledged|resolved|unresolved)$"),
    alert_type: str | None = Query(
        None, pattern=r"^(task_blocked|task_overdue|shipment_late_risk|task_sync_conflict|kpi_below_target)$"
    ),
    shipment_id: int | None = Query(None, gt=0),
    mine: bool = Query(False, description="Chỉ cảnh báo gửi tôi (hoặc gửi mọi manager)"),
    user: SysUser = Depends(_manager_view),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    stmt = select(CtlAlert)
    if status_ == "unresolved":
        stmt = stmt.where(CtlAlert.status != "resolved")
    elif status_:
        stmt = stmt.where(CtlAlert.status == status_)
    if alert_type:
        stmt = stmt.where(CtlAlert.alert_type == alert_type)
    if shipment_id:
        stmt = stmt.where(CtlAlert.shipment_id == shipment_id)
    if mine:
        if Role.MANAGER.value in user.role_codes:
            stmt = stmt.where(or_(CtlAlert.recipient_id == user.id, CtlAlert.recipient_id.is_(None)))
        else:  # business_manager: cảnh báo đã leo thang (BR-205), cảnh báo KPI (Giai đoạn 15)
            stmt = stmt.where(
                or_(
                    CtlAlert.escalated_to_role == Role.BUSINESS_MANAGER.value,
                    CtlAlert.alert_type == AlertType.KPI_BELOW_TARGET.value,
                )
            )
    return db.scalars(stmt.order_by(CtlAlert.id.desc()).limit(500)).all()


@router.post("/alerts/{alert_id:int}:acknowledge", response_model=AlertOut, summary="Xác nhận đã nhận cảnh báo")
def acknowledge_alert(alert_id: int, actor: SysUser = Depends(_alert_handler), db: Session = Depends(get_db)):  # noqa: B008
    return tracking.acknowledge(db, alert_id, actor)


@router.post("/alerts/{alert_id:int}:resolve", response_model=AlertOut, summary="Đóng cảnh báo (có ghi chú)")
def resolve_alert(
    alert_id: int,
    body: ResolveIn,
    actor: SysUser = Depends(_alert_handler),  # noqa: B008
    db: Session = Depends(get_db),
):  # noqa: B008
    return tracking.resolve(db, alert_id, body.note, actor)


@router.post("/alerts:scan", summary="Quét chậm trễ ngay (bình thường chạy định kỳ trong worker)")
def scan_alerts(_: SysUser = Depends(_manager), db: Session = Depends(get_db)) -> dict[str, int]:  # noqa: B008
    return tracking.scan(db)


@router.post(
    "/shipment-plans/{shipment_id:int}:resync",
    summary="Gửi lại trạng thái chuẩn các bước cho Luồng 1 (khi một bên lỡ sự kiện) – Giai đoạn 13",
)
def resync_shipment(
    shipment_id: int,
    actor: SysUser = Depends(_manager),  # noqa: B008
    db: Session = Depends(get_db),
) -> dict[str, int]:  # noqa: B008
    sp = service.get_shipment_plan(db, shipment_id)
    return {"tasks": tracking.resync(db, sp, actor.id)}
