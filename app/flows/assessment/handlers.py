"""Luồng 4 nhận sự kiện từ Redis Stream `l3pl:events:assessment` (Giai đoạn 14) – đăng ký với bộ nhận dùng chung
(app/shared/events/consumer.py, consumer `assessment`; `sys_event_consumptions` – không xử lý trùng).

Sự kiện khác gửi tới Luồng 4 (EV-102/103/113/114/122/141, `doc.*`…) chưa có xử lý → `skipped` (`no_handler`)."""

from app.flows.assessment import facts
from app.shared.events import consumer

CONSUMER = "assessment"

consumer.handler(CONSUMER, "EV-101")(facts.on_contract_activated)
consumer.handler(CONSUMER, "EV-111")(facts.on_shipment_created)
consumer.handler(CONSUMER, "EV-112")(facts.on_shipment_status_changed)
consumer.handler(CONSUMER, "EV-142")(facts.on_shipment_delivered)
for _code in ("EV-131", "EV-132", "EV-133", "ops.task.unblocked"):
    consumer.handler(CONSUMER, _code)(facts.on_ops_task_event)
consumer.handler(CONSUMER, "ctl.task.synced")(facts.on_task_synced)
for _code in ("ctl.task.started", "ctl.task.completed", "ctl.task.blocked", "ctl.task.delayed"):
    consumer.handler(CONSUMER, _code)(facts.on_ctl_task_event)
consumer.handler(CONSUMER, "ctl.alert.raised")(facts.on_alert_raised)
consumer.handler(CONSUMER, "ctl.alert.escalated")(facts.on_alert_escalated)
