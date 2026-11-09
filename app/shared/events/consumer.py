"""
Nhận sự kiện từ outbox cho một luồng (Giai đoạn 11) – dùng chung: Luồng 1 (`ops`), Luồng 2 (`control`), sau này Luồng 4.

- Luồng đăng ký xử lý: `@handler("control", "EV-101")` – hàm `(db, envelope) -> dict | None` chạy **trong giao dịch**
  của bộ nhận (không tự commit). Kết quả trả về lưu ở `sys_event_consumptions.result`.
- `process(consumer, envelope)`: khóa/ghi dòng `sys_event_consumptions` (UNIQUE luồng + `event_id`), chạy xử lý, commit
  **cùng một giao dịch** → sự kiện `processed` không bao giờ xử lý lại (N5); hai bộ nhận chạy song song cùng sự kiện:
  một bên chờ khóa dòng rồi thấy đã xử lý. Lỗi → rollback, ghi `failed` + `attempts` ở giao dịch riêng; đủ
  `JOB_MAX_RETRIES + 1` lần → `dead`, log + nhật ký `event.consume_failed` (không chặn sự kiện sau).
- `consume(consumer)` – tác vụ rq (hàng `events`, xếp bởi bộ phát outbox sau khi XADD): đọc Redis Stream
  `<EVENT_STREAM_PREFIX><luồng>` bằng consumer group cùng tên luồng (tin chưa ACK trước, rồi tin mới), xử lý, XACK. Còn
  tin lỗi → ném lỗi để rq thử lại như Giai đoạn 7.
- `replay(consumer)`: xử lý lại thẳng từ `sys_events` (không cần Redis) – CLI `python -m app.shared.events.cli replay`.
"""

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from redis.exceptions import ResponseError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import jobs
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.timeutil import now_utc
from app.shared.audit import service as audit
from app.shared.events.models import ConsumptionStatus, SysEvent, SysEventConsumption

logger = logging.getLogger("app.events.consumer")

Handler = Callable[[Session, dict[str, Any]], dict[str, Any] | None]
HANDLERS: dict[str, dict[str, Handler]] = {}
WORKER_NAME = "app"
DONE = {ConsumptionStatus.PROCESSED.value, ConsumptionStatus.SKIPPED.value, ConsumptionStatus.DEAD.value}


class ConsumeError(Exception):
    """Còn sự kiện xử lý lỗi – rq thử lại tác vụ nhận."""


def handler(consumer: str, event_code: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS.setdefault(consumer, {})[event_code] = fn
        return fn

    return register


def has_handlers(consumer: str) -> bool:
    return bool(HANDLERS.get(consumer))


def max_attempts() -> int:
    return get_settings().job_max_retries + 1


def _record_failure(consumer: str, envelope: dict, error: str) -> str:
    with SessionLocal() as db:
        row = db.execute(
            insert(SysEventConsumption)
            .values(
                consumer=consumer,
                event_id=uuid.UUID(envelope["event_id"]),
                event_code=envelope["event_code"],
                status=ConsumptionStatus.FAILED.value,
                attempts=1,
                last_error=error[:1000],
            )
            .on_conflict_do_update(
                constraint="uq_sys_event_consumptions_consumer_event",
                set_={"attempts": SysEventConsumption.attempts + 1, "last_error": error[:1000]},
            )
            .returning(SysEventConsumption.id, SysEventConsumption.attempts)
        ).one()
        status = ConsumptionStatus.FAILED.value
        if row.attempts >= max_attempts():
            status = ConsumptionStatus.DEAD.value
            db.execute(update(SysEventConsumption).where(SysEventConsumption.id == row.id).values(status=status))
            audit.record(
                db,
                "event.consume_failed",
                "event_consumption",
                row.id,
                None,
                after={
                    "consumer": consumer,
                    "event_code": envelope["event_code"],
                    "event_id": envelope["event_id"],
                    "attempts": row.attempts,
                    "error": error[:500],
                },
            )
        db.commit()
    log = logger.error if status == ConsumptionStatus.DEAD.value else logger.warning
    log(
        "Luồng %s xử lý sự kiện %s (%s) lỗi lần %d%s: %s",
        consumer,
        envelope["event_code"],
        envelope["event_id"],
        row.attempts,
        " – hết lượt" if status == ConsumptionStatus.DEAD.value else "",
        error,
    )
    return status


def process(consumer: str, envelope: dict[str, Any]) -> str:
    """Xử lý một sự kiện cho `consumer`. Trả: processed | skipped | duplicate | failed | dead."""
    event_id = uuid.UUID(envelope["event_id"])
    code = envelope["event_code"]
    with SessionLocal() as db:
        row = db.execute(
            insert(SysEventConsumption)
            .values(consumer=consumer, event_id=event_id, event_code=code, status=ConsumptionStatus.FAILED.value)
            .on_conflict_do_update(  # khóa dòng (kể cả dòng đã có) tới khi commit
                constraint="uq_sys_event_consumptions_consumer_event",
                set_={"event_code": code},
            )
            .returning(SysEventConsumption.id, SysEventConsumption.status, SysEventConsumption.attempts)
        ).one()
        if row.status in DONE:  # đã xử lý (hoặc hết lượt) – không xử lý lại
            db.rollback()
            return "duplicate"
        fn = HANDLERS.get(consumer, {}).get(code)
        try:
            result = fn(db, envelope) if fn else {"reason": "no_handler"}
            status = ConsumptionStatus.PROCESSED.value if fn else ConsumptionStatus.SKIPPED.value
            db.execute(
                update(SysEventConsumption)
                .where(SysEventConsumption.id == row.id)
                .values(status=status, result=result, last_error=None, processed_at=now_utc())
            )
            db.commit()
            return status
        except Exception as exc:  # lỗi của luồng nhận không làm hỏng bộ nhận (N6)
            db.rollback()
            return _record_failure(consumer, envelope, f"{type(exc).__name__}: {exc}")


def _ensure_group(conn, stream: str, group: str) -> None:
    try:
        conn.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def consume(consumer: str, count: int = 100) -> dict[str, int]:
    """Tác vụ rq: đọc stream của luồng, xử lý, XACK. Tin chưa ACK (lần trước lỗi) đọc lại trước."""
    settings = get_settings()
    if not settings.job_queue_url:
        return {}
    conn = jobs.redis_connection(settings.job_queue_url)
    stream = f"{settings.event_stream_prefix}{consumer}"
    _ensure_group(conn, stream, consumer)
    stats: dict[str, int] = {}
    failed = 0

    def handle(messages) -> None:
        nonlocal failed
        for msg_id, fields in messages:
            if not fields or b"payload" not in fields:  # tin đã bị cắt khỏi stream
                conn.xack(stream, consumer, msg_id)
                continue
            outcome = process(consumer, json.loads(fields[b"payload"]))
            stats[outcome] = stats.get(outcome, 0) + 1
            if outcome == "failed":
                failed += 1
            else:
                conn.xack(stream, consumer, msg_id)

    last = "0"  # tin đã giao nhưng chưa ACK
    while True:
        resp = conn.xreadgroup(consumer, WORKER_NAME, {stream: last}, count=count)
        messages = resp[0][1] if resp else []
        if not messages:
            break
        handle(messages)
        last = messages[-1][0]
    while True:  # tin mới
        resp = conn.xreadgroup(consumer, WORKER_NAME, {stream: ">"}, count=count)
        messages = resp[0][1] if resp else []
        if not messages:
            break
        handle(messages)
    if failed and jobs.in_worker():
        raise ConsumeError(f"Luồng {consumer}: {failed} sự kiện xử lý lỗi – thử lại")
    return stats


def replay(consumer: str, include_dead: bool = False, limit: int = 100_000) -> dict[str, int]:
    """Xử lý từ `sys_events` (sự kiện có `consumer` trong danh sách nhận) – không cần Redis; idempotent."""
    with SessionLocal() as db:
        if include_dead:
            db.execute(
                update(SysEventConsumption)
                .where(SysEventConsumption.consumer == consumer, SysEventConsumption.status == "dead")
                .values(status=ConsumptionStatus.FAILED.value, attempts=0)
            )
            db.commit()
        rows = db.execute(
            select(SysEvent.payload).where(SysEvent.consumers.contains([consumer])).order_by(SysEvent.id).limit(limit)
        ).all()
    stats: dict[str, int] = {}
    for (payload,) in rows:
        outcome = process(consumer, payload)
        stats[outcome] = stats.get(outcome, 0) + 1
    return stats
