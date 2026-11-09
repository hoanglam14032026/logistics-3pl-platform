"""
Hàng đợi tác vụ nền bền vững (Giai đoạn 7) – `rq` trên Redis (`JOB_QUEUE_URL`).

- Có `JOB_QUEUE_URL`: tác vụ lưu trong Redis (bật AOF – `docker-compose.yml`), chạy ở tiến trình worker riêng
  (`python -m app.worker`). Tiến trình API dừng sau khi xếp tác vụ: không mất. Worker chết giữa chừng: rq phát hiện
  tác vụ bị bỏ dở (StartedJobRegistry hết hạn) và chạy lại nếu còn lượt thử. Lỗi → thử lại theo
  `JOB_MAX_RETRIES`, `JOB_RETRY_INTERVALS`; hết lượt → FailedJobRegistry (giữ `JOB_FAILURE_TTL_DAYS` ngày).
- Chưa đặt `JOB_QUEUE_URL` (máy dev, test) hoặc Redis lỗi lúc xếp: chạy nền trong tiến trình API (FastAPI
  `BackgroundTasks`, sau khi trả response) – một lần, không thử lại, mất nếu tiến trình dừng. Redis lỗi được ghi log.

Tác vụ là hàm cấp module (rq lưu đường dẫn hàm + tham số), tham số chỉ là id – **không** đưa token/bí mật vào Redis:
tác vụ tự đọc CSDL và cấp token lúc gửi. rq chạy "ít nhất một lần": tác vụ phải chịu được việc chạy lặp.
"""

import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Literal, Protocol

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Retry, get_current_job

from app.core.config import Settings, get_settings

logger = logging.getLogger("app.jobs")

QUEUE_NAME = "default"


class TaskQueue(Protocol):
    """Nơi xếp tác vụ nền trong tiến trình – FastAPI `BackgroundTasks` thỏa giao diện này."""

    def add_task(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None: ...


@lru_cache(maxsize=4)
def redis_connection(url: str) -> Redis:
    return Redis.from_url(url, socket_connect_timeout=5, socket_timeout=10, health_check_interval=30)


def get_queue(settings: Settings | None = None, url: str | None = None) -> Queue | None:
    settings = settings or get_settings()
    url = url or settings.job_queue_url
    if not url:
        return None
    return Queue(QUEUE_NAME, connection=redis_connection(url))


def enqueue(
    tasks: TaskQueue, func: Callable[..., Any], *args: Any, timeout: int | None = None
) -> Literal["queue", "background"]:
    """Xếp tác vụ. Gọi **sau** khi commit dữ liệu tác vụ cần đọc. Trả nơi tác vụ được xếp.

    `timeout`: giây tối đa một lần chạy trong worker (mặc định `JOB_TIMEOUT_SEC`)."""
    settings = get_settings()
    queue = get_queue(settings)
    if queue is not None:
        retry = Retry(max=settings.job_max_retries, interval=list(settings.job_retry_intervals))
        try:
            queue.enqueue(
                func,
                *args,
                retry=retry if settings.job_max_retries > 0 else None,
                job_timeout=timeout or settings.job_timeout_sec,
                failure_ttl=settings.job_failure_ttl_days * 86400,
                result_ttl=3600,
                meta={"max_retries": settings.job_max_retries},
            )
            return "queue"
        except RedisError as exc:
            logger.error(
                "Không xếp được %s vào hàng đợi (%s) – chạy nền trong tiến trình API, không thử lại",
                func.__name__,
                type(exc).__name__,
            )
    tasks.add_task(func, *args)
    return "background"


def in_worker() -> bool:
    """Đang chạy trong worker rq (lỗi sẽ được thử lại) hay nền trong tiến trình API."""
    return get_current_job() is not None


def attempt() -> tuple[int, bool]:
    """(lần thử hiện tại tính từ 1, có phải lần cuối không). Ngoài worker: (1, True)."""
    job = get_current_job()
    if job is None:
        return 1, True
    left = job.retries_left or 0
    return job.meta.get("max_retries", 0) - left + 1, left == 0
