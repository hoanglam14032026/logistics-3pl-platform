"""Luồng 2 nhận sự kiện từ outbox (Giai đoạn 11) – đăng ký với bộ nhận dùng chung (app/shared/events/consumer.py).

Mỗi xử lý chạy trong giao dịch của bộ nhận cùng dòng `sys_event_consumptions` (consumer `control`) – không xử lý trùng.
"""

from app.flows.control import kpi_alerts, service, tracking
from app.shared.events import consumer

CONSUMER = "control"

consumer.handler(CONSUMER, "EV-101")(service.on_contract_activated)
consumer.handler(CONSUMER, "EV-111")(service.on_shipment_created)
consumer.handler(CONSUMER, "EV-112")(service.on_shipment_status_changed)
# Giai đoạn 13: bước đổi ở Luồng 1 → đồng bộ về Luồng 2 (nguồn chuẩn)
for _code in ("EV-131", "EV-132", "EV-133", "ops.task.unblocked"):
    consumer.handler(CONSUMER, _code)(tracking.apply_from_ops)
consumer.handler(CONSUMER, "EV-403")(kpi_alerts.on_kpi_alert)  # Giai đoạn 15: cảnh báo KPI của Luồng 4
