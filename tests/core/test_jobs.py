"""Hàng đợi bền vững rq + Redis cho thư mời / đặt lại mật khẩu (Giai đoạn 7), trên PostgreSQL + Redis thật.

Cần Redis: docker compose up -d redis (TEST_JOB_QUEUE_URL, mặc định redis://localhost:6379/15 – bộ test xóa sạch CSDL
Redis này). Worker trong test: `SimpleWorker` (không fork) để đọc được hộp thư MAIL_BACKEND=memory.
"""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from rq.registry import FailedJobRegistry, StartedJobRegistry
from sqlalchemy import select

from app.core import jobs
from app.core.config import Settings, get_settings
from app.core.db import SessionLocal
from app.shared.audit.models import SysAuditLog
from app.shared.identity.models import SysUserToken
from app.shared.notifications import mail
from tests.conftest import link_token

ROOT = Path(__file__).resolve().parents[2]
NEW = "MatKhauMoi@2026"


@pytest.fixture()
def queue(client, monkeypatch) -> Queue:
    url = Settings().test_job_queue_url
    conn = Redis.from_url(url)
    if conn.connection_pool.connection_kwargs.get("db", 0) == 0:
        pytest.fail("TEST_JOB_QUEUE_URL không được dùng CSDL Redis số 0 (bộ test xóa sạch CSDL này)")
    try:
        conn.ping()
    except Exception as exc:
        pytest.fail(f"Không kết nối được Redis cho test ({url}): docker compose up -d redis – {exc}")
    conn.flushdb()
    monkeypatch.setenv("JOB_QUEUE_URL", url)
    monkeypatch.setenv("JOB_MAX_RETRIES", "2")
    monkeypatch.setenv("JOB_RETRY_INTERVALS", "0")  # thử lại ngay – không cần bộ lập lịch trong test
    get_settings.cache_clear()
    yield Queue(jobs.QUEUE_NAME, connection=conn)
    conn.flushdb()


def _work(queue: Queue) -> None:
    SimpleWorker([queue], connection=queue.connection).work(burst=True)


def _actions(action: str) -> list[SysAuditLog]:
    with SessionLocal() as db:
        return list(db.scalars(select(SysAuditLog).where(SysAuditLog.action == action).order_by(SysAuditLog.id)))


def _tokens(purpose: str) -> list[SysUserToken]:
    with SessionLocal() as db:
        return list(db.scalars(select(SysUserToken).where(SysUserToken.purpose == purpose).order_by(SysUserToken.id)))


@pytest.fixture()
def staff(make_user):
    return make_user("nhan.vien", email="nv@cty.vn")


def test_mail_job_is_stored_in_redis_until_a_worker_runs_it(client, queue, staff):
    """API chỉ xếp tác vụ vào Redis (không gửi, không cấp token); tiến trình API dừng – tác vụ vẫn còn; worker khác
    chạy sau vẫn gửi được."""
    assert client.post("/api/v2/auth/password/forgot", json={"identifier": "nhan.vien"}).status_code == 202
    assert mail.OUTBOX == [] and _tokens("password_reset") == []
    [job] = queue.jobs
    assert job.func_name == "app.shared.identity.email_flows.deliver_password_reset" and job.args == (staff.id,)
    assert job.retries_left == 2
    # Redis không chứa token/đường dẫn
    raw = b"".join(v for k in queue.connection.keys("rq:job:*") for v in queue.connection.hgetall(k).values())
    assert b"#token=" not in raw and b"reset-password" not in raw

    client.close()  # "tắt" tiến trình API; kết nối Redis mới như ở một worker khác
    _work(Queue(jobs.QUEUE_NAME, connection=Redis.from_url(Settings().test_job_queue_url)))
    assert link_token("nv@cty.vn", "reset-password")
    assert len(_tokens("password_reset")) == 1


def test_retry_until_sent(client, queue, staff):
    calls = []

    def flaky(m, settings=None):
        calls.append(m)
        if len(calls) < 3:
            raise mail.MailError("Gửi thư thất bại: SMTPServerDisconnected")
        mail.OUTBOX.append(m)

    from unittest import mock

    with mock.patch.object(mail, "send", flaky):
        client.post("/api/v2/auth/password/forgot", json={"identifier": "nhan.vien"})
        _work(queue)
    assert len(calls) == 3 and len(mail.OUTBOX) == 1  # 1 lần + 2 lần thử lại
    assert _actions("user.mail_failed") == []  # chưa hết lượt thử thì không ghi lỗi
    # Mỗi lần thử cấp token mới; chỉ token trong thư gửi được còn hiệu lực
    tokens = _tokens("password_reset")
    assert len(tokens) == 3 and [t.revoked_at is None for t in tokens] == [False, False, True]
    token = link_token("nv@cty.vn", "reset-password")
    assert client.post("/api/v2/auth/password/reset", json={"token": token, "new_password": NEW}).status_code == 204


def test_final_failure_is_audited_once_and_kept_in_failed_registry(client, queue, staff, caplog):
    def broken(m, settings=None):
        raise mail.MailError("Gửi thư thất bại: SMTPAuthenticationError")

    from unittest import mock

    with mock.patch.object(mail, "send", broken):
        assert client.post("/api/v2/auth/password/forgot", json={"identifier": "nhan.vien"}).status_code == 202
        _work(queue)
    [log] = _actions("user.mail_failed")
    assert log.after == {
        "purpose": "password_reset",
        "error": "Gửi thư thất bại: SMTPAuthenticationError",
        "attempts": 3,
    }
    assert FailedJobRegistry(queue=queue).count == 1  # quản trị xem / chạy lại được
    assert "lần thử 1" in caplog.text and "lần thử 3" in caplog.text


def test_invitation_through_queue_and_skip_when_no_longer_needed(client, queue, make_party, make_contract, make_user):
    party = make_party("Khách A", tax_code="0100000001")
    make_contract(party.id)
    make_user("quan.ly", roles=["manager"])
    from tests.conftest import DEFAULT_PASSWORD

    r = client.post("/api/v2/auth/login", json={"username": "quan.ly", "password": DEFAULT_PASSWORD})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    user = client.post(
        f"/api/v2/md/parties/{party.id}/users",
        json={"username": "kh.lan", "full_name": "Lan", "email": "lan@a.vn"},
        headers=headers,
    ).json()
    resend = f"/api/v2/md/parties/{party.id}/users/{user['id']}:resend-invite"
    assert client.post(resend, headers=headers).status_code == 202
    assert queue.count == 2 and mail.OUTBOX == []
    _work(queue)
    assert [m.to for m in mail.OUTBOX] == ["lan@a.vn", "lan@a.vn"]  # chạy lặp vẫn đúng: thư sau thay thư trước
    token = link_token("lan@a.vn", "accept-invite")
    assert client.post("/api/v2/auth/invitations/accept", json={"token": token, "new_password": NEW}).status_code == 204
    # Đã nhận lời mời trước khi tác vụ chờ được chạy → bỏ qua, không cấp token mới
    from app.shared.identity import email_flows

    queue.enqueue(email_flows.deliver_invitation, user["id"], None)
    mail.OUTBOX.clear()
    _work(queue)
    assert mail.OUTBOX == [] and len(_tokens("invite")) == 2


def test_redis_down_falls_back_to_background(client, staff, monkeypatch, caplog):
    monkeypatch.setenv("JOB_QUEUE_URL", "redis://127.0.0.1:1/0")  # không có Redis
    get_settings.cache_clear()
    assert client.post("/api/v2/auth/password/forgot", json={"identifier": "nhan.vien"}).status_code == 202
    assert [m.to for m in mail.OUTBOX] == ["nv@cty.vn"]
    assert "Không xếp được deliver_password_reset vào hàng đợi" in caplog.text


def test_worker_requires_queue_url(monkeypatch):
    from app import worker

    monkeypatch.delenv("JOB_QUEUE_URL", raising=False)
    monkeypatch.setenv("JOB_QUEUE_URL", "")
    get_settings.cache_clear()
    assert worker.main() == 2


# --- Worker chết giữa chừng ----------------------------------------------------------------------------------------


def _hanging_smtp() -> int:
    """Máy chủ nhận kết nối nhưng không bao giờ trả lời – giữ tác vụ gửi thư ở trạng thái đang chạy."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    held = []

    def serve():
        while True:
            try:
                held.append(server.accept()[0])
            except OSError:
                return

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1]


def test_job_survives_worker_crash(client, queue, staff):
    """Worker thật (`python -m app.worker`) bị kill -9 khi đang gửi: tác vụ không mất – rq phát hiện tác vụ bị bỏ dở
    và đưa lại vào hàng đợi; worker khác gửi được."""
    port = _hanging_smtp()
    client.post("/api/v2/auth/password/forgot", json={"identifier": "nhan.vien"})
    env = {
        **os.environ,
        "JOB_QUEUE_URL": Settings().test_job_queue_url,
        "JOB_MAX_RETRIES": "2",
        "JOB_RETRY_INTERVALS": "0",
        "MAIL_BACKEND": "smtp",
        "SMTP_HOST": "127.0.0.1",
        "SMTP_PORT": str(port),
        "SMTP_SECURITY": "none",
        "SMTP_FROM": "no-reply@test.vn",
        "SMTP_TIMEOUT_SEC": "120",
        "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES",  # macOS: fork sau khi nạp thư viện hệ thống
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    started = StartedJobRegistry(queue=queue)
    try:
        deadline = time.monotonic() + 30
        while started.count == 0 and time.monotonic() < deadline:
            time.sleep(0.2)
        assert started.count == 1, "worker không nhận tác vụ"
        time.sleep(0.5)  # tác vụ đã cấp token, đang chờ SMTP
    finally:
        os.killpg(proc.pid, signal.SIGKILL)  # giết cả worker và tiến trình con đang chạy tác vụ
        proc.wait(timeout=10)
    assert mail.OUTBOX == [] and queue.count == 0
    # Worker còn sống (hoặc lần khởi động sau) dọn tác vụ hết hạn; ép hết hạn ngay thay vì chờ JOB_TIMEOUT_SEC + 60 giây
    started.cleanup(timestamp=time.time() + 100_000)
    assert queue.count == 1
    _work(queue)
    assert [m.to for m in mail.OUTBOX] == ["nv@cty.vn"]
    tokens = _tokens("password_reset")
    assert len(tokens) == 2 and tokens[0].revoked_at is not None  # token của lần bị bỏ dở đã vô hiệu
