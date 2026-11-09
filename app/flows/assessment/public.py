"""
Giao diện đọc công bố của Luồng 4 (01 §6 – `get_kpi_values`; BR-05: KPI chỉ tính ở Luồng 4) – Giai đoạn 19.

Luồng khác (vd. quyết toán Luồng 1) gọi hàm ở đây, không đọc bảng `asm_*`, không tự tính KPI. Trả DTO thuần (dict),
không trả đối tượng ORM.
"""

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.flows.assessment import kpi


def get_kpi_values(db: Session, contract_id: int, date_from: date, date_to: date) -> dict[str, Any]:
    """KPI vận hành của hợp đồng trong kỳ + so với mục tiêu hiện hành (EV-201) và SLA (EV-101)."""
    p = kpi.Period(date_from, date_to)
    m = kpi.metrics(db, p, "contract", contract_id)[contract_id]
    from app.flows.assessment.models import AsmFactContract

    fact = db.get(AsmFactContract, contract_id)
    return {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "kpis": {
            code: {k: v.get(k) for k in ("value", "numerator", "denominator", "unit", "direction")}
            for code, v in m["kpis"].items()
            if v["applicable"]
        },
        "targets": [
            {**t, "approved_at": t["approved_at"].isoformat() if t.get("approved_at") else None}
            for t in kpi.targets_for(db, contract_id, m)
        ],
        "sla": kpi.sla_compliance(fact.sla if fact else {}, m),
        "below_target": sorted(
            t["kpi_code"] for t in kpi.targets_for(db, contract_id, m) if t["status"] in ("warning", "critical")
        ),
    }
