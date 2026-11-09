"""Luồng 2 nhận sự kiện từ outbox (Giai đoạn 11) – đăng ký với bộ nhận dùng chung (app/shared/events/consumer.py).

Mỗi xử lý chạy trong giao dịch của bộ nhận cùng dòng `sys_event_consumptions` (consumer `control`) – không xử lý trùng.
"""

from app.flows.control import service
from app.shared.events import consumer

CONSUMER = "control"

consumer.handler(CONSUMER, "EV-101")(service.on_contract_activated)
consumer.handler(CONSUMER, "EV-111")(service.on_shipment_created)
consumer.handler(CONSUMER, "EV-112")(service.on_shipment_status_changed)
