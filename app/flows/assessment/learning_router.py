"""
Luồng 4 – đánh giá người học theo buổi học (Giai đoạn 18; 05 §1 – chấm điểm thuộc Luồng 4, không thuộc Luồng 3).

- `GET|PUT /api/v2/assessment/sessions/{session_id}/scores` – người hướng dẫn của hợp đồng đào tạo chấm (thang 0–10,
  nhận xét; buổi thực hành: số sai lệch cài sẵn / phát hiện đúng / báo sai → KPI-11, 12). Buổi phải đã bắt đầu, chưa
  hủy; người học phải được ghi danh; hợp đồng chưa hủy. Đọc: người hướng dẫn đó, `manager`, `business_manager`,
  `admin`. Nhật ký `session_score.grade` (trước / sau). Dữ liệu hợp đồng, buổi, ghi danh lấy từ sự kiện Luồng 3 –
  chưa nhận → 404.
- `GET /api/v2/portal/learning/scores` – người học xem điểm, nhận xét của mình.
- `GET /api/v2/assessment/kpi/learners`, `/kpi/learners/{learner_id}` – KPI-11/12/13 + điểm trung bình theo kỳ
  (`instructor`: người học của hợp đồng mình dạy).
"""

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.core.rbac import Role, forbidden, require_roles
from app.core.timeutil import UtcDatetime, now_utc
from app.flows.assessment import formulas, kpi
from app.flows.assessment.models import (
    AsmFactEnrollment,
    AsmFactTrainingContract,
    AsmFactTrainingSession,
    AsmSessionScore,
)
from app.flows.assessment.router import _period
from app.shared.audit import service as audit
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/assessment", tags=["Assessment – Learner scores"])
portal_router = APIRouter(prefix="/api/v2/portal/learning", tags=["Portal – Learner scores"])

_reader = require_roles(Role.INSTRUCTOR, Role.MANAGER, Role.BUSINESS_MANAGER, Role.ADMIN)
_instructor = require_roles(Role.INSTRUCTOR)
_learner = require_roles(Role.LEARNER)
VIEW_ALL = {Role.MANAGER.value, Role.BUSINESS_MANAGER.value, Role.ADMIN.value}
FIELDS = ("score", "comment", "seeded_discrepancies", "detected_correct", "false_reports")


class ScoreItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learner_id: int = Field(gt=0)
    score: Decimal = Field(ge=0, le=10, max_digits=4, decimal_places=2)
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None
    seeded_discrepancies: int | None = Field(None, gt=0, le=1000, description="Buổi thực hành: số sai lệch cài sẵn")
    detected_correct: int | None = Field(None, ge=0, le=1000)
    false_reports: int | None = Field(None, ge=0, le=1000)

    @model_validator(mode="after")
    def _discrepancies(self):
        self.score = self.score.quantize(Decimal("0.01"))  # thang 0–10, 2 chữ số thập phân như cột NUMERIC(4,2)
        given = [self.seeded_discrepancies, self.detected_correct, self.false_reports]
        if any(v is not None for v in given) and any(v is None for v in given):
            raise ValueError("Ghi đủ cả 3: seeded_discrepancies, detected_correct, false_reports (hoặc bỏ cả 3)")
        if self.seeded_discrepancies is not None and self.detected_correct > self.seeded_discrepancies:
            raise ValueError("detected_correct không vượt seeded_discrepancies")
        return self


class ScoresIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ScoreItem] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _unique(self):
        ids = [i.learner_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Mỗi người học một dòng")
        return self


class ScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: int
    contract_id: int
    learner_id: int
    score: Decimal
    comment: str | None
    seeded_discrepancies: int | None
    detected_correct: int | None
    false_reports: int | None
    graded_by: int
    graded_at: UtcDatetime


class MyScoreOut(BaseModel):
    session_id: int
    contract_id: int
    contract_no: str | None
    session_title: str | None
    starts_at: UtcDatetime | None
    score: Decimal
    comment: str | None
    graded_at: UtcDatetime


class LearnerKpiOut(BaseModel):
    learner_id: int
    username: str | None
    full_name: str | None
    sessions_scored: int
    avg_score: float | None
    kpis: dict[str, Any]


class LearnerKpiList(BaseModel):
    period: dict[str, Any]
    mode: str
    items: list[LearnerKpiOut]
    total: int


def _not_found(what: str) -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, f"Không tìm thấy {what} (hoặc Luồng 4 chưa nhận sự kiện)")


def _session_and_contract(db: Session, session_id: int, user: SysUser):
    s = db.get(AsmFactTrainingSession, session_id)
    if s is None:
        raise _not_found("buổi học")
    c = db.get(AsmFactTrainingContract, s.contract_id)
    if c is None:
        raise _not_found("hợp đồng đào tạo")
    if not (VIEW_ALL & set(user.role_codes)) and c.instructor_id != user.id:
        raise _not_found("buổi học")
    return s, c


def _scores(db: Session, session_id: int) -> list[AsmSessionScore]:
    return list(
        db.scalars(
            select(AsmSessionScore).where(AsmSessionScore.session_id == session_id).order_by(AsmSessionScore.learner_id)
        )
    )


@router.get("/sessions/{session_id:int}/scores", response_model=list[ScoreOut], summary="Điểm của buổi học")
def get_scores(session_id: int, user: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    _session_and_contract(db, session_id, user)
    return _scores(db, session_id)


def _snap(row: AsmSessionScore | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {f: (str(getattr(row, f)) if f == "score" else getattr(row, f)) for f in FIELDS}


@router.put(
    "/sessions/{session_id:int}/scores",
    response_model=list[ScoreOut],
    summary="Chấm điểm người học theo buổi (người hướng dẫn của hợp đồng)",
)
def put_scores(
    session_id: int,
    body: ScoresIn,
    actor: SysUser = Depends(_instructor),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    s, c = _session_and_contract(db, session_id, actor)
    if c.instructor_id != actor.id:
        raise forbidden("Chỉ người hướng dẫn của hợp đồng chấm điểm")
    if c.status == "cancelled":
        raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, "Hợp đồng đào tạo đã hủy")
    if s.status != "scheduled":
        raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, "Buổi học đã hủy")
    if s.starts_at > now_utc():
        raise AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, "Buổi học chưa bắt đầu – chưa chấm được")
    enrolled = set(
        db.scalars(select(AsmFactEnrollment.learner_id).where(AsmFactEnrollment.contract_id == c.contract_id))
    )
    unknown = sorted({i.learner_id for i in body.items} - enrolled)
    if unknown:
        message = f"Không phải người học của hợp đồng: {unknown}"
        raise AppError(
            ErrorCode.VALIDATION_ERROR, 422, message, [{"field": "body.items", "issue": "invalid", "message": message}]
        )
    current = {
        r.learner_id: r
        for r in db.scalars(select(AsmSessionScore).where(AsmSessionScore.session_id == s.session_id).with_for_update())
    }
    before, after = {}, {}
    for item in body.items:
        row = current.get(item.learner_id)
        old = _snap(row)
        values = item.model_dump(exclude={"learner_id"})
        if row is None:
            row = AsmSessionScore(
                session_id=s.session_id,
                contract_id=c.contract_id,
                learner_id=item.learner_id,
                graded_by=actor.id,
                **values,
            )
            db.add(row)
        else:
            for k, v in values.items():
                setattr(row, k, v)
        new = {f: (str(values[f]) if f == "score" else values[f]) for f in FIELDS}
        if old != new:
            row.graded_by, row.graded_at = actor.id, now_utc()
            before[str(item.learner_id)], after[str(item.learner_id)] = old, new
    if after:
        db.flush()
        audit.record(db, "session_score.grade", "training_session", s.session_id, actor.id, before, after)
    db.commit()
    return _scores(db, s.session_id)


@portal_router.get("/scores", response_model=list[MyScoreOut], summary="Điểm của tôi (người học)")
def my_scores(user: SysUser = Depends(_learner), db: Session = Depends(get_db)):  # noqa: B008
    sc, s, c = AsmSessionScore, AsmFactTrainingSession, AsmFactTrainingContract
    rows = db.execute(
        select(sc, s.title, s.starts_at, c.contract_no)
        .outerjoin(s, s.session_id == sc.session_id)
        .outerjoin(c, c.contract_id == sc.contract_id)
        .where(sc.learner_id == user.id)
        .order_by(s.starts_at, sc.session_id)
    ).all()
    return [
        MyScoreOut(
            session_id=r[0].session_id,
            contract_id=r[0].contract_id,
            contract_no=r.contract_no,
            session_title=r.title,
            starts_at=r.starts_at,
            score=r[0].score,
            comment=r[0].comment,
            graded_at=r[0].graded_at,
        )
        for r in rows
    ]


# --- KPI người học ------------------------------------------------------------------------------------------------


def _learner_scope(db: Session, user: SysUser, contract_id: int | None) -> set[int] | None:
    """None = mọi người học; instructor: người học của hợp đồng mình dạy."""
    stmt = select(AsmFactEnrollment.learner_id)
    if contract_id is not None:
        stmt = stmt.where(AsmFactEnrollment.contract_id == contract_id)
    if not (VIEW_ALL & set(user.role_codes)):
        own = select(AsmFactTrainingContract.contract_id).where(AsmFactTrainingContract.instructor_id == user.id)
        stmt = stmt.where(AsmFactEnrollment.contract_id.in_(own))
    elif contract_id is None:
        return None
    return set(db.scalars(stmt))


def _avg_scores(db: Session, p: kpi.Period, learner_ids: set[int] | None) -> dict[int, tuple[int, float]]:
    sc, s = AsmSessionScore, AsmFactTrainingSession
    stmt = (
        select(sc.learner_id, func.count(), func.avg(sc.score))
        .join(s, s.session_id == sc.session_id)
        .where(s.starts_at >= p.start, s.starts_at < p.end)
        .group_by(sc.learner_id)
    )
    if learner_ids is not None:
        stmt = stmt.where(sc.learner_id.in_(learner_ids or {-1}))
    return {lid: (n, round(float(avg), 2)) for lid, n, avg in db.execute(stmt)}


def _items(db: Session, p: kpi.Period, ids: set[int]) -> list[LearnerKpiOut]:
    computed = formulas.compute_training(db, p, "learner")
    avgs = _avg_scores(db, p, ids)
    users = {u.id: u for u in db.scalars(select(SysUser).where(SysUser.id.in_(ids or {-1})))}
    out = []
    for lid in sorted(ids):
        n, avg = avgs.get(lid, (0, None))
        u = users.get(lid)
        out.append(
            LearnerKpiOut(
                learner_id=lid,
                username=u.username if u else None,
                full_name=u.full_name if u else None,
                sessions_scored=n,
                avg_score=avg,
                kpis=formulas.kpis_out(computed, lid),
            )
        )
    return out


@router.get("/kpi/learners", response_model=LearnerKpiList, summary="KPI-11/12/13 + điểm trung bình theo người học")
def learner_kpis(
    contract_id: int | None = Query(None, gt=0, description="Hợp đồng đào tạo"),
    p: kpi.Period = Depends(_period),  # noqa: B008
    user: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    scope = _learner_scope(db, user, contract_id)
    ids = scope if scope is not None else set(db.scalars(select(AsmFactEnrollment.learner_id)))
    items = _items(db, p, ids)
    return {"period": p.out(), "mode": "training", "items": items, "total": len(items)}


@router.get("/kpi/learners/{learner_id:int}", response_model=LearnerKpiOut, summary="KPI một người học")
def learner_kpi(
    learner_id: int,
    p: kpi.Period = Depends(_period),  # noqa: B008
    user: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    scope = _learner_scope(db, user, None)
    known = db.scalar(select(AsmFactEnrollment.learner_id).where(AsmFactEnrollment.learner_id == learner_id).limit(1))
    if known is None or (scope is not None and learner_id not in scope):
        raise _not_found("người học")
    return _items(db, p, {learner_id})[0]
