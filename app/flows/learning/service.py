"""
Nghiệp vụ Luồng 3 – hợp đồng đào tạo, buổi học, điểm danh (Giai đoạn 18).

Quyền:
- Hợp đồng: tạo / sửa (khi `draft`) / ghi danh / kích hoạt / hoàn tất / hủy – `admin`, `manager`. Đọc: `admin`,
  `manager`, `business_manager` (mọi hợp đồng), `instructor` (hợp đồng mình dạy); `customer` – qua cổng, hợp đồng của
  khách hàng mình; `learner` – qua cổng, hợp đồng mình học. Không thấy → 404.
- Buổi học: tạo / hủy – `admin`, `manager`, người hướng dẫn của hợp đồng (hợp đồng `draft` / `active`).
- Điểm danh + hoàn thành – chỉ người hướng dẫn của hợp đồng, hợp đồng `active`, buổi chưa hủy và đã bắt đầu.

Sự kiện (luồng nhận 4): kích hoạt → `lrn.contract.activated` + EV-302 cho mỗi người học + `lrn.session.scheduled` cho
mỗi buổi đã có; sau đó ghi danh / thêm buổi / hủy buổi phát ngay; đổi hoàn thành → EV-303; hoàn tất / hủy hợp đồng →
`lrn.contract.closed`. Mọi thao tác ghi nhật ký.
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.numbering import next_number
from app.core.rbac import Role, forbidden
from app.core.timeutil import VN_TZ, now_utc, today_vn
from app.flows.learning.models import (
    AttendanceStatus,
    CompletionStatus,
    LrnAttendance,
    LrnContractLearner,
    LrnSession,
    LrnTrainingContract,
    SessionStatus,
    TrainingStatus,
)
from app.shared.audit import service as audit
from app.shared.events import publisher
from app.shared.events.contracts import (
    EnrollmentCreated,
    ProgressCompleted,
    SessionCancelled,
    SessionScheduled,
    TrainingContractActivated,
    TrainingContractClosed,
)
from app.shared.identity.models import SysUser, SysUserRole
from app.shared.masterdata.models import MdLocation, MdParty, PartyStatus, PartyType

MODE = "training"
ENTITY = "training_contract"
SESSION = "training_session"
MANAGE = {Role.ADMIN.value, Role.MANAGER.value}
VIEW_ALL = {Role.ADMIN.value, Role.MANAGER.value, Role.BUSINESS_MANAGER.value}
AUDIT_FIELDS = (
    "contract_no",
    "customer_id",
    "title",
    "instructor_id",
    "start_date",
    "end_date",
    "schedule",
    "tuition_fee",
    "currency",
    "note",
    "status",
)


def _not_found(what: str = "hợp đồng đào tạo") -> AppError:
    return AppError(ErrorCode.NOT_FOUND, 404, f"Không tìm thấy {what}")


def _conflict(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_STATE_TRANSITION, 409, message)


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, message, [{"field": field, "issue": "invalid", "message": message}]
    )


def _snap(c: LrnTrainingContract) -> dict[str, Any]:
    out = audit.snapshot(c, AUDIT_FIELDS)
    out["tuition_fee"] = str(c.tuition_fee)
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in out.items()}


def _has_role(db: Session, user_id: int, role: Role) -> bool:
    return bool(
        db.scalar(
            select(func.count())
            .select_from(SysUserRole)
            .join(SysUser, SysUser.id == SysUserRole.user_id)
            .where(SysUserRole.user_id == user_id, SysUserRole.role == role.value, SysUser.is_active.is_(True))
        )
    )


def _check_customer(db: Session, party_id: int) -> None:
    party = db.get(MdParty, party_id)
    if party is None or party.status != PartyStatus.ACTIVE.value or party.party_type != PartyType.CUSTOMER.value:
        raise _invalid("body.customer_id", "Khách hàng không tồn tại, đã lưu trữ hoặc không phải loại customer")


def _check_instructor(db: Session, user_id: int) -> None:
    if not _has_role(db, user_id, Role.INSTRUCTOR):
        raise _invalid("body.instructor_id", "Người hướng dẫn phải là tài khoản đang hoạt động có vai trò instructor")


def _check_learners(db: Session, learner_ids: list[int]) -> None:
    bad = [i for i in learner_ids if not _has_role(db, i, Role.LEARNER)]
    if bad:
        raise _invalid("body.learner_ids", f"Không phải người học đang hoạt động: {bad}")


# --- Đọc ------------------------------------------------------------------------------------------------------------


def _visible_stmt(user: SysUser):
    stmt = select(LrnTrainingContract)
    roles = set(user.role_codes)
    if roles & VIEW_ALL:
        return stmt
    if Role.INSTRUCTOR.value in roles:
        return stmt.where(LrnTrainingContract.instructor_id == user.id)
    if Role.CUSTOMER.value in roles:  # cổng: không có hợp đồng nháp
        return stmt.where(
            LrnTrainingContract.customer_id == user.party_id, LrnTrainingContract.status != TrainingStatus.DRAFT.value
        )
    if Role.LEARNER.value in roles:
        enrolled = select(LrnContractLearner.contract_id).where(LrnContractLearner.learner_id == user.id)
        return stmt.where(
            LrnTrainingContract.id.in_(enrolled), LrnTrainingContract.status != TrainingStatus.DRAFT.value
        )
    return stmt.where(False)


def list_contracts(db: Session, user: SysUser, status: str | None = None) -> list[LrnTrainingContract]:
    stmt = _visible_stmt(user)
    if status:
        stmt = stmt.where(LrnTrainingContract.status == status)
    return list(db.scalars(stmt.order_by(LrnTrainingContract.id.desc()).limit(500)))


def get_contract(db: Session, contract_id: int, user: SysUser, for_update: bool = False) -> LrnTrainingContract:
    stmt = _visible_stmt(user).where(LrnTrainingContract.id == contract_id)
    contract = db.scalar(stmt.with_for_update() if for_update else stmt)
    if contract is None:
        raise _not_found()
    return contract


def learners_of(db: Session, contract_id: int) -> list[LrnContractLearner]:
    return list(
        db.scalars(
            select(LrnContractLearner)
            .where(LrnContractLearner.contract_id == contract_id)
            .order_by(LrnContractLearner.learner_id)
        )
    )


def sessions_of(db: Session, contract_id: int) -> list[LrnSession]:
    return list(db.scalars(select(LrnSession).where(LrnSession.contract_id == contract_id).order_by(LrnSession.seq)))


def attendance_of(db: Session, session_id: int, learner_id: int | None = None) -> list[LrnAttendance]:
    stmt = select(LrnAttendance).where(LrnAttendance.session_id == session_id)
    if learner_id is not None:
        stmt = stmt.where(LrnAttendance.learner_id == learner_id)
    return list(db.scalars(stmt.order_by(LrnAttendance.learner_id)))


# --- Hợp đồng -------------------------------------------------------------------------------------------------------


def _ensure_manager(user: SysUser) -> None:
    if not MANAGE & set(user.role_codes):
        raise forbidden()


def _publish(db: Session, event, entity: str, entity_id: int, actor_id: int | None) -> None:
    publisher.publish(db, event, entity, entity_id, actor_id, MODE)


def create_contract(db: Session, data: dict[str, Any], actor: SysUser) -> LrnTrainingContract:
    _ensure_manager(actor)
    _check_customer(db, data["customer_id"])
    _check_instructor(db, data["instructor_id"])
    learner_ids = sorted(set(data.pop("learner_ids", [])))
    _check_learners(db, learner_ids)
    contract = LrnTrainingContract(
        **data,
        contract_no=next_number(db, "DT", today_vn().year),
        status=TrainingStatus.DRAFT.value,
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(contract)
    db.flush()
    for lid in learner_ids:
        db.add(LrnContractLearner(contract_id=contract.id, learner_id=lid, enrolled_by=actor.id))
    audit.record(
        db,
        "training_contract.create",
        ENTITY,
        contract.id,
        actor.id,
        after={**_snap(contract), "learner_ids": learner_ids},
    )
    db.commit()
    db.refresh(contract)
    return contract


def update_contract(
    db: Session, contract_id: int, changes: dict[str, Any], actor: SysUser, expected_version: int | None
) -> LrnTrainingContract:
    _ensure_manager(actor)
    contract = get_contract(db, contract_id, actor, for_update=True)
    if expected_version is not None and expected_version != contract.row_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, 409, "Hợp đồng đã bị người khác thay đổi")
    if contract.status != TrainingStatus.DRAFT.value:
        raise _conflict(f"Chỉ sửa hợp đồng nháp (đang {contract.status})")
    if "customer_id" in changes:
        _check_customer(db, changes["customer_id"])
    if "instructor_id" in changes:
        _check_instructor(db, changes["instructor_id"])
    start = changes.get("start_date", contract.start_date)
    end = changes.get("end_date", contract.end_date)
    if start > end:
        raise _invalid("body.end_date", "Ngày kết thúc phải sau ngày bắt đầu")
    before = _snap(contract)
    for k, v in changes.items():
        setattr(contract, k, v)
    contract.updated_by = actor.id
    db.flush()
    old, new = audit.changed(before, _snap(contract))
    if new:
        audit.record(db, "training_contract.update", ENTITY, contract.id, actor.id, old, new)
    db.commit()
    db.refresh(contract)
    return contract


def _enrollment_event(db: Session, contract: LrnTrainingContract, learner_id: int) -> EnrollmentCreated:
    required = [s.id for s in sessions_of(db, contract.id) if s.is_required and s.status == SessionStatus.SCHEDULED]
    return EnrollmentCreated(
        cohort_id=contract.id,
        course_id=contract.id,
        learner_id=learner_id,
        required_resource_ids=required,
        contract_id=contract.id,
        instructor_id=contract.instructor_id,
    )


def _session_event(s: LrnSession) -> SessionScheduled:
    return SessionScheduled(
        session_id=s.id,
        contract_id=s.contract_id,
        seq=s.seq,
        title=s.title,
        starts_at=s.starts_at,
        ends_at=s.ends_at,
        is_required=s.is_required,
    )


def add_learners(db: Session, contract_id: int, learner_ids: list[int], actor: SysUser) -> list[LrnContractLearner]:
    _ensure_manager(actor)
    contract = get_contract(db, contract_id, actor, for_update=True)
    if contract.status not in (TrainingStatus.DRAFT.value, TrainingStatus.ACTIVE.value):
        raise _conflict(f"Hợp đồng đã {contract.status} – không ghi danh thêm được")
    existing = {r.learner_id for r in learners_of(db, contract.id)}
    new_ids = sorted(set(learner_ids) - existing)
    if not new_ids:
        raise AppError(ErrorCode.DUPLICATE, 409, "Người học đã được ghi danh")
    _check_learners(db, new_ids)
    for lid in new_ids:
        db.add(LrnContractLearner(contract_id=contract.id, learner_id=lid, enrolled_by=actor.id))
        if contract.status == TrainingStatus.ACTIVE.value:
            _publish(db, _enrollment_event(db, contract, lid), ENTITY, contract.id, actor.id)
    audit.record(db, "training_contract.enroll", ENTITY, contract.id, actor.id, after={"learner_ids": new_ids})
    db.commit()
    return learners_of(db, contract.id)


def activate(db: Session, contract_id: int, actor: SysUser) -> LrnTrainingContract:
    _ensure_manager(actor)
    contract = get_contract(db, contract_id, actor, for_update=True)
    if contract.status != TrainingStatus.DRAFT.value:
        raise _conflict(f"Chỉ kích hoạt hợp đồng nháp (đang {contract.status})")
    learners = learners_of(db, contract.id)
    if not learners:
        raise _conflict("Hợp đồng chưa có người học")
    contract.status, contract.activated_at, contract.updated_by = TrainingStatus.ACTIVE.value, now_utc(), actor.id
    db.flush()
    audit.record(
        db, "training_contract.activate", ENTITY, contract.id, actor.id, {"status": "draft"}, {"status": "active"}
    )
    _publish(
        db,
        TrainingContractActivated(
            contract_id=contract.id,
            contract_no=contract.contract_no,
            customer_id=contract.customer_id,
            instructor_id=contract.instructor_id,
            title=contract.title,
            start_date=contract.start_date,
            end_date=contract.end_date,
        ),
        ENTITY,
        contract.id,
        actor.id,
    )
    for s in sessions_of(db, contract.id):
        if s.status == SessionStatus.SCHEDULED:
            _publish(db, _session_event(s), SESSION, s.id, actor.id)
    for r in learners:
        _publish(db, _enrollment_event(db, contract, r.learner_id), ENTITY, contract.id, actor.id)
    db.commit()
    db.refresh(contract)
    return contract


def close(db: Session, contract_id: int, target: str, reason: str | None, actor: SysUser) -> LrnTrainingContract:
    _ensure_manager(actor)
    contract = get_contract(db, contract_id, actor, for_update=True)
    allowed = (
        (TrainingStatus.ACTIVE.value,)
        if target == TrainingStatus.COMPLETED.value
        else (TrainingStatus.DRAFT.value, TrainingStatus.ACTIVE.value)
    )
    if contract.status not in allowed:
        raise _conflict(f"Hợp đồng đang {contract.status} – không chuyển sang {target} được")
    before = contract.status
    contract.status, contract.status_reason, contract.updated_by = target, reason, actor.id
    db.flush()
    audit.record(
        db,
        f"training_contract.{'complete' if target == 'completed' else 'cancel'}",
        ENTITY,
        contract.id,
        actor.id,
        {"status": before},
        {"status": target, "reason": reason},
    )
    if before == TrainingStatus.ACTIVE.value:
        _publish(db, TrainingContractClosed(contract_id=contract.id, status=target), ENTITY, contract.id, actor.id)
    db.commit()
    db.refresh(contract)
    return contract


# --- Buổi học -------------------------------------------------------------------------------------------------------


def _ensure_session_writer(contract: LrnTrainingContract, actor: SysUser) -> None:
    if not (MANAGE & set(actor.role_codes) or actor.id == contract.instructor_id):
        raise forbidden("Chỉ admin, manager hoặc người hướng dẫn của hợp đồng")


def create_session(db: Session, contract_id: int, data: dict[str, Any], actor: SysUser) -> LrnSession:
    contract = get_contract(db, contract_id, actor, for_update=True)
    _ensure_session_writer(contract, actor)
    if contract.status not in (TrainingStatus.DRAFT.value, TrainingStatus.ACTIVE.value):
        raise _conflict(f"Hợp đồng đã {contract.status} – không thêm buổi học được")
    start_vn = data["starts_at"].astimezone(VN_TZ).date()
    end_vn = data["ends_at"].astimezone(VN_TZ).date()
    if start_vn < contract.start_date or end_vn > contract.end_date:
        raise _invalid("body.starts_at", "Buổi học phải nằm trong thời hạn hợp đồng")
    if data.get("location_id") is not None:
        loc = db.get(MdLocation, data["location_id"])
        if loc is None or loc.status != PartyStatus.ACTIVE.value:
            raise _invalid("body.location_id", "Địa điểm không tồn tại hoặc đã lưu trữ")
    seq = (db.scalar(select(func.max(LrnSession.seq)).where(LrnSession.contract_id == contract.id)) or 0) + 1
    session = LrnSession(contract_id=contract.id, seq=seq, created_by=actor.id, **data)
    db.add(session)
    db.flush()
    audit.record(
        db,
        "training_session.create",
        SESSION,
        session.id,
        actor.id,
        after={
            "contract_id": contract.id,
            "seq": seq,
            "title": session.title,
            "starts_at": session.starts_at.isoformat(),
            "ends_at": session.ends_at.isoformat(),
            "is_required": session.is_required,
        },
    )
    if contract.status == TrainingStatus.ACTIVE.value:
        _publish(db, _session_event(session), SESSION, session.id, actor.id)
    db.commit()
    db.refresh(session)
    return session


def get_session(db: Session, session_id: int, actor: SysUser) -> tuple[LrnSession, LrnTrainingContract]:
    session = db.get(LrnSession, session_id)
    if session is None:
        raise _not_found("buổi học")
    contract = get_contract(db, session.contract_id, actor)  # quyền xem theo hợp đồng
    return session, contract


def cancel_session(db: Session, session_id: int, reason: str, actor: SysUser) -> LrnSession:
    session, contract = get_session(db, session_id, actor)
    _ensure_session_writer(contract, actor)
    db.refresh(session, with_for_update=True)
    if session.status == SessionStatus.CANCELLED.value:
        raise _conflict("Buổi học đã hủy")
    if db.scalar(select(func.count()).select_from(LrnAttendance).where(LrnAttendance.session_id == session.id)):
        raise _conflict("Buổi học đã điểm danh – không hủy được")
    session.status, session.cancel_reason = SessionStatus.CANCELLED.value, reason
    db.flush()
    audit.record(
        db,
        "training_session.cancel",
        SESSION,
        session.id,
        actor.id,
        {"status": "scheduled"},
        {"status": "cancelled", "reason": reason},
    )
    if contract.status == TrainingStatus.ACTIVE.value:
        _publish(
            db,
            SessionCancelled(session_id=session.id, contract_id=contract.id, reason=reason),
            SESSION,
            session.id,
            actor.id,
        )
    db.commit()
    db.refresh(session)
    return session


# --- Điểm danh, hoàn thành ------------------------------------------------------------------------------------------


def record_attendance(db: Session, session_id: int, items: list[dict[str, Any]], actor: SysUser) -> list[LrnAttendance]:
    session, contract = get_session(db, session_id, actor)
    if actor.id != contract.instructor_id:
        raise forbidden("Chỉ người hướng dẫn của hợp đồng điểm danh, ghi nhận hoàn thành")
    if contract.status != TrainingStatus.ACTIVE.value:
        raise _conflict(f"Hợp đồng đang {contract.status} – chỉ điểm danh khi đang hiệu lực (active)")
    db.refresh(session, with_for_update=True)
    if session.status == SessionStatus.CANCELLED.value:
        raise _conflict("Buổi học đã hủy")
    now = now_utc()
    if session.starts_at > now:
        raise _conflict("Buổi học chưa bắt đầu – chưa điểm danh được")
    enrolled = {r.learner_id for r in learners_of(db, contract.id)}
    unknown = sorted({i["learner_id"] for i in items} - enrolled)
    if unknown:
        raise _invalid("body.items", f"Không phải người học của hợp đồng: {unknown}")
    current = {a.learner_id: a for a in attendance_of(db, session.id)}
    before, after = {}, {}
    for item in items:
        lid = item["learner_id"]
        if item.get("completion") == CompletionStatus.COMPLETED.value and item["attendance"] not in (
            AttendanceStatus.PRESENT.value,
            AttendanceStatus.LATE.value,
        ):
            raise _invalid("body.items", f"Người học {lid} vắng – không ghi hoàn thành được")
        row = current.get(lid)
        old = (row.attendance, row.completion, row.note) if row else None
        new = (item["attendance"], item.get("completion"), item.get("note"))
        if old == new:
            continue
        if row is None:
            row = LrnAttendance(
                session_id=session.id,
                learner_id=lid,
                recorded_by=actor.id,
                **dict(zip(("attendance", "completion", "note"), new, strict=True)),
            )
            db.add(row)
        else:
            row.attendance, row.completion, row.note = new
            row.recorded_by, row.recorded_at = actor.id, now
        before[str(lid)] = dict(zip(("attendance", "completion", "note"), old, strict=True)) if old else None
        after[str(lid)] = dict(zip(("attendance", "completion", "note"), new, strict=True))
        old_done = bool(old and old[1] == CompletionStatus.COMPLETED.value)
        new_done = new[1] == CompletionStatus.COMPLETED.value
        if old_done != new_done:
            _publish(
                db,
                ProgressCompleted(
                    learner_id=lid,
                    course_id=contract.id,
                    resource_id=session.id,
                    completed_at=now,
                    completed=new_done,
                    contract_id=contract.id,
                    attendance=new[0],
                ),
                SESSION,
                session.id,
                actor.id,
            )
    if after:
        db.flush()
        audit.record(db, "training_session.attendance", SESSION, session.id, actor.id, before, after)
    db.commit()
    return attendance_of(db, session.id)


def my_sessions(db: Session, learner: SysUser) -> list[dict[str, Any]]:
    """Cổng người học: buổi học của các hợp đồng mình học (không nháp) + điểm danh / hoàn thành của mình."""
    contracts = {c.id: c for c in list_contracts(db, learner)}
    out = []
    for cid, c in sorted(contracts.items()):
        for s in sessions_of(db, cid):
            a = next(iter(attendance_of(db, s.id, learner.id)), None)
            out.append(
                {
                    "session_id": s.id,
                    "contract_id": cid,
                    "contract_no": c.contract_no,
                    "seq": s.seq,
                    "title": s.title,
                    "starts_at": s.starts_at,
                    "ends_at": s.ends_at,
                    "is_required": s.is_required,
                    "status": s.status,
                    "attendance": a.attendance if a else None,
                    "completion": a.completion if a else None,
                }
            )
    return out
