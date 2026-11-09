"""
Tính KPI vận hành từ dữ kiện `asm_fact_*` (Giai đoạn 14; 05 §3, BR-05 – nơi duy nhất tính KPI). Tính khi đọc, chỉ dữ
liệu `production` (BR-01). Kỳ `[from, to]` là ngày theo giờ Việt Nam, gồm cả hai đầu.

Chỉ số (mẫu số = 0 → `null`, không trả 0 – 05 §3):
- **Lô** (theo `delivered_at` trong kỳ): `delivered`, `on_time` (`delivered_at ≤ sla_due_at`),
  `on_time_rate` (KPI-07, %), `late`, `avg_late_hours` (trung bình giờ trễ của các lô trễ), `max_late_hours`.
- **Bước** (trạng thái `done`, theo `actual_end` trong kỳ): `completed`, `on_time` (`actual_end ≤ planned_end`),
  `on_time_rate` (KPI-03, %), `late`, `avg_delay_hours` (trung bình giờ trễ của các bước trễ), `max_delay_hours`.
- **Bị chặn** (theo thời điểm chặn trong kỳ): `blocked_tasks` (số bước khác nhau), `block_events` (số lần).
- **Cảnh báo** (theo `raised_at` trong kỳ): `alerts`, `critical`, `escalated` (đã leo thang BR-205).

Phân nhóm: toàn bộ · hợp đồng (`contract_id` của lô) · lô · nhân viên (người được giao bước: `assignee_id`, chưa có thì
người thực hiện ở Luồng 1; cảnh báo theo người được giao của bước bị cảnh báo; lô không tính theo nhân viên).

So với SLA hợp đồng (EV-101): điều khoản có `kpi_code` là KPI-03 hoặc KPI-07 được so mục tiêu (`target_value`, %, chiều
cao là tốt); điều khoản khác → `computed = false`.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, distinct, func, literal, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.timeutil import VN_TZ, today_vn
from app.flows.assessment.models import AsmFactAlert, AsmFactContract, AsmFactShipment, AsmFactTask, AsmFactTaskBlock

MODE = "production"
MAX_DAYS = 366
DEFAULT_DAYS = 30
COMPUTED_KPIS = {"KPI-03": ("tasks", "on_time_rate"), "KPI-07": ("shipments", "on_time_rate")}


@dataclass(frozen=True)
class Period:
    date_from: date
    date_to: date

    @property
    def start(self) -> datetime:
        return datetime.combine(self.date_from, time.min, VN_TZ)

    @property
    def end(self) -> datetime:  # không gồm
        return datetime.combine(self.date_to + timedelta(days=1), time.min, VN_TZ)

    def out(self) -> dict[str, Any]:
        return {"from": self.date_from, "to": self.date_to, "timezone": "Asia/Ho_Chi_Minh"}


def period(date_from: date | None, date_to: date | None) -> Period:
    date_to = date_to or today_vn()
    date_from = date_from or date_to - timedelta(days=DEFAULT_DAYS - 1)
    issues = []
    if date_from > date_to:
        issues.append({"field": "query.from", "issue": "after_to", "message": "`from` phải ≤ `to`"})
    elif (date_to - date_from).days + 1 > MAX_DAYS:
        issues.append({"field": "query.to", "issue": "range_too_long", "message": f"Kỳ tối đa {MAX_DAYS} ngày"})
    if issues:
        raise AppError(ErrorCode.VALIDATION_ERROR, 422, details=issues)
    return Period(date_from, date_to)


def _hours(expr):
    return func.extract("epoch", expr) / 3600.0


def _rate(num: int, den: int) -> float | None:
    return round(num * 100.0 / den, 2) if den else None


def _round(v) -> float | None:
    return None if v is None else round(float(v), 2)


# --- Truy vấn theo nhóm -------------------------------------------------------------------------------------------
# `group`: None | "contract" | "shipment" | "staff"; `key`: lọc một đối tượng của nhóm. Trả {khóa nhóm: {...}}.


def _staff_of_task():
    return func.coalesce(AsmFactTask.assignee_id, AsmFactTask.performer_id)


def _shipment_stats(db: Session, p: Period, group: str | None, key: int | None) -> dict:
    if group == "staff":
        return {}
    s = AsmFactShipment
    col = {None: literal(0), "contract": s.contract_id, "shipment": s.shipment_id}[group]
    late = s.delivered_at > s.sla_due_at
    late_h = _hours(s.delivered_at - s.sla_due_at)
    stmt = select(
        col.label("k"),
        func.count().label("delivered"),
        func.count().filter(~late).label("on_time"),
        func.avg(late_h).filter(late).label("avg_late"),
        func.max(late_h).filter(late).label("max_late"),
    ).where(s.mode == MODE, s.delivered_at >= p.start, s.delivered_at < p.end, s.sla_due_at.is_not(None))
    if key is not None:
        stmt = stmt.where(col == key)
    out = {}
    for r in db.execute(stmt.group_by(col)):
        out[r.k] = {
            "delivered": r.delivered,
            "on_time": r.on_time,
            "late": r.delivered - r.on_time,
            "on_time_rate": _rate(r.on_time, r.delivered),
            "avg_late_hours": _round(r.avg_late),
            "max_late_hours": _round(r.max_late),
        }
    return out


def _task_group_col(group: str | None):
    if group == "contract":
        return AsmFactShipment.contract_id
    if group == "shipment":
        return AsmFactTask.shipment_id
    if group == "staff":
        return _staff_of_task()
    return literal(0)


def _task_stats(db: Session, p: Period, group: str | None, key: int | None) -> dict:
    t = AsmFactTask
    col = _task_group_col(group)
    late = t.actual_end > t.planned_end
    delay_h = _hours(t.actual_end - t.planned_end)
    stmt = (
        select(
            col.label("k"),
            func.count().label("completed"),
            func.count().filter(~late).label("on_time"),
            func.avg(delay_h).filter(late).label("avg_delay"),
            func.max(delay_h).filter(late).label("max_delay"),
            func.count().filter(t.sequence_ok.is_(True)).label("sequence_ok"),
        )
        .select_from(t)
        .outerjoin(AsmFactShipment, AsmFactShipment.shipment_id == t.shipment_id)
        .where(
            t.mode == MODE,
            t.status == "done",
            t.actual_end >= p.start,
            t.actual_end < p.end,
            t.planned_end.is_not(None),
        )
    )
    if key is not None:
        stmt = stmt.where(col == key)
    out = {}
    for r in db.execute(stmt.group_by(col)):
        out[r.k] = {
            "completed": r.completed,
            "on_time": r.on_time,
            "late": r.completed - r.on_time,
            "on_time_rate": _rate(r.on_time, r.completed),
            "avg_delay_hours": _round(r.avg_delay),
            "max_delay_hours": _round(r.max_delay),
        }
    return out


def _block_stats(db: Session, p: Period, group: str | None, key: int | None) -> dict:
    b = AsmFactTaskBlock
    col = {
        None: literal(0),
        "contract": AsmFactShipment.contract_id,
        "shipment": b.shipment_id,
        "staff": b.assignee_id,
    }[group]
    stmt = (
        select(col.label("k"), func.count(distinct(b.task_id)).label("tasks"), func.count().label("events"))
        .select_from(b)
        .outerjoin(AsmFactShipment, AsmFactShipment.shipment_id == b.shipment_id)
        .where(b.mode == MODE, b.blocked_at >= p.start, b.blocked_at < p.end)
    )
    if key is not None:
        stmt = stmt.where(col == key)
    return {r.k: {"blocked_tasks": r.tasks, "block_events": r.events} for r in db.execute(stmt.group_by(col))}


def _alert_stats(db: Session, p: Period, group: str | None, key: int | None) -> dict:
    a = AsmFactAlert
    col = {
        None: literal(0),
        "contract": AsmFactShipment.contract_id,
        "shipment": a.shipment_id,
        "staff": _staff_of_task(),
    }[group]
    stmt = (
        select(
            col.label("k"),
            func.count().label("alerts"),
            func.count().filter(a.severity == "critical").label("critical"),
            func.count().filter(a.escalated_at.is_not(None)).label("escalated"),
        )
        .select_from(a)
        .outerjoin(AsmFactShipment, AsmFactShipment.shipment_id == a.shipment_id)
        .outerjoin(AsmFactTask, AsmFactTask.task_id == a.task_id)
        .where(a.mode == MODE, a.raised_at >= p.start, a.raised_at < p.end)
    )
    if key is not None:
        stmt = stmt.where(col == key)
    return {
        r.k: {"alerts": r.alerts, "critical": r.critical, "escalated": r.escalated}
        for r in db.execute(stmt.group_by(col))
    }


_EMPTY_SHIPMENTS = {
    "delivered": 0,
    "on_time": 0,
    "late": 0,
    "on_time_rate": None,
    "avg_late_hours": None,
    "max_late_hours": None,
}
_EMPTY_TASKS = {
    "completed": 0,
    "on_time": 0,
    "late": 0,
    "on_time_rate": None,
    "avg_delay_hours": None,
    "max_delay_hours": None,
}
_EMPTY_BLOCKS = {"blocked_tasks": 0, "block_events": 0}
_EMPTY_ALERTS = {"alerts": 0, "critical": 0, "escalated": 0}


def metrics(db: Session, p: Period, group: str | None = None, key: int | None = None) -> dict[Any, dict[str, Any]]:
    """{khóa nhóm: {shipments, tasks, blocked, alerts}} – chỉ nhóm có số liệu trong kỳ (toàn bộ: khóa 0)."""
    parts = {
        "shipments": (_shipment_stats(db, p, group, key), _EMPTY_SHIPMENTS),
        "tasks": (_task_stats(db, p, group, key), _EMPTY_TASKS),
        "blocked": (_block_stats(db, p, group, key), _EMPTY_BLOCKS),
        "alerts": (_alert_stats(db, p, group, key), _EMPTY_ALERTS),
    }
    keys = set()
    for data, _ in parts.values():
        keys.update(k for k in data if k is not None)
    if group is None:
        keys = {0}
    elif key is not None:
        keys = {key}
    out = {}
    for k in keys:
        m = {name: dict(data.get(k) or blank) for name, (data, blank) in parts.items()}
        if group == "staff":
            m["shipments"] = None  # lô không gán theo nhân viên
        out[k] = m
    return out


def empty(group: str | None = None) -> dict[str, Any]:
    """Chỉ số của đối tượng không có số liệu trong kỳ."""
    return {
        "shipments": None if group == "staff" else dict(_EMPTY_SHIPMENTS),
        "tasks": dict(_EMPTY_TASKS),
        "blocked": dict(_EMPTY_BLOCKS),
        "alerts": dict(_EMPTY_ALERTS),
    }


# --- SLA hợp đồng -----------------------------------------------------------------------------------------------


def _decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def sla_compliance(sla: dict[str, dict[str, Any]], m: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for sla_code in sorted(sla):
        term = sla[sla_code] or {}
        kpi_code = term.get("kpi_code")
        target = _decimal(term.get("target_value"))
        source = COMPUTED_KPIS.get(kpi_code or "")
        actual = m[source[0]][source[1]] if source and m.get(source[0]) else None
        met = None
        if source and actual is not None and target is not None:
            met = Decimal(str(actual)) >= target
        out.append(
            {
                "sla_code": sla_code,
                "kpi_code": kpi_code,
                "description": term.get("description"),
                "target_value": str(target) if target is not None else None,
                "unit": term.get("unit"),
                "computed": source is not None,
                "actual_value": actual,
                "met": met,
            }
        )
    return out


def contracts_in_scope(db: Session, p: Period, ids: set[int]) -> list[AsmFactContract]:
    """Hợp đồng có số liệu trong kỳ hoặc có hiệu lực giao với kỳ."""
    c = AsmFactContract
    stmt = select(c).where(
        c.mode == MODE,
        or_(c.contract_id.in_(ids or {-1}), and_(c.valid_from <= p.date_to, c.valid_to >= p.date_from)),
    )
    return list(db.scalars(stmt.order_by(c.contract_id)))


def shipments_info(db: Session, ids: set[int]) -> dict[int, AsmFactShipment]:
    if not ids:
        return {}
    rows = db.scalars(select(AsmFactShipment).where(AsmFactShipment.shipment_id.in_(ids)))
    return {r.shipment_id: r for r in rows}
