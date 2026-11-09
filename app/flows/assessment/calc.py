"""
Tính KPI định kỳ + cảnh báo KPI EV-403 (Giai đoạn 15; 05 BR-407, ASM-F02, ASM-F10).

- Worker (`python -m app.worker`) xếp tác vụ rq `scheduled_calc` lúc `ASSESSMENT_KPI_CALC_HOUR` giờ Việt Nam mỗi ngày
  (mặc định 01:00); tác vụ tính cho **hôm qua** rồi tự xếp lần sau. `POST /api/v2/assessment/kpi/calc-runs` (admin) tính
  ngay. Lỗi → `asm_calc_runs.status = failed` + log; trong worker ném lỗi để rq thử lại như Giai đoạn 7.
- Mỗi lần tính (`asm_calc_runs`): ba kỳ kết thúc ở ngày `as_of` – tuần (từ thứ Hai), tháng, quý (tính tới ngày đó) –
  cho đối tượng `all`, `contract`, `staff`; mỗi KPI đo được (mẫu số > 0) → một dòng `asm_fact_kpi_measurements` (chỉ
  thêm; tính lại = dòng mới với `calc_run_id` mới).
- So với mục tiêu hiện hành của hợp đồng (EV-201 → `asm_kpi_targets`): kém hơn mục tiêu → cảnh báo `warning`, kém hơn
  ngưỡng cảnh báo (`warning_threshold`) → `critical`. Mỗi (KPI, hợp đồng, kỳ) cảnh báo **một lần** (`asm_kpi_alerts`):
  phát EV-403 (Luồng 2 tạo cảnh báo + email / Zalo cho manager, business_manager) + nhật ký `kpi.alert`.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import jobs
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.timeutil import VN_TZ, now_utc, today_vn
from app.flows.assessment import formulas, kpi
from app.flows.assessment.models import AsmCalcRun, AsmFactContract, AsmFactKpiMeasurement, AsmKpiAlert
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import KpiAlert

logger = logging.getLogger("app.assessment.calc")

SUBJECTS = (("all", None), ("contract", "contract"), ("staff", "staff"))


def periods(as_of: date) -> list[tuple[str, kpi.Period]]:
    quarter_start = date(as_of.year, 3 * ((as_of.month - 1) // 3) + 1, 1)
    return [
        ("week", kpi.Period(as_of - timedelta(days=as_of.weekday()), as_of)),
        ("month", kpi.Period(as_of.replace(day=1), as_of)),
        ("quarter", kpi.Period(quarter_start, as_of)),
    ]


def _measure(db: Session, run: AsmCalcRun, defs: list) -> list[AsmFactKpiMeasurement]:
    versions = {d.kpi_code: d.version for d in defs}
    rows: list[AsmFactKpiMeasurement] = []
    for period_type, p in periods(run.as_of):
        for subject_type, group in SUBJECTS:
            computed = formulas.compute(db, p, group, defs=defs)
            for subject_id, results in computed["values"].items():
                for code, r in results.items():
                    if not r.denominator:
                        continue
                    rows.append(
                        AsmFactKpiMeasurement(
                            calc_run_id=run.id,
                            kpi_code=code,
                            kpi_version=versions[code],
                            subject_type=subject_type,
                            subject_id=subject_id,
                            mode=kpi.MODE,
                            period_type=period_type,
                            period_start=p.date_from,
                            period_end=p.date_to,
                            numerator=Decimal(str(r.numerator)),
                            denominator=r.denominator,
                            value=None if r.value is None else Decimal(str(r.value)),
                            details=r.details or None,
                        )
                    )
    db.add_all(rows)
    db.flush()
    return rows


def _alert(db: Session, run: AsmCalcRun, m: AsmFactKpiMeasurement, target, d, severity: str) -> AsmKpiAlert | None:
    threshold = target.warning_threshold if severity == "critical" else target.target_value
    alert_id = db.execute(
        insert(AsmKpiAlert)
        .values(
            kpi_code=m.kpi_code,
            subject_type=m.subject_type,
            subject_id=m.subject_id,
            period_type=m.period_type,
            period_start=m.period_start,
            period_end=m.period_end,
            value=m.value,
            target=target.target_value,
            warning_threshold=target.warning_threshold,
            severity=severity,
            calc_run_id=run.id,
            measurement_id=m.id,
            target_id=target.id,
            event_id="00000000-0000-0000-0000-000000000000",  # thay bằng event_id của EV-403 ngay bên dưới
        )
        .on_conflict_do_nothing(constraint="uq_asm_kpi_alerts_period")
        .returning(AsmKpiAlert.id)
    ).scalar()
    if alert_id is None:  # đã cảnh báo trong kỳ này
        return None
    alert = db.get(AsmKpiAlert, alert_id)
    contract = db.get(AsmFactContract, m.subject_id)
    event = KpiAlert(
        alert_id=alert.id,
        subject_type=m.subject_type,
        subject_id=m.subject_id,
        subject_label=f"hợp đồng {contract.contract_no}" if contract else None,
        kpi_code=m.kpi_code,
        kpi_name=d.name,
        unit=d.unit,
        direction=d.direction,
        value=m.value,
        target=target.target_value,
        threshold=threshold,
        severity=severity,
        period=m.period_type,
        period_start=m.period_start,
        period_end=m.period_end,
    )
    body = publisher.publish(db, event, "kpi_alert", alert.id, None, kpi.MODE)
    alert.event_id = body["event_id"]
    audit.record(
        db,
        "kpi.alert",
        "kpi_alert",
        alert.id,
        None,
        after={
            "kpi_code": m.kpi_code,
            "subject_type": m.subject_type,
            "subject_id": m.subject_id,
            "period": m.period_type,
            "period_start": m.period_start.isoformat(),
            "period_end": m.period_end.isoformat(),
            "value": str(m.value),
            "target": str(target.target_value),
            "warning_threshold": str(target.warning_threshold) if target.warning_threshold is not None else None,
            "severity": severity,
            "plan_id": target.source_plan_id,
            "calc_run_id": run.id,
        },
    )
    logger.warning(
        "KPI %s hợp đồng %s kỳ %s %s…%s = %s (mục tiêu %s) – %s",
        m.kpi_code,
        m.subject_id,
        m.period_type,
        m.period_start,
        m.period_end,
        m.value,
        target.target_value,
        severity,
    )
    return alert


def _check_targets(db: Session, run: AsmCalcRun, rows: list[AsmFactKpiMeasurement], defs: list) -> dict[str, int]:
    by_code = {d.kpi_code: d for d in defs}
    contract_rows = [r for r in rows if r.subject_type == "contract"]
    targets = kpi.current_targets(db, {r.subject_id for r in contract_rows})
    stats = {"checked": 0, "breached": 0, "alerts": 0}
    for m in contract_rows:
        target = targets.get((m.subject_id, m.kpi_code))
        if target is None or m.value is None:
            continue
        stats["checked"] += 1
        status = kpi.evaluate(by_code[m.kpi_code].direction, m.value, target.target_value, target.warning_threshold)
        if status not in ("warning", "critical"):
            continue
        stats["breached"] += 1
        if _alert(db, run, m, target, by_code[m.kpi_code], status):
            stats["alerts"] += 1
    return stats


def run_calc(trigger: str, as_of: date | None = None, requested_by: int | None = None) -> AsmCalcRun:
    """Một lần tính (giao dịch riêng). Lỗi → dòng `failed` (giao dịch khác) rồi ném lại."""
    as_of = as_of or today_vn() - timedelta(days=1)
    with SessionLocal() as db:
        run = AsmCalcRun(trigger=trigger, as_of=as_of, status="running", requested_by=requested_by)
        db.add(run)
        try:
            db.flush()
            defs = formulas.definitions(db)
            rows = _measure(db, run, defs)
            stats: dict[str, Any] = {"measurements": len(rows), **_check_targets(db, run, rows, defs)}
            run.status, run.finished_at, run.stats = "succeeded", now_utc(), stats
            db.commit()
            db.refresh(run)
            logger.info("Tính KPI ngày %s (%s): %s", as_of, trigger, stats)
            return run
        except Exception as exc:
            db.rollback()
            error = f"{type(exc).__name__}: {exc}"[:1000]
            logger.exception("Tính KPI ngày %s lỗi", as_of)
    with SessionLocal() as db:
        failed = AsmCalcRun(
            trigger=trigger,
            as_of=as_of,
            status="failed",
            requested_by=requested_by,
            finished_at=now_utc(),
            error=error,
        )
        db.add(failed)
        db.flush()
        audit.record(
            db,
            "kpi.calc_failed",
            "calc_run",
            failed.id,
            requested_by,
            after={"as_of": as_of.isoformat(), "error": error},
        )
        db.commit()
    raise RuntimeError(error)


# --- Lịch hằng ngày (rq) -------------------------------------------------------------------------------------------


def next_run_at(now=None):
    """Lần chạy kế tiếp lúc `ASSESSMENT_KPI_CALC_HOUR`:00 giờ Việt Nam (sau `now`)."""
    from datetime import datetime, time

    now = (now or now_utc()).astimezone(VN_TZ)
    at = datetime.combine(now.date(), time(get_settings().assessment_kpi_calc_hour), VN_TZ)
    return at if at > now else at + timedelta(days=1)


def schedule_daily(now=None) -> bool:
    """Xếp tác vụ tính KPI lần kế tiếp (job id theo ngày – xếp lại cùng ngày không sinh trùng)."""
    from redis.exceptions import RedisError
    from rq import Queue, Retry

    settings = get_settings()
    if not settings.job_queue_url or not settings.assessment_kpi_calc_enabled:
        return False
    at = next_run_at(now)
    queue = Queue(jobs.QUEUE_NAME, connection=jobs.redis_connection(settings.job_queue_url))
    try:
        queue.enqueue_at(
            at,
            scheduled_calc,
            job_id=f"assessment-kpi-calc-{at.date().isoformat()}",
            retry=Retry(max=settings.job_max_retries, interval=list(settings.job_retry_intervals))
            if settings.job_max_retries > 0
            else None,
            job_timeout=settings.job_timeout_sec,
            failure_ttl=settings.job_failure_ttl_days * 86400,
            result_ttl=86400,
            meta={"max_retries": settings.job_max_retries},
        )
    except RedisError as exc:
        logger.error("Không xếp được tác vụ tính KPI hằng ngày (%s)", type(exc).__name__)
        return False
    return True


def scheduled_calc() -> dict[str, Any]:
    """Tác vụ rq: tính KPI cho hôm qua rồi xếp lần sau (kể cả khi lỗi – rq thử lại lần này)."""
    schedule_daily()
    run = run_calc("schedule")
    return {"run_id": run.id, **(run.stats or {})}
