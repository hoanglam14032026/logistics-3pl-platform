"""P2 - Hoc lieu 7 loai, topics T01..T07, courses, progress, search (04 §2/§3)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.flows.learning.models import (
    LrnCourse,
    LrnCourseItem,
    LrnProgress,
    LrnResource,
    LrnTopic,
)

# Không đặt prefix ở đây: app/main.py gắn router này hai lần – /api/v2/learning (chuẩn) và /learning (alias cũ).
router = APIRouter(tags=["learning-resources"])

TOPICS_SEED = [
    ("T01", "Hop dong va dieu kien thuong mai"),
    ("T02", "Chung tu thuong mai va van tai"),
    ("T03", "Thu tuc hai quan"),
    ("T04", "Van tai va giao nhan"),
    ("T05", "Kho bai va phan phoi"),
    ("T06", "Quan ly van hanh va kiem soat"),
    ("T07", "Do luong hieu qua"),
]

def _ensure_topics(db: Session):
    """Seed T01..T07 neu DB trong (test full reset DB)."""
    if db.scalar(select(LrnTopic).limit(1)) is None:
        for code, name in TOPICS_SEED:
            db.add(LrnTopic(topic_code=code, name=name))
        db.commit()

class TopicOut(BaseModel):
    topic_code: str
    name: str
    description: str | None = None

    model_config = {"from_attributes": True}

class ResourceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    resource_type: str
    topic_code: str | None = None
    level: str | None = None
    detail: dict[str, Any] | None = None

class ResourceOut(BaseModel):
    id: int
    title: str
    summary: str | None
    resource_type: str
    topic_code: str | None
    level: str | None
    status: str
    detail: dict[str, Any] | None

    model_config = {"from_attributes": True}

class CourseCreate(BaseModel):
    course_code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    owner_instructor_id: int | None = None

class CourseOut(BaseModel):
    id: int
    course_code: str
    name: str
    description: str | None
    status: str

    model_config = {"from_attributes": True}

class CourseItemCreate(BaseModel):
    resource_id: int
    seq: int = Field(ge=1)
    is_required: bool = True
    min_completion: int = Field(default=100, ge=0, le=100)

class ProgressUpdate(BaseModel):
    learner_id: int
    resource_id: int
    course_id: int | None = None
    percent_complete: int = Field(ge=0, le=100)
    last_position_sec: int | None = None

class ProgressOut(BaseModel):
    id: int
    learner_id: int
    resource_id: int
    course_id: int | None
    percent_complete: int
    last_position_sec: int | None
    completed_at: Any | None = None

    model_config = {"from_attributes": True}

VALID_TYPES = {"GUIDE", "ACADEMIC_DOC", "VIDEO", "INTERVIEW", "CASE_STUDY", "TEMPLATE", "REGULATION"}

@router.get("/topics", response_model=list[TopicOut])
def list_topics(db: Session = Depends(get_db)):
    _ensure_topics(db)
    return list(db.scalars(select(LrnTopic).order_by(LrnTopic.topic_code)).all())

@router.get("/resources", response_model=list[ResourceOut])
def search_resources(
    q: str | None = Query(default=None),
    resource_type: str | None = None,
    topic_code: str | None = None,
    level: str | None = None,
    status: str | None = Query(default="published"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(LrnResource)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(LrnResource.title.ilike(like), LrnResource.summary.ilike(like)))
    if resource_type:
        if resource_type not in VALID_TYPES:
            raise HTTPException(status_code=422, detail="resource_type khong hop le")
        stmt = stmt.where(LrnResource.resource_type == resource_type)
    if topic_code:
        stmt = stmt.where(LrnResource.topic_code == topic_code)
    if level:
        stmt = stmt.where(LrnResource.level == level)
    if status:
        stmt = stmt.where(LrnResource.status == status)
    return list(db.scalars(stmt.order_by(LrnResource.id.desc()).limit(limit)).all())

@router.post("/resources", response_model=ResourceOut, status_code=201)
def create_resource(payload: ResourceCreate, db: Session = Depends(get_db)):
    if payload.resource_type not in VALID_TYPES:
        raise HTTPException(status_code=422, detail="resource_type khong hop le")
    _ensure_topics(db)
    if payload.topic_code:
        topic = db.scalar(select(LrnTopic).where(LrnTopic.topic_code == payload.topic_code))
        if not topic:
            raise HTTPException(status_code=422, detail="topic_code khong ton tai (T01..T07)")
    obj = LrnResource(**payload.model_dump(), status="draft")
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj

@router.post("/resources/{resource_id}/publish", response_model=ResourceOut)
def publish_resource(resource_id: int, db: Session = Depends(get_db)):
    obj = db.get(LrnResource, resource_id)
    if not obj:
        raise HTTPException(status_code=404, detail="resource khong ton tai")
    obj.status = "published"
    db.commit()
    db.refresh(obj)
    return obj

@router.post("/courses", response_model=CourseOut, status_code=201)
def create_course(payload: CourseCreate, db: Session = Depends(get_db)):
    if db.scalar(select(LrnCourse).where(LrnCourse.course_code == payload.course_code)):
        raise HTTPException(status_code=422, detail="course_code da ton tai")
    obj = LrnCourse(**payload.model_dump(), status="draft")
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj

@router.post("/courses/{course_id}/items", status_code=201)
def add_course_item(course_id: int, payload: CourseItemCreate, db: Session = Depends(get_db)):
    if not db.get(LrnCourse, course_id):
        raise HTTPException(status_code=404, detail="course khong ton tai")
    if not db.get(LrnResource, payload.resource_id):
        raise HTTPException(status_code=404, detail="resource khong ton tai")
    obj = LrnCourseItem(course_id=course_id, **payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return {"id": obj.id, "course_id": course_id, "resource_id": obj.resource_id, "seq": obj.seq}

@router.post("/progress", response_model=ProgressOut)
def update_progress(payload: ProgressUpdate, db: Session = Depends(get_db)):
    obj = db.scalar(
        select(LrnProgress).where(
            LrnProgress.learner_id == payload.learner_id,
            LrnProgress.resource_id == payload.resource_id,
        )
    )
    if not obj:
        obj = LrnProgress(**payload.model_dump())
        db.add(obj)
    else:
        obj.percent_complete = payload.percent_complete
        obj.last_position_sec = payload.last_position_sec
        if payload.course_id:
            obj.course_id = payload.course_id
    if payload.percent_complete >= 100:
        from app.core.timeutil import now_utc

        obj.completed_at = now_utc()
    db.commit()
    db.refresh(obj)
    return obj

@router.get("/progress", response_model=list[ProgressOut])
def list_progress(learner_id: int, db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(LrnProgress).where(LrnProgress.learner_id == learner_id).order_by(LrnProgress.id.desc())
        ).all()
    )
