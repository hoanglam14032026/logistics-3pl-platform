"""Test bộ đếm giới hạn tần suất app/core/rate_limit.py trên PostgreSQL (06 §3)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core import rate_limit
from app.core.db import SessionLocal, engine
from app.core.rate_limit import SysRateLimitCounter
from tests.conftest import truncate_all

T0 = datetime(2026, 11, 8, 10, 0, 15, tzinfo=UTC)  # giây thứ 15 của cửa sổ 60 giây


def setup_function():
    truncate_all()


def test_counts_within_window_and_resets_next_window():
    hits = [rate_limit.hit(engine, "s", "k", limit=3, window_sec=60, at=T0 + timedelta(seconds=i)) for i in range(5)]
    assert [h.count for h in hits] == [1, 2, 3, 4, 5]
    assert [h.exceeded for h in hits] == [False, False, False, True, True]
    assert [h.first_exceeded for h in hits] == [False, False, False, True, False]
    assert hits[0].retry_after == 45  # còn 45 giây tới cửa sổ kế tiếp
    nxt = rate_limit.hit(engine, "s", "k", limit=3, window_sec=60, at=T0 + timedelta(seconds=45))
    assert nxt.count == 1 and not nxt.exceeded


def test_scopes_and_keys_are_independent():
    rate_limit.hit(engine, "a", "k", 1, 60, at=T0)
    assert rate_limit.hit(engine, "a", "khac", 1, 60, at=T0).count == 1
    assert rate_limit.hit(engine, "b", "k", 1, 60, at=T0).count == 1


def test_concurrent_hits_are_counted_exactly():
    with ThreadPoolExecutor(max_workers=10) as pool:
        counts = list(pool.map(lambda _: rate_limit.hit(engine, "s", "k", 100, 60, at=T0).count, range(40)))
    assert sorted(counts) == list(range(1, 41))  # không mất lượt, không trùng số


def test_old_windows_are_cleaned_up():
    rate_limit.hit(engine, "s", "cu", 5, 60, at=T0)
    rate_limit.hit(engine, "s", "moi", 5, 60, at=T0 + timedelta(hours=2))
    with SessionLocal() as db:
        assert db.scalars(select(SysRateLimitCounter.key)).all() == ["moi"]
        assert db.scalar(select(func.count()).select_from(SysRateLimitCounter)) == 1


def test_long_key_truncated():
    assert rate_limit.hit(engine, "s", "x" * 500, 5, 60, at=T0).count == 1
