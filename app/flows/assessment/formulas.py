"""
Công thức KPI-01…14 (05 §3) – Giai đoạn 15. `FORMULAS[formula_key](db, period, group, key)` → `{khóa nhóm: Result}`,
hoặc `None` khi KPI không đo ở cấp đó. Danh mục (`asm_kpi_definitions`) trỏ tới công thức bằng `formula_key`.

Quy ước chung (như Giai đoạn 14): chỉ dữ liệu `production` (BR-01); kỳ theo giờ Việt Nam; mẫu số 0 → không có dòng
(giá trị `null`). Nhóm: `None` (toàn bộ – khóa 0), `contract`, `shipment`, `staff`.

- Tỉ lệ (%): `numerator` = số đạt, `denominator` = số được đo, `value` = numerator ÷ denominator × 100.
- Thời gian (giờ, trung vị): `numerator` = tổng số giờ, `denominator` = số đo được, `value` = trung vị.
- KPI-11, 12, 13 chỉ có ở chế độ `training` (người học) → không đo trên dữ liệu `production` (`None`).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, exists, func, literal, select
from sqlalchemy.orm import Session

from app.flows.assessment.models import (
    AsmFactCheckpoint,
    AsmFactCustoms,
    AsmFactDocCheck,
    AsmFactIncident,
    AsmFactServiceRequest,
    AsmFactShipment,
    AsmFactTask,
)

MODE = "production"
DOCUMENT_ERROR = "DOCUMENT_ERROR"


@dataclass
class Result:
    numerator: float
    denominator: int
    value: float | None
    details: dict[str, Any] = field(default_factory=dict)

    def out(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "details": self.details or None,
        }


Formula = Callable[..., dict[Any, Result] | None]
_NA = object()  # cấp không đo


def _hours(expr):
    return func.extract("epoch", expr) / 3600.0


def _r2(v) -> float | None:
    return None if v is None else round(float(v), 2)


def _ship_contract():
    return AsmFactShipment.contract_id


def _group_col(cols: dict[str, Any], group: str | None):
    if group is None:
        return literal(0)
    return cols.get(group, _NA)


def _run(db: Session, stmt_fn, cols: dict[str, Any], group: str | None, key: int | None):
    col = _group_col(cols, group)
    if col is _NA:
        return None
    stmt = stmt_fn(col.label("k"))
    if key is not None:
        stmt = stmt.where(col == key)
    return [r for r in db.execute(stmt.group_by(col)) if r.k is not None]


def _rate(db, p, group, key, *, model, time_col, good, where=(), cols, join_shipment=False) -> dict | None:
    def build(kcol):
        stmt = select(kcol, func.count().label("den"), func.count().filter(good).label("num")).select_from(model)
        if join_shipment:
            stmt = stmt.outerjoin(AsmFactShipment, AsmFactShipment.shipment_id == model.shipment_id)
        return stmt.where(model.mode == MODE, time_col >= p.start, time_col < p.end, *where)

    rows = _run(db, build, cols, group, key)
    if rows is None:
        return None
    return {r.k: Result(r.num, r.den, round(r.num * 100.0 / r.den, 2) if r.den else None) for r in rows}


def _median(db, p, group, key, *, model, time_col, hours, where=(), cols, join_shipment=False) -> dict | None:
    def build(kcol):
        stmt = select(
            kcol,
            func.count().label("den"),
            func.sum(hours).label("total"),
            func.percentile_cont(0.5).within_group(hours).label("p50"),
        ).select_from(model)
        if join_shipment:
            stmt = stmt.outerjoin(AsmFactShipment, AsmFactShipment.shipment_id == model.shipment_id)
        return stmt.where(model.mode == MODE, time_col >= p.start, time_col < p.end, *where)

    rows = _run(db, build, cols, group, key)
    if rows is None:
        return None
    return {r.k: Result(_r2(r.total) or 0.0, r.den, _r2(r.p50)) for r in rows}


# --- KPI ------------------------------------------------------------------------------------------------------------


def kpi01_doc_first_time_right(db, p, group, key):
    """Lô có lần kiểm tra chứng từ đầu (`run_no = 1`) không lỗi chặn ÷ số lô đã kiểm tra (EV-122)."""
    m = AsmFactDocCheck
    cols = {"contract": _ship_contract(), "shipment": m.shipment_id, "staff": m.checked_by}
    return _rate(
        db,
        p,
        group,
        key,
        model=m,
        time_col=m.checked_at,
        good=m.blocking_count == 0,
        where=(m.run_no == 1,),
        cols=cols,
        join_shipment=True,
    )


def kpi02_doc_processing_hours(db, p, group, key):
    """Trung vị giờ từ chứng từ đầu tiên (EV-121) tới lô `docs_verified` (EV-112)."""
    s = AsmFactShipment
    cols = {"contract": s.contract_id, "shipment": s.shipment_id, "staff": s.docs_verified_by}
    return _median(
        db,
        p,
        group,
        key,
        model=s,
        time_col=s.docs_verified_at,
        hours=_hours(s.docs_verified_at - s.first_doc_received_at),
        where=(s.first_doc_received_at.is_not(None),),
        cols=cols,
    )


def _task_cols():
    t = AsmFactTask
    return {
        "contract": _ship_contract(),
        "shipment": t.shipment_id,
        "staff": func.coalesce(t.assignee_id, t.performer_id),
    }


def kpi03_task_on_time(db, p, group, key):
    """Bước hoàn thành `actual_end ≤ planned_end` ÷ bước hoàn thành (EV-132 / ctl.task.*)."""
    t = AsmFactTask
    return _rate(
        db,
        p,
        group,
        key,
        model=t,
        time_col=t.actual_end,
        good=t.actual_end <= t.planned_end,
        where=(t.status == "done", t.planned_end.is_not(None)),
        cols=_task_cols(),
        join_shipment=True,
    )


def kpi04_process_compliance(db, p, group, key):
    """Bước `sequence_ok ∧ evidence_ok` ÷ bước hoàn thành có `sequence_ok` (EV-132). `evidence_ok` chưa kiểm chứng
    (null) coi là đạt – CẦN XÁC NHẬN."""
    t = AsmFactTask
    good = and_(t.sequence_ok.is_(True), func.coalesce(t.evidence_ok, True).is_(True))
    return _rate(
        db,
        p,
        group,
        key,
        model=t,
        time_col=t.actual_end,
        good=good,
        where=(t.status == "done", t.sequence_ok.is_not(None)),
        cols=_task_cols(),
        join_shipment=True,
    )


def kpi05_checkpoint_first_pass(db, p, group, key):
    """Điểm kiểm soát `passed` ở lần đầu ÷ điểm kiểm soát đã đánh giá lần đầu (EV-204)."""
    c = AsmFactCheckpoint
    cols = {"contract": _ship_contract(), "shipment": c.shipment_id, "staff": c.performer_id}
    return _rate(
        db,
        p,
        group,
        key,
        model=c,
        time_col=c.evaluated_at,
        good=c.result == "passed",
        where=(c.attempt_no == 1,),
        cols=cols,
        join_shipment=True,
    )


def kpi06_customs_clearance_hours(db, p, group, key):
    """Trung vị giờ `cleared_at − submitted_at` (EV-141); `details.by_channel` – trung vị theo luồng xanh/vàng/đỏ."""
    c = AsmFactCustoms
    cols = {"contract": _ship_contract(), "shipment": c.shipment_id}
    hours = _hours(c.cleared_at - c.submitted_at)
    out = _median(db, p, group, key, model=c, time_col=c.cleared_at, hours=hours, cols=cols, join_shipment=True)
    if not out:
        return out
    col = _group_col(cols, group)
    stmt = (
        select(col.label("k"), c.channel, func.percentile_cont(0.5).within_group(hours).label("p50"))
        .select_from(c)
        .outerjoin(AsmFactShipment, AsmFactShipment.shipment_id == c.shipment_id)
        .where(c.mode == MODE, c.cleared_at >= p.start, c.cleared_at < p.end)
    )
    if key is not None:
        stmt = stmt.where(col == key)
    for r in db.execute(stmt.group_by(col, c.channel)):
        if r.k in out:
            out[r.k].details.setdefault("by_channel", {})[r.channel] = _r2(r.p50)
    return out


def _delivered(db, p, group, key, good, where=()):
    s = AsmFactShipment
    return _rate(
        db,
        p,
        group,
        key,
        model=s,
        time_col=s.delivered_at,
        good=good,
        where=(s.sla_due_at.is_not(None), *where),
        cols={"contract": s.contract_id, "shipment": s.shipment_id},
    )


def kpi07_on_time_delivery(db, p, group, key):
    """Lô `delivered_at ≤ sla_due_at` ÷ lô đã giao (EV-142)."""
    s = AsmFactShipment
    return _delivered(db, p, group, key, s.delivered_at <= s.sla_due_at)


def kpi08_in_full_damage_free(db, p, group, key):
    """Lô giao đủ ∧ nguyên vẹn ÷ lô đã giao (EV-142)."""
    s = AsmFactShipment
    return _delivered(db, p, group, key, and_(s.in_full.is_(True), s.damage_free.is_(True)))


def _has_incident(doc_error_only: bool = False):
    i = AsmFactIncident
    cond = [i.shipment_id == AsmFactShipment.shipment_id, i.mode == MODE]
    if doc_error_only:
        cond.append(i.incident_type == DOCUMENT_ERROR)
    return exists(select(i.incident_id).where(*cond))


def kpi09_perfect_order(db, p, group, key):
    """Lô đúng hạn ∧ đủ ∧ nguyên vẹn ∧ không có sự cố `DOCUMENT_ERROR` ÷ lô đã giao (EV-142, EV-205)."""
    s = AsmFactShipment
    good = and_(
        s.delivered_at <= s.sla_due_at,
        s.in_full.is_(True),
        s.damage_free.is_(True),
        ~_has_incident(doc_error_only=True),
    )
    return _delivered(db, p, group, key, good)


def kpi10_incident_rate(db, p, group, key):
    """Lô có ≥ 1 sự cố ÷ số lô (lô tạo trong kỳ; EV-111, EV-205). Thấp là tốt."""
    s = AsmFactShipment
    return _rate(
        db,
        p,
        group,
        key,
        model=s,
        time_col=s.created_at,
        good=_has_incident(),
        cols={"contract": s.contract_id, "shipment": s.shipment_id},
    )


def training_only(db, p, group, key):
    """KPI-11, 12, 13 – chỉ chế độ `training` (người học, đáp án tình huống, học liệu) – không đo trên `production`."""
    return None


def kpi14_service_request_response(db, p, group, key):
    """Yêu cầu dịch vụ quyết định `decided_at ≤ response_due_at` ÷ yêu cầu đã quyết định (EV-113, EV-114);
    `details.median_response_hours` – trung vị giờ phản hồi."""
    r = AsmFactServiceRequest
    cols = {"contract": r.contract_id, "shipment": r.shipment_id, "staff": r.decided_by}
    out = _rate(
        db,
        p,
        group,
        key,
        model=r,
        time_col=r.decided_at,
        good=r.decided_at <= r.response_due_at,
        where=(r.response_due_at.is_not(None),),
        cols=cols,
    )
    medians = _median(
        db,
        p,
        group,
        key,
        model=r,
        time_col=r.decided_at,
        hours=_hours(r.decided_at - r.submitted_at),
        where=(r.submitted_at.is_not(None), r.response_due_at.is_not(None)),
        cols=cols,
    )
    for k, res in (out or {}).items():
        if medians and k in medians:
            res.details["median_response_hours"] = medians[k].value
    return out


FORMULAS: dict[str, Formula] = {
    "doc_first_time_right": kpi01_doc_first_time_right,
    "doc_processing_hours": kpi02_doc_processing_hours,
    "task_on_time": kpi03_task_on_time,
    "process_compliance": kpi04_process_compliance,
    "checkpoint_first_pass": kpi05_checkpoint_first_pass,
    "customs_clearance_hours": kpi06_customs_clearance_hours,
    "on_time_delivery": kpi07_on_time_delivery,
    "in_full_damage_free": kpi08_in_full_damage_free,
    "perfect_order": kpi09_perfect_order,
    "incident_rate": kpi10_incident_rate,
    "discrepancy_detection": training_only,
    "false_report": training_only,
    "learning_completion": training_only,
    "service_request_response": kpi14_service_request_response,
}


def definitions(db: Session) -> list:
    """Danh mục KPI đang dùng (bản `version` mới nhất của mỗi mã)."""
    from app.flows.assessment.models import AsmKpiDefinition as D

    rows = db.scalars(select(D).where(D.status == "active").order_by(D.kpi_code, D.version.desc())).all()
    latest: dict[str, Any] = {}
    for d in rows:
        latest.setdefault(d.kpi_code, d)
    return list(latest.values())


def compute(db: Session, p, group: str | None, key: int | None = None, defs: list | None = None) -> dict:
    """{khóa nhóm: {kpi_code: Result}} + `applicable` = mã KPI đo được ở cấp này."""
    defs = defs if defs is not None else definitions(db)
    out: dict[Any, dict[str, Result]] = {}
    applicable: set[str] = set()
    for d in defs:
        fn = FORMULAS.get(d.formula_key)
        res = fn(db, p, group, key) if fn and MODE in d.modes else None
        if res is None:
            continue
        applicable.add(d.kpi_code)
        for k, r in res.items():
            out.setdefault(k, {})[d.kpi_code] = r
    return {"values": out, "applicable": applicable, "defs": defs}


def kpis_out(computed: dict, k: Any) -> dict[str, dict[str, Any]]:
    """Khối `kpis` của một đối tượng: mọi KPI trong danh mục (không đo được ở cấp này → `applicable = false`)."""
    values = computed["values"].get(k, {})
    out = {}
    for d in computed["defs"]:
        r = values.get(d.kpi_code)
        base = r.out() if r else {"value": None, "numerator": 0, "denominator": 0, "details": None}
        out[d.kpi_code] = {
            "name": d.name,
            "unit": d.unit,
            "direction": d.direction,
            "applicable": d.kpi_code in computed["applicable"],
            **base,
        }
    return out
