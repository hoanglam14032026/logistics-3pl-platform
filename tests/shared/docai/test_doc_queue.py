"""Job trích xuất chứng từ qua hàng đợi rq + Redis thật (Giai đoạn 9; như email Giai đoạn 7).

Cần Redis: docker compose up -d redis (TEST_JOB_QUEUE_URL, mặc định redis://localhost:6379/15 – bộ test xóa sạch CSDL
Redis này). Worker trong test: `SimpleWorker` (không fork).
"""

import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from rq.registry import FailedJobRegistry

from app.core import jobs
from app.core.config import Settings, get_settings
from tests.shared.docai.conftest import JOBS, PARTY_ANSWER, PARTY_TEXT, logs, upload


@pytest.fixture()
def queue(client, monkeypatch) -> Queue:
    url = Settings().test_job_queue_url
    conn = Redis.from_url(url)
    if conn.connection_pool.connection_kwargs.get("db", 0) == 0:
        pytest.fail("TEST_JOB_QUEUE_URL không được dùng CSDL Redis số 0")
    try:
        conn.ping()
    except Exception as exc:
        pytest.fail(f"Không kết nối được Redis cho test ({url}): docker compose up -d redis – {exc}")
    conn.flushdb()
    monkeypatch.setenv("JOB_QUEUE_URL", url)
    monkeypatch.setenv("JOB_MAX_RETRIES", "2")
    monkeypatch.setenv("JOB_RETRY_INTERVALS", "0")
    get_settings.cache_clear()
    yield Queue(jobs.QUEUE_NAME, connection=conn)
    conn.flushdb()


@pytest.fixture()
def model(fake_ollama, make_model):
    return make_model()


def _work(queue: Queue) -> None:
    SimpleWorker([queue], connection=queue.connection).work(burst=True)


def _get(client, users, job_id) -> dict:
    return client.get(f"{JOBS}/{job_id}", headers=users["h"]["ops"]).json()


def test_job_in_redis_carries_only_ids_then_worker_extracts(client, queue, users, model, fake_ollama):
    r = upload(client, users["h"]["ops"], model.id)
    assert r.status_code == 202
    job_id = r.json()["id"]
    assert _get(client, users, job_id)["status"] == "queued" and _get(client, users, job_id)["queued_via"] == "queue"
    assert fake_ollama.chats() == []  # API không gọi model trong request
    [rq_job] = queue.jobs
    assert rq_job.func_name == "app.shared.docai.tasks.process_doc_job"
    assert rq_job.args == (job_id, users["user"]["ops"].id) and rq_job.timeout == 1800
    # Redis không chứa nội dung chứng từ, kết quả AI, token
    raw = b"".join(v for k in queue.connection.keys("rq:job:*") for v in queue.connection.hgetall(k).values())
    for secret in (b"0101234567", b"MINH AN", b"Bearer", b"eyJ"):
        assert secret not in raw

    _work(queue)
    job = _get(client, users, job_id)
    assert job["status"] == "needs_review" and job["attempts"] == 1
    assert len(fake_ollama.chats()) == 1


def test_retry_until_model_server_answers(client, queue, users, model, fake_ollama):
    calls = []

    def flaky(body):
        calls.append(1)
        if len(calls) < 3:
            return 503, {"error": "model đang nạp"}
        import json

        return 200, {"message": {"content": json.dumps(PARTY_ANSWER, ensure_ascii=False)}}

    fake_ollama.reply = flaky
    job_id = upload(client, users["h"]["ops"], model.id).json()["id"]
    _work(queue)
    job = _get(client, users, job_id)
    assert len(calls) == 3 and job["status"] == "needs_review" and job["attempts"] == 3
    assert logs("doc.failed") == []  # chưa hết lượt thì không ghi lỗi


def test_final_failure_marks_failed_and_audits_once(client, queue, users, model, fake_ollama):
    fake_ollama.reply = lambda body: (503, {"error": "hết bộ nhớ GPU"})
    job_id = upload(client, users["h"]["ops"], model.id, content=PARTY_TEXT.encode()).json()["id"]
    _work(queue)
    job = _get(client, users, job_id)
    assert job["status"] == "failed" and job["attempts"] == 3 and "503" in job["last_error"]
    [failed] = logs("doc.failed", job_id)
    assert failed.after["attempts"] == 3 and failed.after["in_worker"] is True
    assert failed.request_id and failed.ip == "testclient"  # request tải lên
    assert FailedJobRegistry(queue=queue).count == 1


def test_model_disabled_while_queued_fails_job(client, queue, users, model, fake_ollama):
    job_id = upload(client, users["h"]["ops"], model.id).json()["id"]
    assert client.post(f"/api/v2/admin/ai-models/{model.id}:deactivate", headers=users["h"]["admin"]).status_code == 200
    _work(queue)
    job = _get(client, users, job_id)
    assert job["status"] == "failed" and "đã tắt" in job["last_error"] and job["ai_model_id"] == model.id
    assert fake_ollama.chats() == []  # không gửi dữ liệu cho model đã bị admin tắt


def test_duplicate_delivery_is_ignored(client, queue, users, model, fake_ollama):
    from app.shared.docai import tasks

    job_id = upload(client, users["h"]["ops"], model.id).json()["id"]
    _work(queue)
    queue.enqueue(tasks.process_doc_job, job_id, users["user"]["ops"].id)  # rq "ít nhất một lần"
    queue.enqueue(tasks.process_doc_job, job_id, users["user"]["ops2"].id)  # sai người tạo
    _work(queue)
    assert len(fake_ollama.chats()) == 1 and _get(client, users, job_id)["attempts"] == 1


def test_redis_down_falls_back_to_background(client, users, model, monkeypatch, caplog):
    monkeypatch.setenv("JOB_QUEUE_URL", "redis://127.0.0.1:1/0")  # không có Redis
    get_settings.cache_clear()
    r = upload(client, users["h"]["ops"], model.id)
    assert r.status_code == 202
    job = _get(client, users, r.json()["id"])
    assert job["queued_via"] == "background" and job["status"] == "needs_review"
    assert "Không xếp được process_doc_job vào hàng đợi" in caplog.text
