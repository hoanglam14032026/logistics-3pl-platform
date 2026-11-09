"""
Bộ phát outbox `sys_events` (Giai đoạn 10; 01 §4, N5, N6) – gửi tiếp sự kiện cho Luồng 2 (`control`), Luồng 4
(`assessment`) qua rq + Redis.

1. Sau mỗi commit có sự kiện mới (sự kiện `after_commit` của phiên SQLAlchemy): xếp tác vụ `dispatch_events(ids)` vào
   hàng đợi rq `events` (`JOB_QUEUE_URL`) – tham số chỉ là id dòng outbox. Redis lỗi lúc xếp / chưa có `JOB_QUEUE_URL`:
   ghi log, sự kiện **chờ trong outbox** (không mất) – gửi lại khi API/worker khởi động hoặc bằng
   `python -m app.shared.events.cli dispatch`.
2. Worker (`python -m app.worker`, nghe cả hàng `default` và `events`): mỗi luồng nhận đang bật (ENABLED_FLOWS) →
   `XADD` phong bì vào Redis Stream `EVENT_STREAM_PREFIX<luồng>` (vd. `l3pl:events:control`) – Luồng 2, 4 đọc stream
   này (consumer group), xử lý idempotent theo `event_id` (N5). Gửi xong mọi luồng → `sent`.
3. Lỗi: ghi log, `last_error`, thử lại `JOB_MAX_RETRIES` lần theo `JOB_RETRY_INTERVALS` (như Giai đoạn 7); hết lượt →
   `failed` + nhật ký `outbox.dispatch_failed`, tác vụ vào FailedJobRegistry. Luồng nhận đang tắt: sự kiện giữ
   `pending` cho luồng đó (N6) – bật lại rồi gửi lại.
"""

import json
import logging
from collections.abc import Iterable

from redis.exceptions import RedisError
from rq import Queue, Retry
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from app.core import jobs
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.timeutil import now_utc, to_utc_iso
from app.shared.audit import service as audit
from app.shared.events.models import EventStatus, SysEvent
from app.shared.events.publisher import PENDING_KEY

logger = logging.getLogger("app.events")

EVENTS_QUEUE = "events"
BATCH = 100


class DispatchError(Exception):
    """Còn sự kiện chưa gửi được – rq thử lại."""


def stream_key(consumer: str) -> str:
    return f"{get_settings().event_stream_prefix}{consumer}"


# --- Xếp tác vụ sau commit -----------------------------------------------------------------------------------------


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    pending = session.info.pop(PENDING_KEY, None)
    if pending:
        ids = [p if isinstance(p, int) else p.id for p in pending]
        schedule([i for i in ids if i is not None])


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(PENDING_KEY, None)


def schedule(event_ids: Iterable[int]) -> bool:
    """Xếp tác vụ gửi; trả False nếu không xếp được (sự kiện vẫn chờ trong outbox)."""
    ids = list(event_ids)
    if not ids:
        return True
    settings = get_settings()
    if not settings.job_queue_url:
        logger.debug("Chưa đặt JOB_QUEUE_URL – %d sự kiện chờ trong outbox", len(ids))
        return False
    queue = Queue(EVENTS_QUEUE, connection=jobs.redis_connection(settings.job_queue_url))
    retry = Retry(max=settings.job_max_retries, interval=list(settings.job_retry_intervals))
    try:
        for i in range(0, len(ids), BATCH):
            queue.enqueue(
                dispatch_events,
                ids[i : i + BATCH],
                retry=retry if settings.job_max_retries > 0 else None,
                job_timeout=settings.job_timeout_sec,
                failure_ttl=settings.job_failure_ttl_days * 86400,
                result_ttl=3600,
                meta={"max_retries": settings.job_max_retries},
            )
    except RedisError as exc:
        logger.error(
            "Không xếp được tác vụ gửi %d sự kiện (%s) – sự kiện chờ trong outbox, gửi lại khi khởi động / CLI",
            len(ids),
            type(exc).__name__,
        )
        return False
    return True


def schedule_pending(include_failed: bool = False, limit: int = 10_000) -> int:
    """Xếp lại mọi sự kiện chưa gửi (khởi động API/worker, CLI). `include_failed`: đưa `failed` về `pending` trước."""
    with SessionLocal() as db:
        if include_failed:
            db.execute(update(SysEvent).where(SysEvent.status == EventStatus.FAILED.value).values(status="pending"))
            db.commit()
        stmt = select(SysEvent.id).where(SysEvent.status == EventStatus.PENDING.value)
        ids = list(db.scalars(stmt.order_by(SysEvent.id).limit(limit)))
    if ids and schedule(ids):
        logger.info("Đã xếp gửi lại %d sự kiện chờ trong outbox", len(ids))
    return len(ids)


# --- Gửi (chạy trong worker) ---------------------------------------------------------------------------------------


def _deliver_one(db: Session, event_id: int, redis_conn, enabled: frozenset[str]) -> str | None:
    """Gửi một sự kiện cho các luồng nhận còn thiếu. Trả lỗi (nếu có) – đã ghi vào dòng outbox."""
    ev = db.scalar(select(SysEvent).where(SysEvent.id == event_id).with_for_update(skip_locked=True))
    if ev is None or ev.status == EventStatus.SENT.value:
        return None
    settings = get_settings()
    deliveries = {k: dict(v) for k, v in (ev.deliveries or {}).items()}
    errors, tried = [], False
    body = json.dumps(ev.payload, ensure_ascii=False, separators=(",", ":"))
    for consumer in ev.consumers:
        if deliveries.get(consumer, {}).get("status") == "sent":
            continue
        if consumer not in enabled:
            deliveries[consumer] = {"status": "pending", "reason": "consumer_disabled"}  # N6: chờ luồng bật lại
            continue
        tried = True
        try:
            stream_id = redis_conn.xadd(
                stream_key(consumer),
                {"event_id": str(ev.event_id), "event_code": ev.event_code, "payload": body},
                maxlen=settings.event_stream_maxlen,
                approximate=True,
            )
        except RedisError as exc:
            message = f"{consumer}: {type(exc).__name__}: {exc}"[:300]
            deliveries[consumer] = {"status": "pending", "error": message}
            errors.append(message)
            continue
        sid = stream_id.decode() if isinstance(stream_id, bytes) else str(stream_id)
        deliveries[consumer] = {"status": "sent", "at": to_utc_iso(now_utc()), "stream_id": sid}
    ev.deliveries = deliveries
    if tried:
        ev.attempts += 1
    if all(deliveries.get(c, {}).get("status") == "sent" for c in ev.consumers):
        ev.status = EventStatus.SENT.value
        ev.dispatched_at = now_utc()
        ev.last_error = None
    elif errors:
        ev.last_error = "; ".join(errors)[:1000]
    db.commit()
    return "; ".join(errors) or None


def dispatch_events(event_ids: list[int]) -> None:
    """Tác vụ rq: gửi các sự kiện outbox cho Luồng 2, 4. Chạy lặp an toàn (sự kiện đã `sent` bỏ qua)."""
    settings = get_settings()
    if not settings.job_queue_url:
        logger.warning("Chưa đặt JOB_QUEUE_URL – không gửi được sự kiện, giữ trong outbox")
        return
    redis_conn = jobs.redis_connection(settings.job_queue_url)
    enabled = frozenset(settings.enabled_flows)
    failed: dict[int, str] = {}
    for event_id in event_ids:
        with SessionLocal() as db:
            error = _deliver_one(db, event_id, redis_conn, enabled)
        if error:
            failed[event_id] = error
    if not failed:
        return
    attempt_no, last = jobs.attempt()
    final = last or not jobs.in_worker()
    for event_id, error in failed.items():
        logger.error(
            "Gửi sự kiện outbox %s lỗi (lần thử %d)%s: %s",
            event_id,
            attempt_no,
            " – hết lượt" if final else " – rq sẽ thử lại",
            error,
        )
    if final:
        with SessionLocal() as db:
            rows = db.scalars(select(SysEvent).where(SysEvent.id.in_(failed)).with_for_update()).all()
            for ev in rows:
                if ev.status != EventStatus.PENDING.value:
                    continue
                ev.status = EventStatus.FAILED.value
                audit.record(
                    db,
                    "outbox.dispatch_failed",
                    "event",
                    ev.id,
                    None,
                    after={
                        "event_code": ev.event_code,
                        "event_id": str(ev.event_id),
                        "attempts": ev.attempts,
                        "error": failed[ev.id][:500],
                    },
                )
            db.commit()
    if jobs.in_worker():
        raise DispatchError(f"{len(failed)} sự kiện chưa gửi được")
