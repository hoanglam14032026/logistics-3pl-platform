"""
Luồng 2 nhận cảnh báo KPI EV-403 của Luồng 4 (Giai đoạn 15; 03 M-07) → cảnh báo `kpi_below_target` trong `ctl_alerts`
(không gắn lô) cho mọi `manager` và `business_manager`: xem / xác nhận / đóng qua `/api/v2/control/alerts`, gửi email /
Zalo qua hàng đợi như Giai đoạn 13, nhật ký `alert.raise`. Không leo thang BR-205 (đã gửi business_manager ngay).
Sự kiện `training` bỏ qua (BR-01). Một cảnh báo chưa xử lý cho mỗi (đối tượng, KPI, kỳ).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import now_utc
from app.flows.control import notify
from app.flows.control.models import AlertStatus, AlertType, CtlAlert, CtlContractPlan
from app.shared.audit import service as audit

PURPOSE = "kpi"


def on_kpi_alert(db: Session, envelope: dict[str, Any]) -> dict[str, Any]:
    d = envelope["data"]
    if envelope.get("mode") == "training":
        return {"raised": False, "reason": "training"}
    key = f"kpi:{d['subject_type']}:{d['subject_id']}:{d['kpi_code']}:{d['period']}:{d['period_start']}"
    existing = db.scalar(
        select(CtlAlert)
        .where(CtlAlert.dedup_key == key, CtlAlert.status != AlertStatus.RESOLVED.value)
        .with_for_update()
    )
    if existing is not None:
        return {"raised": False, "reason": "already_open", "alert_id": existing.id}
    contract_id = d["subject_id"] if d["subject_type"] == "contract" else None
    plan_id = None
    if contract_id is not None:
        plan_id = db.scalar(select(CtlContractPlan.id).where(CtlContractPlan.contract_id == contract_id))
    who = d.get("subject_label") or f"{d['subject_type']} {d['subject_id']}"
    message = (
        f"KPI {d['kpi_code']} ({d['kpi_name']}) của {who} kỳ {d['period_start']}…{d['period_end']}: "
        f"{d['value']} {d['unit']} – {'dưới' if d['direction'] == 'higher_better' else 'trên'} "
        f"{'ngưỡng cảnh báo' if d['severity'] == 'critical' else 'mục tiêu'} {d['threshold']} (mục tiêu {d['target']})"
    )
    alert = CtlAlert(
        alert_type=AlertType.KPI_BELOW_TARGET.value,
        severity=d["severity"],
        dedup_key=key,
        shipment_id=None,
        contract_id=contract_id,
        plan_id=plan_id,
        recipient_id=None,
        message=message[:500],
        details={**d, "source_event_id": envelope["event_id"]},
        status=AlertStatus.OPEN.value,
        raised_at=now_utc(),
    )
    db.add(alert)
    db.flush()
    audit.record(
        db,
        "alert.raise",
        "alert",
        alert.id,
        None,
        after={
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "contract_id": contract_id,
            "kpi_code": d["kpi_code"],
            "value": d["value"],
            "target": d["target"],
            "period": d["period"],
            "period_start": d["period_start"],
            "source_event_id": envelope["event_id"],
        },
    )
    sent = notify.queue(db, alert, PURPOSE)
    return {"raised": True, "alert_id": alert.id, "notifications": len(sent)}
