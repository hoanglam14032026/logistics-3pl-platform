"""
Giao diện đọc công bố của Luồng 1 (01 N4) – Giai đoạn 20.

Luồng khác gọi hàm ở đây, không đọc bảng `ops_*`. Trả DTO thuần (dict), không trả đối tượng ORM.
"""

from datetime import date
from typing import Any

from sqlalchemy.orm import Session


def overdue_staff_advances(db: Session, today: date) -> list[dict[str, Any]]:
    """Tạm ứng đã chi (`disbursed` / `settling`) quá `due_date` mà chưa hoàn ứng – Luồng 2 cảnh báo M-10.
    Hợp đồng `training` không cảnh báo (BR-01)."""
    from app.flows.operations import advance_service
    from app.flows.operations.models import OpsServiceContract

    out = []
    for a in advance_service.overdue(db, today):
        contract = db.get(OpsServiceContract, a.contract_id)
        if contract is None or contract.mode != "production":
            continue
        out.append(
            {
                "advance_id": a.id,
                "advance_no": a.advance_no,
                "staff_id": a.staff_id,
                "contract_id": a.contract_id,
                "contract_no": contract.contract_no,
                "shipment_id": a.shipment_id,
                "amount": str(a.amount),
                "currency": a.currency,
                "due_date": a.due_date.isoformat(),
                "overdue_days": (today - a.due_date).days,
                "status": a.status,
            }
        )
    return out
