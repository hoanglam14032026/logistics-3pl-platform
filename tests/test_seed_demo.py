"""app.scripts.seed_demo: tạo đủ tài khoản + hợp đồng mẫu, chạy lại không nhân đôi."""

import pytest
from sqlalchemy import func, select, text

from app.core.db import SessionLocal, engine
from app.flows.operations.models import OpsServiceContract
from app.scripts import seed_demo
from app.shared.identity.models import SysUser


@pytest.fixture
def env(client):
    # `client` xóa sạch dữ liệu (TRUNCATE) nên nạp lại T01..T07 sau đó; production có sẵn từ migration 0024
    values = ", ".join(f"('{c}', 'Chu de {c}')" for c in seed_demo.EXPECTED_TOPICS)
    with engine.begin() as conn:
        conn.execute(text(f"INSERT INTO lrn_topics (topic_code, name) VALUES {values} ON CONFLICT DO NOTHING"))
    return client


def _counts():
    with SessionLocal() as db:
        users = db.scalar(select(func.count()).select_from(SysUser))
        contracts = db.scalar(select(func.count()).select_from(OpsServiceContract))
    return users, contracts


def test_seed_is_idempotent(env):
    seed_demo.seed()
    assert _counts() == (4, 1)
    seed_demo.seed()
    assert _counts() == (4, 1)


def test_seed_demo_accounts_can_login(env):
    client = env
    seed_demo.seed()
    for username, _, _ in seed_demo.DEMO_USERS:
        r = client.post("/api/v2/auth/login", json={"username": username, "password": seed_demo.DEMO_PASSWORD})
        assert r.status_code == 200, (username, r.text)


def test_main_prints_accounts(env, capsys):
    assert seed_demo.main() == 0
    out = capsys.readouterr().out
    for username, _, _ in seed_demo.DEMO_USERS:
        assert username in out
    assert seed_demo.DEMO_PASSWORD in out
