"""
Luồng 2 – lập kế hoạch, phân công (Giai đoạn 11).

- EV-101 (hợp đồng kích hoạt) → kế hoạch triển khai `draft` (CTL-F01): bản chụp hợp đồng, mục tiêu KPI từ SLA, người
  phụ trách hợp đồng (`account_manager`) = `manager` ít kế hoạch đang mở nhất.
- EV-111 (lô mới) → kế hoạch lô + các bước từ quy trình mẫu đã duyệt khớp chiều, phương thức vận tải, dịch vụ của hợp
  đồng; thời gian kế hoạch = giờ chuẩn cộng dồn theo thứ tự bước; phân công tự động (app/flows/control/assign.py) →
  EV-202 cho Luồng 1. Bước điểm kiểm soát: người kiểm soát = `account_manager` (khác người thực hiện – BR-201).
- EV-112 sang `closed`/`cancelled` → đóng các bước (không còn tính vào tải công việc).
- Đổi người phụ trách (tự động / `manager` chỉ định): ghi `ctl_assignments` (chỉ ghi thêm), nhật ký `assignment.change`,
  phát EV-202 – cùng giao dịch.

Xử lý sự kiện chạy trong giao dịch của bộ nhận (app/shared/events/consumer.py) – không tự commit.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.pagination import PageParams, order_by_clause, paginate
from app.core.rbac import Role
from app.core.timeutil import now_utc
from app.flows.control import assign
from app.flows.control.models import (
    ACTIVE_TASK_STATUSES,
    AssignMethod,
    CtlAssignment,
    CtlContractPlan,
    CtlPlanKpiTarget,
    CtlPlanMember,
    CtlShipmentPlan,
    CtlShipmentTask,
    CtlStaffProfile,
    PlanStatus,
    TaskStatus,
    WorkStatus,
)
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import AssignmentChanged
from app.shared.identity.models import SysUser
from app.shared.masterdata.models import MdProcessStep, MdProcessTemplate, TemplateStatus

STAFF_MEMBER_ROLE = "doc_staff"
STAFF_ROLES = ("doc_staff", "customs_staff", "transport_coordinator", "warehouse_staff")
CLOSING_STATUSES = ("closed", "cancelled")


def _not_found(what: str = "Không tìm thấy") -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, what)


def _field_error(field: str, message: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, message, [{"field": field, "issue": "invalid", "message": message}]
    )


# --- EV-101: kế hoạch triển khai hợp đồng -----------------------------------------------------------------------


def _pick_account_manager(db: Session) -> int | None:
    managers = assign.users_with_role(db, Role.MANAGER.value)
    if not managers:
        return None
    counts = dict(
        db.execute(
            select(CtlContractPlan.account_manager_id, func.count())
            .where(CtlContractPlan.status != PlanStatus.CLOSED.value)
            .group_by(CtlContractPlan.account_manager_id)
        ).all()
    )
    return min(managers, key=lambda u: (counts.get(u.id, 0), u.id)).id


def on_contract_activated(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    data = envelope["data"]
    existing = db.scalar(select(CtlContractPlan).where(CtlContractPlan.contract_id == data["contract_id"]))
    if existing is not None:
        return {"plan_id": existing.id, "created": False}
    manager_id = _pick_account_manager(db)
    plan = CtlContractPlan(
        contract_id=data["contract_id"],
        contract_no=data["contract_no"],
        customer_id=data["customer_id"],
        mode=envelope.get("mode", "production"),
        valid_from=data["valid_from"],
        valid_to=data["valid_to"],
        service_scopes=data.get("service_scope", []),
        account_manager_id=manager_id,
        status=PlanStatus.DRAFT.value,
        source_event_id=envelope["event_id"],
    )
    db.add(plan)
    db.flush()
    if manager_id:
        db.add(CtlPlanMember(plan_id=plan.id, user_id=manager_id, role_in_contract="account_manager"))
    for sla_code, sla in (data.get("sla") or {}).items():
        db.add(
            CtlPlanKpiTarget(
                plan_id=plan.id,
                kpi_code=sla.get("kpi_code") or sla_code,
                description=sla.get("description"),
                target_value=Decimal(str(sla["target_value"])) if sla.get("target_value") is not None else None,
                unit=sla.get("unit"),
                source="contract_sla",
            )
        )
    db.flush()
    audit.record(
        db,
        "plan.create",
        "contract_plan",
        plan.id,
        None,
        after={
            "contract_id": plan.contract_id,
            "contract_no": plan.contract_no,
            "account_manager_id": manager_id,
            "kpi_targets": sorted((data.get("sla") or {}).keys()),
            "source_event_id": envelope["event_id"],
        },
    )
    return {"plan_id": plan.id, "created": True, "account_manager_id": manager_id}


# --- EV-111: kế hoạch lô, bước, phân công ----------------------------------------------------------------------


def choose_template(
    db: Session, direction: str | None, mode: str | None, services: list[str]
) -> MdProcessTemplate | None:
    """Quy trình `approved` cùng chiều, có phương thức vận tải của lô, chung ít nhất một dịch vụ với hợp đồng; nhiều quy
    trình khớp → chung nhiều dịch vụ nhất, rồi mã quy trình."""
    if not direction or not mode:
        return None
    templates = db.scalars(
        select(MdProcessTemplate).where(
            MdProcessTemplate.status == TemplateStatus.APPROVED.value, MdProcessTemplate.direction == direction
        )
    ).all()
    matches = [
        (len(set(t.service_codes) & set(services)), t)
        for t in templates
        if mode in t.transport_modes and set(t.service_codes) & set(services)
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda m: (-m[0], m[1].process_code))[0][1]


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def on_shipment_created(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    data = envelope["data"]
    existing = db.scalar(select(CtlShipmentPlan).where(CtlShipmentPlan.shipment_id == data["shipment_id"]))
    if existing is not None:
        return {"shipment_plan_id": existing.id, "created": False}
    plan = db.scalar(select(CtlContractPlan).where(CtlContractPlan.contract_id == data["contract_id"]))
    services = data.get("service_codes") or [s["service_code"] for s in (plan.service_scopes if plan else [])]
    template = choose_template(db, data.get("direction"), data.get("transport_mode"), services)
    sp = CtlShipmentPlan(
        shipment_id=data["shipment_id"],
        shipment_no=data["shipment_no"],
        contract_id=data["contract_id"],
        plan_id=plan.id if plan else None,
        mode=envelope.get("mode", "production"),
        direction=data.get("direction"),
        transport_mode=data.get("transport_mode"),
        required_delivery_at=_parse_dt(data.get("required_delivery_at")),
        template_id=template.id if template else None,
        process_code=template.process_code if template else None,
        process_version=template.version if template else None,
        status=WorkStatus.OPEN.value,
        source_event_id=envelope["event_id"],
    )
    db.add(sp)
    db.flush()
    start = _parse_dt(envelope.get("occurred_at")) or now_utc()
    tasks = []
    if template is not None:
        steps = db.scalars(
            select(MdProcessStep).where(MdProcessStep.template_id == template.id).order_by(MdProcessStep.seq)
        ).all()
        cursor = start
        for step in steps:
            end = cursor + timedelta(hours=float(step.standard_duration_hours))
            task = CtlShipmentTask(
                shipment_plan_id=sp.id,
                step_code=step.step_code,
                seq=step.seq,
                name=step.name,
                responsible_role=step.responsible_role,
                required_skill=step.required_skill,
                standard_duration_hours=step.standard_duration_hours,
                depends_on=list(step.depends_on or []),
                is_checkpoint=step.is_checkpoint,
                checkpoint_type=step.checkpoint_type,
                planned_start=cursor,
                planned_end=end,
                status=TaskStatus.OPEN.value,
            )
            db.add(task)
            tasks.append(task)
            cursor = end
        db.flush()
    audit.record(
        db,
        "shipment_plan.create",
        "shipment_plan",
        sp.id,
        None,
        after={
            "shipment_id": sp.shipment_id,
            "plan_id": sp.plan_id,
            "process_code": sp.process_code,
            "process_version": sp.process_version,
            "task_count": len(tasks),
            "source_event_id": envelope["event_id"],
        },
    )
    assignment = auto_assign(db, sp, None, "Tự phân công khi tạo lô (EV-111)")
    return {
        "shipment_plan_id": sp.id,
        "created": True,
        "process_code": sp.process_code,
        "task_count": len(tasks),
        "assignee_id": assignment.assignee_id if assignment else sp.assignee_id,
    }


def tasks_of(db: Session, sp: CtlShipmentPlan) -> list[CtlShipmentTask]:
    stmt = select(CtlShipmentTask).where(CtlShipmentTask.shipment_plan_id == sp.id)
    return list(db.scalars(stmt.order_by(CtlShipmentTask.seq)))


def _plan_staff(db: Session, plan_id: int | None) -> set[int] | None:
    if plan_id is None:
        return None
    members = set(
        db.scalars(
            select(CtlPlanMember.user_id).where(
                CtlPlanMember.plan_id == plan_id,
                CtlPlanMember.role_in_contract.in_(STAFF_ROLES),
                CtlPlanMember.added_by.is_not(
                    None
                ),  # do manager thêm; hệ thống tự thêm khi phân công thì không giới hạn
            )
        )
    )
    return members or None


def _ensure_member(db: Session, plan_id: int | None, user_id: int) -> None:
    if plan_id is None:
        return
    exists = db.scalar(
        select(CtlPlanMember.id).where(
            CtlPlanMember.plan_id == plan_id,
            CtlPlanMember.user_id == user_id,
            CtlPlanMember.role_in_contract.in_(STAFF_ROLES),
        )
    )
    if not exists:
        # added_by NULL: hệ thống thêm theo phân công (không dùng để giới hạn phạm vi phân công tự động – BR-204)
        db.add(CtlPlanMember(plan_id=plan_id, user_id=user_id, role_in_contract=STAFF_MEMBER_ROLE, added_by=None))


def _apply(
    db: Session,
    sp: CtlShipmentPlan,
    assignee_id: int | None,
    method: AssignMethod,
    actor_id: int | None,
    reason: str,
    score: dict | None,
) -> CtlAssignment | None:
    """Đổi người phụ trách lô + các bước `ops_staff` đang mở; lịch sử, nhật ký, EV-202 cùng giao dịch."""
    previous = sp.assignee_id
    current = db.scalar(
        select(CtlAssignment).where(
            CtlAssignment.shipment_id == sp.shipment_id,
            CtlAssignment.task_id.is_(None),
            CtlAssignment.is_current.is_(True),
        )
    )
    if current is not None and previous == assignee_id:
        return None  # không đổi
    if current is not None:
        current.is_current = False
        db.flush()
    sp.assignee_id = assignee_id
    sp.updated_by = actor_id
    tasks = tasks_of(db, sp)
    for t in tasks:
        if t.status in ACTIVE_TASK_STATUSES and t.responsible_role == Role.OPS_STAFF.value:
            t.assignee_id = assignee_id
    _set_controllers(db, sp, tasks, assignee_id)
    body = None
    if assignee_id is not None:
        _ensure_member(db, sp.plan_id, assignee_id)
        body = publisher.publish(
            db,
            AssignmentChanged(
                shipment_id=sp.shipment_id,
                assignee_id=assignee_id,
                previous_assignee_id=previous,
                method=method.value,
                reason=reason,
            ),
            "shipment",
            sp.shipment_id,
            actor_id,
            sp.mode,
        )
    row = CtlAssignment(
        shipment_id=sp.shipment_id,
        assignee_id=assignee_id,
        previous_assignee_id=previous,
        assigned_by=actor_id,
        method=method.value,
        reason=reason,
        score=score,
        is_current=True,
        event_id=body["event_id"] if body else None,
    )
    db.add(row)
    db.flush()
    if body is None:
        audit.record(
            db,
            "assignment.unassigned",
            "shipment",
            sp.shipment_id,
            actor_id,
            before={"assignee_id": previous},
            after={"reason": reason, "score": score},
        )
        return row
    audit.record(
        db,
        "assignment.change",
        "shipment",
        sp.shipment_id,
        actor_id,
        before={"assignee_id": previous},
        after={
            "assignee_id": assignee_id,
            "method": method.value,
            "reason": reason,
            "score": score,
            "assignment_id": row.id,
            "event_id": body["event_id"],
        },
    )
    return row


def _set_controllers(db: Session, sp: CtlShipmentPlan, tasks: list[CtlShipmentTask], performer: int | None) -> None:
    """Người kiểm soát bước điểm kiểm soát: account_manager của kế hoạch; trùng người thực hiện → manager khác ít việc
    nhất (BR-201 – nguyên tắc 4 mắt)."""
    checkpoints = [t for t in tasks if t.is_checkpoint and t.status in ACTIVE_TASK_STATUSES]
    if not checkpoints:
        return
    plan = db.get(CtlContractPlan, sp.plan_id) if sp.plan_id else None
    controller = plan.account_manager_id if plan else None
    if controller is None or controller == performer:
        ranked = assign.rank(db, Role.MANAGER.value, set(), Decimal(0), exclude={performer} if performer else None)
        controller = ranked[0].user_id if ranked else None
    for t in checkpoints:
        t.controller_id = controller if controller != t.assignee_id else None


def auto_assign(db: Session, sp: CtlShipmentPlan, actor_id: int | None, reason: str) -> CtlAssignment | None:
    assign.lock(db)
    tasks = [
        t for t in tasks_of(db, sp) if t.status in ACTIVE_TASK_STATUSES and t.responsible_role == Role.OPS_STAFF.value
    ]
    skills = {t.required_skill for t in tasks if t.required_skill}
    hours = sum((Decimal(t.standard_duration_hours) for t in tasks), Decimal(0))
    restrict = _plan_staff(db, sp.plan_id)
    ranked = assign.rank(db, Role.OPS_STAFF.value, skills, hours, restrict, exclude_shipment_plan_id=sp.id)
    if not ranked and restrict is not None:  # thành viên kế hoạch đều không nhận được (nghỉ, khóa…) → mọi ops_staff
        ranked = assign.rank(db, Role.OPS_STAFF.value, skills, hours, None, exclude_shipment_plan_id=sp.id)
    score = {
        "required_skills": sorted(skills),
        "hours": str(hours),
        "restricted_to_plan_members": restrict is not None,
        "candidates": [assign.serialize(c) for c in ranked[:5]],
    }
    chosen = ranked[0].user_id if ranked else None
    reason_text = reason if chosen else f"{reason} – không có ops_staff phù hợp (đang hoạt động, sẵn sàng)"
    return _apply(db, sp, chosen, AssignMethod.AUTO, actor_id, reason_text, score)


# --- EV-112: đóng việc ------------------------------------------------------------------------------------------


def on_shipment_status_changed(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    data = envelope["data"]
    sp = db.scalar(select(CtlShipmentPlan).where(CtlShipmentPlan.shipment_id == data["shipment_id"]).with_for_update())
    if sp is None:
        return {"reason": "no_shipment_plan"}
    sp.shipment_status = data["to"]
    closed = 0
    if data["to"] in CLOSING_STATUSES and sp.status == WorkStatus.OPEN.value:
        closed = db.execute(
            update(CtlShipmentTask)
            .where(CtlShipmentTask.shipment_plan_id == sp.id, CtlShipmentTask.status.in_(ACTIVE_TASK_STATUSES))
            .values(status=TaskStatus.CLOSED.value, blocked_reason=None)
        ).rowcount
        sp.status = WorkStatus.CLOSED.value
        sp.closed_at = now_utc()
    return {"shipment_plan_id": sp.id, "shipment_status": data["to"], "tasks_closed": closed}


# --- Thao tác của manager ---------------------------------------------------------------------------------------


def get_shipment_plan(db: Session, shipment_id: int, for_update: bool = False) -> CtlShipmentPlan:
    stmt = select(CtlShipmentPlan).where(CtlShipmentPlan.shipment_id == shipment_id)
    sp = db.scalar(stmt.with_for_update() if for_update else stmt)
    if sp is None:
        raise _not_found("Chưa có kế hoạch cho lô này (Luồng 2 chưa nhận EV-111)")
    return sp


def _open(sp: CtlShipmentPlan) -> None:
    if sp.status != WorkStatus.OPEN.value:
        raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, "Lô đã đóng/hủy – không phân công được")


def reassign(db: Session, shipment_id: int, assignee_id: int, reason: str, actor: SysUser) -> CtlShipmentPlan:
    sp = get_shipment_plan(db, shipment_id, for_update=True)
    _open(sp)
    user = db.get(SysUser, assignee_id)
    if user is None or not user.is_active or Role.OPS_STAFF.value not in user.role_codes:
        raise _field_error("body.assignee_id", "Người được phân công phải là tài khoản ops_staff đang hoạt động")
    if sp.assignee_id == assignee_id:
        raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, "Lô đã do người này phụ trách")
    assign.lock(db)
    ranked = assign.rank(db, Role.OPS_STAFF.value, set(), Decimal(0), exclude_shipment_plan_id=sp.id)
    score = {"manual": True, "chosen": next((assign.serialize(c) for c in ranked if c.user_id == assignee_id), None)}
    _apply(db, sp, assignee_id, AssignMethod.MANUAL, actor.id, reason, score)
    db.commit()
    return sp


def rerun_auto(db: Session, shipment_id: int, actor: SysUser) -> CtlShipmentPlan:
    sp = get_shipment_plan(db, shipment_id, for_update=True)
    _open(sp)
    auto_assign(db, sp, actor.id, "Manager yêu cầu phân công tự động lại")
    db.commit()
    return sp


def upsert_profile(
    db: Session,
    user_id: int,
    skills: dict[str, int],
    max_load: Decimal,
    available: bool,
    note: str | None,
    actor: SysUser,
) -> CtlStaffProfile:
    user = db.get(SysUser, user_id)
    if user is None or Role.OPS_STAFF.value not in user.role_codes and Role.MANAGER.value not in user.role_codes:
        raise _not_found("Không có nhân viên ops_staff/manager này")
    prof = db.scalar(select(CtlStaffProfile).where(CtlStaffProfile.user_id == user_id).with_for_update())
    before = None
    if prof is None:
        prof = CtlStaffProfile(user_id=user_id, created_by=actor.id)
        db.add(prof)
    else:
        before = {"skills": prof.skills, "max_load_hours": str(prof.max_load_hours), "is_available": prof.is_available}
    prof.skills, prof.max_load_hours, prof.is_available, prof.note = skills, max_load, available, note
    prof.updated_by = actor.id
    db.flush()
    after = {"skills": skills, "max_load_hours": str(max_load), "is_available": available}
    audit.record(db, "staff_profile.update", "staff_profile", user_id, actor.id, before, after)
    db.commit()
    return prof


# --- Đọc --------------------------------------------------------------------------------------------------------


def is_manager_view(user: SysUser) -> bool:
    return bool({Role.MANAGER.value, Role.BUSINESS_MANAGER.value, Role.ADMIN.value} & set(user.role_codes))


def visible_plan_ids(db: Session, user: SysUser) -> set[int] | None:
    """None = mọi kế hoạch. ops_staff: kế hoạch mình là thành viên hoặc có lô mình phụ trách."""
    if is_manager_view(user):
        return None
    members = set(db.scalars(select(CtlPlanMember.plan_id).where(CtlPlanMember.user_id == user.id)))
    own = set(
        db.scalars(
            select(CtlShipmentPlan.plan_id).where(
                CtlShipmentPlan.assignee_id == user.id, CtlShipmentPlan.plan_id.is_not(None)
            )
        )
    )
    return members | own


def list_plans(db: Session, user: SysUser, params: PageParams, status: str | None) -> tuple[list[CtlContractPlan], int]:
    stmt = select(CtlContractPlan)
    ids = visible_plan_ids(db, user)
    if ids is not None:
        stmt = stmt.where(CtlContractPlan.id.in_(ids or [0]))
    if status:
        stmt = stmt.where(CtlContractPlan.status == status)
    stmt = stmt.order_by(order_by_clause(params.sort, {"created_at": CtlContractPlan.created_at}), CtlContractPlan.id)
    return paginate(db, stmt, params)


def get_plan(db: Session, plan_id: int, user: SysUser) -> CtlContractPlan:
    plan = db.get(CtlContractPlan, plan_id)
    ids = visible_plan_ids(db, user)
    if plan is None or (ids is not None and plan.id not in ids):
        raise _not_found("Không tìm thấy kế hoạch")
    return plan


def plan_children(db: Session, plan: CtlContractPlan, user: SysUser) -> dict[str, list]:
    stmt = select(CtlShipmentPlan).where(CtlShipmentPlan.plan_id == plan.id)
    if not is_manager_view(user):
        stmt = stmt.where(CtlShipmentPlan.assignee_id == user.id)
    return {
        "members": list(
            db.scalars(select(CtlPlanMember).where(CtlPlanMember.plan_id == plan.id).order_by(CtlPlanMember.id))
        ),
        "kpi_targets": list(
            db.scalars(
                select(CtlPlanKpiTarget).where(CtlPlanKpiTarget.plan_id == plan.id).order_by(CtlPlanKpiTarget.id)
            )
        ),
        "shipments": list(db.scalars(stmt.order_by(CtlShipmentPlan.id))),
    }


def list_shipment_plans(
    db: Session, user: SysUser, params: PageParams, contract_id: int | None, assignee_id: int | None, status: str | None
) -> tuple[list[CtlShipmentPlan], int]:
    stmt = select(CtlShipmentPlan)
    if not is_manager_view(user):
        stmt = stmt.where(CtlShipmentPlan.assignee_id == user.id)
    if contract_id:
        stmt = stmt.where(CtlShipmentPlan.contract_id == contract_id)
    if assignee_id:
        stmt = stmt.where(CtlShipmentPlan.assignee_id == assignee_id)
    if status:
        stmt = stmt.where(CtlShipmentPlan.status == status)
    stmt = stmt.order_by(order_by_clause(params.sort, {"created_at": CtlShipmentPlan.created_at}), CtlShipmentPlan.id)
    return paginate(db, stmt, params)


def visible_shipment_plan(db: Session, shipment_id: int, user: SysUser) -> CtlShipmentPlan:
    sp = db.scalar(select(CtlShipmentPlan).where(CtlShipmentPlan.shipment_id == shipment_id))
    if sp is None or (not is_manager_view(user) and sp.assignee_id != user.id):
        raise _not_found("Không tìm thấy kế hoạch lô")
    return sp


def assignments_of(db: Session, shipment_id: int) -> list[CtlAssignment]:
    stmt = select(CtlAssignment).where(CtlAssignment.shipment_id == shipment_id).order_by(CtlAssignment.id)
    return list(db.scalars(stmt))


def list_assignments(
    db: Session, user: SysUser, shipment_id: int | None, assignee_id: int | None, current: bool | None
) -> list[CtlAssignment]:
    stmt = select(CtlAssignment)
    if not is_manager_view(user):
        stmt = stmt.where(CtlAssignment.assignee_id == user.id)
    if shipment_id:
        stmt = stmt.where(CtlAssignment.shipment_id == shipment_id)
    if assignee_id:
        stmt = stmt.where(CtlAssignment.assignee_id == assignee_id)
    if current is not None:
        stmt = stmt.where(CtlAssignment.is_current.is_(current))
    return list(db.scalars(stmt.order_by(CtlAssignment.id.desc()).limit(500)))


def workload(db: Session) -> list[dict[str, Any]]:
    current = assign.loads(db)
    profs = assign.profiles(db)
    open_shipments = dict(
        db.execute(
            select(CtlShipmentPlan.assignee_id, func.count())
            .where(CtlShipmentPlan.status == WorkStatus.OPEN.value, CtlShipmentPlan.assignee_id.is_not(None))
            .group_by(CtlShipmentPlan.assignee_id)
        ).all()
    )
    out = []
    for user in assign.users_with_role(db, Role.OPS_STAFF.value):
        prof = profs.get(user.id)
        hours, tasks = current.get(user.id, (Decimal(0), 0))
        capacity = Decimal(prof.max_load_hours) if prof else assign.DEFAULT_MAX_LOAD
        out.append(
            {
                "user_id": user.id,
                "username": user.username,
                "full_name": user.full_name,
                "open_shipments": open_shipments.get(user.id, 0),
                "open_tasks": tasks,
                "open_hours": hours,
                "max_load_hours": capacity,
                "load_ratio": round(float(hours / capacity), 4),
                "is_available": prof.is_available if prof else True,
                "skills": (prof.skills if prof else {}) or {},
            }
        )
    return out


def my_tasks(db: Session, user: SysUser) -> list[CtlShipmentTask]:
    stmt = select(CtlShipmentTask).where(
        CtlShipmentTask.assignee_id == user.id, CtlShipmentTask.status.in_(ACTIVE_TASK_STATUSES)
    )
    return list(db.scalars(stmt.order_by(CtlShipmentTask.planned_start, CtlShipmentTask.id)))
