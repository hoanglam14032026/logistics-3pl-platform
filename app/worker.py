"""
Worker tác vụ nền (Giai đoạn 7): `python -m app.worker` – chạy tác vụ trong hàng đợi rq `JOB_QUEUE_URL`.

- Kèm bộ lập lịch rq (`with_scheduler`): tác vụ thử lại sau `JOB_RETRY_INTERVALS` giây được đưa lại vào hàng đợi đúng
  hạn; tác vụ bị bỏ dở khi worker chết được chạy lại nếu còn lượt thử.
- Mỗi tác vụ chạy trong tiến trình con (fork) – lỗi/treo trong tác vụ không làm chết worker; quá `JOB_TIMEOUT_SEC` bị
  dừng và tính là lỗi.
- Chạy nhiều worker song song được (nhiều container). Dừng bằng SIGTERM: worker chờ tác vụ đang chạy xong.
- Worker không chạy migration: khởi động sau khi API đã đưa CSDL lên head.
"""

import logging
import sys

from rq import Worker

from app.core.config import get_settings
from app.core.jobs import QUEUE_NAME, redis_connection


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if not settings.job_queue_url:
        logging.getLogger("app.worker").error("Chưa đặt JOB_QUEUE_URL – không có hàng đợi để chạy")
        return 2
    worker = Worker([QUEUE_NAME], connection=redis_connection(settings.job_queue_url))
    worker.work(with_scheduler=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
