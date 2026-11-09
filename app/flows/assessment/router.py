"""
API báo cáo KPI vận hành `/api/v2/assessment/kpi…` (Giai đoạn 14; 05 ASM-F10, 06 §4 "L4 – Kết quả KPI vận hành").

Quyền: chỉ `manager`, `business_manager`, `admin` (vai trò khác, `customer` → 403). Chỉ dữ liệu `production` (BR-01).
Tham số kỳ `from`, `to` (ngày, giờ Việt Nam, gồm cả hai đầu; mặc định 30 ngày tới hôm nay; tối đa 366 ngày).

- `GET /kpi` – toàn bộ · `GET /kpi/contracts`, `/kpi/contracts/{id}` – theo hợp đồng (kèm so với SLA) ·
  `GET /kpi/shipments`, `/kpi/shipments/{id}` – theo lô · `GET /kpi/staff`, `/kpi/staff/{user_id}` – theo nhân viên.
"""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.core.pagination import PageParams
from app.core.rbac import Role, require_roles
from app.core.timeutil import now_utc
from app.flows.assessment import calc, formulas, kpi
from app.flows.assessment.models import (
    AsmCalcRun,
    AsmFactContract,
    AsmFactKpiMeasurement,
    AsmFactShipment,
    AsmFactTask,
    AsmKpiAlert,
    AsmKpiTarget,
)
from app.flows.assessment.schemas import (
    CalcRunIn,
    CalcRunOut,
    ContractKpiDetail,
    ContractKpiList,
    ContractKpiOut,
    KpiAlertOut,
    KpiDefinitionOut,
    KpiSummaryOut,
    KpiTargetOut,
    MeasurementOut,
    ShipmentKpiDetail,
    ShipmentKpiList,
    ShipmentKpiOut,
    StaffKpiDetail,
    StaffKpiList,
    StaffKpiOut,
)
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/assessment", tags=["Assessment – KPI"])

_viewer = require_roles(Role.MANAGER, Role.BUSINESS_MANAGER, Role.ADMIN)
_admin = require_roles(Role.ADMIN)


def _period(
    date_from: date | None = Query(None, alias="from", description="Ngày đầu kỳ (YYYY-MM-DD, giờ Việt Nam)"),
    date_to: date | None = Query(None, alias="to", description="Ngày cuối kỳ (gồm cả ngày này)"),
) -> kpi.Period:
    return kpi.period(date_from, date_to)


def _not_found(what: str) -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, f"Không có số liệu KPI của {what}")


def _head(p: kpi.Period) -> dict[str, Any]:
    return {"period": p.out(), "mode": kpi.MODE}


@router.get("/kpi", response_model=KpiSummaryOut, summary="KPI vận hành toàn bộ trong kỳ")
def summary(
    p: kpi.Period = Depends(_period),  # noqa: B008
    _: SysUser = Depends(_viewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return {**_head(p), "generated_at": now_utc(), "metrics": kpi.metrics(db, p)[0]}


# --- Theo hợp đồng ----------------------------------------------------------------------------------------------


def _contract_out(db: Session, c: AsmFactContract | None, contract_id: int, m: dict[str, Any]) -> dict[str, Any]:
    return {
        "targets": kpi.targets_for(db, contract_id, m),
        "contract_id": contract_id,
        "contract_no": c.contract_no if c else None,
        "customer_id": c.customer_id if c else None,
        "valid_from": c.valid_from if c else None,
        "valid_to": c.valid_to if c else None,
        "metrics": m,
        "sla": kpi.sla_compliance(c.sla if c else {}, m),
    }


@router.get("/kpi/contracts", response_model=ContractKpiList, summary="KPI theo hợp đồng (so với SLA)")
def by_contract(
    p: kpi.Period = Depends(_period),  # noqa: B008
    _: SysUser = Depends(_viewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    valid = {c.contract_id for c in kpi.contracts_in_scope(db, p, set())}
    data = kpi.metrics(db, p, "contract", extra_keys=valid)
    contracts = {c.contract_id: c for c in kpi.contracts_in_scope(db, p, set(data))}
    items = [ContractKpiOut(**_contract_out(db, contracts.get(cid), cid, data[cid])) for cid in sorted(data)]
    return {**_head(p), "items": items, "total": len(items)}


@router.get("/kpi/contracts/{contract_id:int}", response_model=ContractKpiDetail, summary="KPI một hợp đồng")
def contract_detail(
    contract_id: int,
    p: kpi.Period = Depends(_period),  # noqa: B008
    _: SysUser = Depends(_viewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    c = db.get(AsmFactContract, contract_id)
    if c is None or c.mode != kpi.MODE:
        raise _not_found("hợp đồng")
    m = kpi.metrics(db, p, "contract", contract_id)[contract_id]
    return {**_head(p), **_contract_out(db, c, contract_id, m)}


# --- Theo lô ----------------------------------------------------------------------------------------------------


def _shipment_out(s: AsmFactShipment | None, shipment_id: int, m: dict[str, Any]) -> dict[str, Any]:
    on_time = None
    if s and s.delivered_at and s.sla_due_at:
        on_time = s.delivered_at <= s.sla_due_at
    return {
        "shipment_id": shipment_id,
        "shipment_no": s.shipment_no if s else None,
        "contract_id": s.contract_id if s else None,
        "status": s.status if s else None,
        "required_delivery_at": s.required_delivery_at if s else None,
        "delivered_at": s.delivered_at if s else None,
        "sla_due_at": s.sla_due_at if s else None,
        "on_time": on_time,
        "metrics": m,
    }


@router.get("/kpi/shipments", response_model=ShipmentKpiList, summary="KPI theo lô (lô có số liệu / tạo trong kỳ)")
def by_shipment(
    contract_id: int | None = Query(None, description="Lọc theo hợp đồng"),
    p: kpi.Period = Depends(_period),  # noqa: B008
    page: PageParams = Depends(),  # noqa: B008
    _: SysUser = Depends(_viewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    created = set(
        db.scalars(
            select(AsmFactShipment.shipment_id).where(
                AsmFactShipment.mode == kpi.MODE,
                AsmFactShipment.created_at >= p.start,
                AsmFactShipment.created_at < p.end,
            )
        )
    )
    data = kpi.metrics(db, p, "shipment", extra_keys=created)
    info = kpi.shipments_info(db, set(data))
    ids = sorted(
        (
            sid
            for sid in data
            if (sid in info and info[sid].mode == kpi.MODE)
            and (contract_id is None or info[sid].contract_id == contract_id)
        ),
        reverse=True,
    )
    start = (page.page - 1) * page.page_size
    items = [
        ShipmentKpiOut(**_shipment_out(info.get(sid), sid, data[sid])) for sid in ids[start : start + page.page_size]
    ]
    return {**_head(p), "items": items, "total": len(ids), "page": page.page, "page_size": page.page_size}


@router.get("/kpi/shipments/{shipment_id:int}", response_model=ShipmentKpiDetail, summary="KPI một lô")
def shipment_detail(
    shipment_id: int,
    p: kpi.Period = Depends(_period),  # noqa: B008
    _: SysUser = Depends(_viewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    s = db.get(AsmFactShipment, shipment_id)
    if s is None or s.mode != kpi.MODE:
        raise _not_found("lô hàng")
    return {**_head(p), **_shipment_out(s, shipment_id, kpi.metrics(db, p, "shipment", shipment_id)[shipment_id])}


# --- Theo nhân viên ---------------------------------------------------------------------------------------------


def _users(db: Session, ids: set[int]) -> dict[int, SysUser]:
    if not ids:
        return {}
    return {u.id: u for u in db.scalars(select(SysUser).where(SysUser.id.in_(ids)))}


def _staff_out(u: SysUser | None, user_id: int, m: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "username": u.username if u else None,
        "full_name": u.full_name if u else None,
        "metrics": m,
    }


@router.get("/kpi/staff", response_model=StaffKpiList, summary="KPI theo nhân viên (người được giao bước)")
def by_staff(
    p: kpi.Period = Depends(_period),  # noqa: B008
    _: SysUser = Depends(_viewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    data = kpi.metrics(db, p, "staff")
    users = _users(db, set(data))
    items = [StaffKpiOut(**_staff_out(users.get(uid), uid, data[uid])) for uid in sorted(data)]
    return {**_head(p), "items": items, "total": len(items)}


@router.get("/kpi/staff/{user_id:int}", response_model=StaffKpiDetail, summary="KPI một nhân viên")
def staff_detail(
    user_id: int,
    p: kpi.Period = Depends(_period),  # noqa: B008
    _: SysUser = Depends(_viewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    user = db.get(SysUser, user_id)
    m = kpi.metrics(db, p, "staff", user_id)[user_id]
    known = db.scalar(
        select(AsmFactTask.task_id).where((AsmFactTask.assignee_id == user_id) | (AsmFactTask.performer_id == user_id))
    )
    if user is None and known is None and not any(k["denominator"] for k in m["kpis"].values()):
        raise _not_found("nhân viên")
    return {**_head(p), **_staff_out(user, user_id, m)}


# --- Giai đoạn 15: danh mục, mục tiêu, số đo định kỳ, lần tính, cảnh báo KPI ----------------------------------------


@router.get("/kpi/definitions", response_model=list[KpiDefinitionOut], summary="Danh mục KPI-01…14")
def list_definitions(_: SysUser = Depends(_viewer), db: Session = Depends(get_db)):  # noqa: B008
    return formulas.definitions(db)


@router.get("/kpi/targets", response_model=list[KpiTargetOut], summary="Mục tiêu KPI theo hợp đồng (từ EV-201)")
def list_targets(
    contract_id: int | None = Query(None, gt=0),
    _: SysUser = Depends(_viewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    t = AsmKpiTarget
    stmt = select(t).where(t.mode == kpi.MODE).order_by(t.subject_id, t.kpi_code, t.valid_from.desc(), t.id.desc())
    if contract_id is not None:
        stmt = stmt.where(t.subject_id == contract_id)
    current = {r.id for r in kpi.current_targets(db, {contract_id} if contract_id else None).values()}
    return [KpiTargetOut.model_validate(r).model_copy(update={"current": r.id in current}) for r in db.scalars(stmt)]


@router.get(
    "/kpi/measurements",
    response_model=list[MeasurementOut],
    summary="Số đo KPI đã lưu (tính định kỳ) – mặc định chỉ lần tính mới nhất của mỗi kỳ",
)
def list_measurements(
    kpi_code: str | None = Query(None, max_length=16),
    subject_type: str | None = Query(None, pattern=r"^(all|contract|staff)$"),
    subject_id: int | None = Query(None, ge=0),
    period_type: str | None = Query(None, pattern=r"^(week|month|quarter)$"),
    p: kpi.Period = Depends(_period),  # noqa: B008
    history: bool = Query(False, description="true: mọi lần tính (kể cả bản cũ)"),
    _: SysUser = Depends(_viewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    m = AsmFactKpiMeasurement
    stmt = select(m).where(m.mode == kpi.MODE, m.period_end >= p.date_from, m.period_end <= p.date_to)
    for col, value in ((m.kpi_code, kpi_code), (m.subject_type, subject_type), (m.subject_id, subject_id)):
        if value is not None:
            stmt = stmt.where(col == value)
    if period_type:
        stmt = stmt.where(m.period_type == period_type)
    keys = (m.kpi_code, m.subject_type, m.subject_id, m.period_type, m.period_start, m.period_end)
    if not history:
        stmt = stmt.distinct(*keys).order_by(*keys, m.calc_run_id.desc())
    else:
        stmt = stmt.order_by(*keys, m.calc_run_id.desc())
    return db.scalars(stmt.limit(5000)).all()


@router.get("/kpi/calc-runs", response_model=list[CalcRunOut], summary="Các lần tính KPI")
def list_calc_runs(_: SysUser = Depends(_viewer), db: Session = Depends(get_db)):  # noqa: B008
    return db.scalars(select(AsmCalcRun).order_by(AsmCalcRun.id.desc()).limit(100)).all()


@router.post(
    "/kpi/calc-runs",
    response_model=CalcRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Tính KPI ngay (admin) – lưu số đo, cảnh báo EV-403 như lần tính hằng ngày",
)
def create_calc_run(body: CalcRunIn | None = None, actor: SysUser = Depends(_admin)):  # noqa: B008
    as_of = body.as_of if body else None
    if as_of is not None and as_of > kpi.today_vn():
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            details=[{"field": "body.as_of", "issue": "in_future", "message": "as_of không được sau hôm nay"}],
        )
    try:
        return calc.run_calc("manual", as_of, actor.id)
    except RuntimeError as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, 500, "Tính KPI lỗi – xem nhật ký") from exc


@router.get("/kpi/alerts", response_model=list[KpiAlertOut], summary="Cảnh báo KPI đã phát (EV-403)")
def list_kpi_alerts(
    contract_id: int | None = Query(None, gt=0),
    _: SysUser = Depends(_viewer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    stmt = select(AsmKpiAlert).order_by(AsmKpiAlert.id.desc()).limit(500)
    if contract_id is not None:
        stmt = stmt.where(AsmKpiAlert.subject_type == "contract", AsmKpiAlert.subject_id == contract_id)
    return db.scalars(stmt).all()
