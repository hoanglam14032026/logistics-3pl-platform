"""P2 - Hoc lieu 7 loai, topics, courses, progress (04 §2/§3)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import engine
from app.core.migrations import upgrade_to_head
from app.main import create_app

client = TestClient(create_app())

@pytest.fixture(autouse=True)
def _ensure_head_and_topics():
    upgrade_to_head(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lrn_topics (topic_code, name) VALUES "
                "('T01', 'Hop dong va dieu kien thuong mai'), "
                "('T02', 'Chung tu thuong mai va van tai'), "
                "('T03', 'Thu tuc hai quan'), "
                "('T04', 'Van tai va giao nhan'), "
                "('T05', 'Kho bai va phan phoi'), "
                "('T06', 'Quan ly van hanh va kiem soat'), "
                "('T07', 'Do luong hieu qua') "
                "ON CONFLICT (topic_code) DO NOTHING"
            )
        )

def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"

@pytest.mark.parametrize("prefix", ["/api/v2/learning", "/learning"])  # /learning: alias cho client cũ
def test_topics_seed(prefix):
    r = client.get(f"{prefix}/topics")
    assert r.status_code == 200
    codes = [t["topic_code"] for t in r.json()]
    for expected in ["T01", "T02", "T03", "T04", "T05", "T06", "T07"]:
        assert expected in codes, f"thieu topic {expected}, co: {codes}"

def test_create_resource_invalid_type():
    r = client.post(
        "/learning/resources",
        json={"title": _uniq("X"), "resource_type": "WRONG", "topic_code": "T01"},
    )
    assert r.status_code == 422

def test_create_resource_invalid_topic():
    r = client.post(
        "/learning/resources",
        json={"title": _uniq("X"), "resource_type": "GUIDE", "topic_code": "T99"},
    )
    assert r.status_code == 422

def test_resource_flow_search_publish_course_progress():
    uniq = uuid.uuid4().hex[:8]
    title = f"Hop dong mau FWD {uniq}"
    course_code = f"C-P2-{uniq}"
    learner_id = 900000 + int(uniq[:6], 16) % 90000
    r = client.post(
        "/learning/resources",
        json={
            "title": title,
            "summary": f"Mau hop dong van tai {uniq}",
            "resource_type": "GUIDE",
            "topic_code": "T01",
            "level": "basic",
        },
    )
    assert r.status_code == 201, r.text
    res_id = r.json()["id"]
    assert r.json()["status"] == "draft"
    r = client.get("/learning/resources", params={"q": title})
    assert r.status_code == 200
    assert all(x["id"] != res_id for x in r.json())
    r = client.post(f"/learning/resources/{res_id}/publish")
    assert r.status_code == 200
    assert r.json()["status"] == "published"
    r = client.get("/learning/resources", params={"q": title, "topic_code": "T01"})
    assert any(x["id"] == res_id for x in r.json()), r.text
    r = client.get("/learning/resources", params={"resource_type": "VIDEO", "topic_code": "T01", "q": title})
    assert all(x["id"] != res_id for x in r.json())
    r = client.post(
        "/learning/courses",
        json={"course_code": course_code, "name": f"Khoa P2 test {uniq}"},
    )
    assert r.status_code == 201, r.text
    course_id = r.json()["id"]
    r = client.post(
        f"/learning/courses/{course_id}/items",
        json={"resource_id": res_id, "seq": 1},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/learning/progress",
        json={"learner_id": learner_id, "resource_id": res_id, "course_id": course_id, "percent_complete": 50},
    )
    assert r.status_code == 200
    assert r.json()["percent_complete"] == 50
    r = client.post(
        "/learning/progress",
        json={"learner_id": learner_id, "resource_id": res_id, "course_id": course_id, "percent_complete": 100},
    )
    assert r.status_code == 200
    assert r.json()["completed_at"] is not None
    r = client.get("/learning/progress", params={"learner_id": learner_id})
    assert r.status_code == 200
    assert any(x["resource_id"] == res_id for x in r.json())
