"""
Mục tiêu KPI + duyệt kế hoạch hợp đồng (Giai đoạn 15; 03 §3.1, CTL-F01) – phát EV-201 cho Luồng 4.

`draft` / `revised` → (`manager` sửa mục tiêu KPI, `:submit`) → `pending_approval` → `business_manager` `:approve`
(→ `approved`, EV-201 kèm toàn bộ mục tiêu) hoặc `:reject` (lý do → `draft`). Kế hoạch đã duyệt: `manager` `:revise`
(→ `revised`, `plan_version + 1`) để đổi mục tiêu rồi gửi duyệt lại. Mục tiêu nằm ở dữ liệu kế hoạch
(`ctl_plan_kpi_targets` – mặc định từ SLA hợp đồng trong EV-101), không đặt cứng trong mã nguồn.
"""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.timeutil import now_utc
from app.flows.control.models import CtlContractPlan, CtlPlanKpiTarget, PlanStatus
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import KpiTargetItem, PlanApproved
from app.shared.identity.models import SysUser

EDITABLE = (PlanStatus.DRAFT.value, PlanStatus.REVISED.value)


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _lock(db: Session, plan_id: int, expected_version: int | None) -> CtlContractPlan:
    plan = db.scalar(select(CtlContractPlan).where(CtlContractPlan.id == plan_id).with_for_update())
    if plan is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy kế hoạch")
    if expected_version is not None and expected_version != plan.row_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, 409, "Kế hoạch đã bị người khác thay đổi")
    return plan


def targets_of(db: Session, plan_id: int) -> list[CtlPlanKpiTarget]:
    return list(
        db.scalars(
            select(CtlPlanKpiTarget).where(CtlPlanKpiTarget.plan_id == plan_id).order_by(CtlPlanKpiTarget.kpi_code)
        )
    )


def _snapshot(targets: list[CtlPlanKpiTarget]) -> list[dict[str, Any]]:
    return [
        {
            "kpi_code": t.kpi_code,
            "target_value": str(t.target_value) if t.target_value is not None else None,
            "warning_threshold": str(t.warning_threshold) if t.warning_threshold is not None else None,
            "unit": t.unit,
        }
        for t in targets
    ]


def _touch(plan: CtlContractPlan, actor: SysUser) -> None:
    plan.updated_at, plan.updated_by = now_utc(), actor.id


def set_targets(
    db: Session, plan_id: int, items: list[dict[str, Any]], actor: SysUser, expected_version: int | None
) -> CtlContractPlan:
    """Thay toàn bộ mục tiêu KPI của kế hoạch nháp / đang sửa. Mã đã có từ SLA giữ `source = contract_sla`."""
    plan = _lock(db, plan_id, expected_version)
    if plan.status not in EDITABLE:
        raise _conflict(f"Kế hoạch đang '{plan.status}' – chỉ sửa mục tiêu khi nháp / đang sửa (revised)")
    before = targets_of(db, plan.id)
    sources = {t.kpi_code: t.source for t in before}
    db.execute(delete(CtlPlanKpiTarget).where(CtlPlanKpiTarget.plan_id == plan.id))
    for item in items:
        db.add(
            CtlPlanKpiTarget(
                plan_id=plan.id,
                kpi_code=item["kpi_code"],
                description=item.get("description"),
                target_value=item["target_value"],
                unit=item.get("unit"),
                warning_threshold=item.get("warning_threshold"),
                source=sources.get(item["kpi_code"], "internal"),
            )
        )
    _touch(plan, actor)
    db.flush()
    audit.record(
        db,
        "plan.kpi_targets",
        "contract_plan",
        plan.id,
        actor.id,
        {"kpi_targets": _snapshot(before)},
        {"kpi_targets": _snapshot(targets_of(db, plan.id))},
    )
    db.commit()
    return plan


def _transition(
    db: Session, plan_id: int, actor: SysUser, expected_version: int | None, allowed: tuple[str, ...], target: str
) -> tuple[CtlContractPlan, str]:
    plan = _lock(db, plan_id, expected_version)
    if plan.status not in allowed:
        raise _conflict(f"Kế hoạch đang '{plan.status}' – không chuyển sang '{target}' được")
    before = plan.status
    plan.status = target
    _touch(plan, actor)
    return plan, before


def submit(db: Session, plan_id: int, actor: SysUser, expected_version: int | None) -> CtlContractPlan:
    plan, before = _transition(db, plan_id, actor, expected_version, EDITABLE, PlanStatus.PENDING_APPROVAL.value)
    if not any(t.target_value is not None for t in targets_of(db, plan.id)):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            "Kế hoạch chưa có mục tiêu KPI nào có giá trị",
            [{"field": "kpi_targets", "issue": "required", "message": "Cần ít nhất một mục tiêu KPI"}],
        )
    db.flush()
    audit.record(db, "plan.submit", "contract_plan", plan.id, actor.id, {"status": before}, {"status": plan.status})
    db.commit()
    return plan


def approve(db: Session, plan_id: int, actor: SysUser, expected_version: int | None) -> CtlContractPlan:
    plan, before = _transition(
        db, plan_id, actor, expected_version, (PlanStatus.PENDING_APPROVAL.value,), PlanStatus.APPROVED.value
    )
    targets = targets_of(db, plan.id)
    db.flush()
    audit.record(
        db,
        "plan.approve",
        "contract_plan",
        plan.id,
        actor.id,
        {"status": before},
        {"status": plan.status, "plan_version": plan.plan_version, "kpi_targets": _snapshot(targets)},
    )
    publisher.publish(
        db,
        PlanApproved(
            plan_id=plan.id,
            contract_id=plan.contract_id,
            plan_version=plan.plan_version,
            approved_by=actor.id,
            kpi_targets=[
                KpiTargetItem(
                    kpi_code=t.kpi_code, target=t.target_value, warning_threshold=t.warning_threshold, unit=t.unit
                )
                for t in targets
            ],
        ),
        "contract_plan",
        plan.id,
        actor.id,
        plan.mode,
    )
    db.commit()
    return plan


def reject(db: Session, plan_id: int, reason: str, actor: SysUser, expected_version: int | None) -> CtlContractPlan:
    plan, before = _transition(
        db, plan_id, actor, expected_version, (PlanStatus.PENDING_APPROVAL.value,), PlanStatus.DRAFT.value
    )
    db.flush()
    audit.record(
        db,
        "plan.reject",
        "contract_plan",
        plan.id,
        actor.id,
        {"status": before},
        {"status": plan.status, "reason": reason},
    )
    db.commit()
    return plan


def revise(db: Session, plan_id: int, actor: SysUser, expected_version: int | None) -> CtlContractPlan:
    plan, before = _transition(
        db, plan_id, actor, expected_version, (PlanStatus.APPROVED.value,), PlanStatus.REVISED.value
    )
    plan.plan_version += 1
    db.flush()
    audit.record(
        db,
        "plan.revise",
        "contract_plan",
        plan.id,
        actor.id,
        {"status": before},
        {"status": plan.status, "plan_version": plan.plan_version},
    )
    db.commit()
    return plan
