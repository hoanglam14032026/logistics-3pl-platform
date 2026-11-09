"""
Đăng ký xử lý sự kiện của các luồng (Giai đoạn 11) – nạp ở `create_app`, worker (`python -m app.worker`), CLI.

Mỗi luồng tự đăng ký trong `<luồng>/handlers.py` bằng `@consumer.handler("<luồng>", "<mã sự kiện>")`; `shared` không
import `flows` (N4) – module này ở gốc `app/` gom lại.
"""

from app.flows.control import handlers as _control_handlers  # noqa: F401  (Luồng 2: EV-101, EV-111, EV-112, EV-142)
from app.flows.operations import handlers as _operations_handlers  # noqa: F401  (Luồng 1: EV-202)
