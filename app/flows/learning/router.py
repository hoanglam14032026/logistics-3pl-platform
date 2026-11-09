"""
API Luồng 3 – đào tạo `/api/v2/learning/...` (Giai đoạn 18) và cổng `/api/v2/portal/...`.

- Hợp đồng đào tạo: `GET|POST /training-contracts`, `GET|PATCH /training-contracts/{id}`, `POST …/{id}/learners`,
  `:activate`, `:complete`, `:cancel` – quyền xem `service.py`.
- Buổi học: `GET|POST /training-contracts/{id}/sessions`, `POST /sessions/{id}:cancel`; điểm danh + hoàn thành:
  `GET|PUT /sessions/{id}/attendance` (người hướng dẫn của hợp đồng).
- Cổng: `GET /api/v2/portal/training-contracts` (customer – hợp đồng của khách hàng mình; learner – hợp đồng mình
  học), `GET /api/v2/portal/learning/sessions` (learner – buổi học + điểm danh, hoàn thành của mình).
"""

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import Role, require_roles
from app.core.versioning import etag, parse_if_match
from app.flows.learning import service
from app.flows.learning.schemas import (
    AttendanceIn,
    AttendanceOut,
    ContractCreate,
    ContractOut,
    ContractUpdate,
    LearnersIn,
    MySessionOut,
    PortalContractOut,
    ReasonIn,
    SessionCreate,
    SessionOut,
)
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/learning", tags=["Learning – Training contracts"])
portal_router = APIRouter(prefix="/api/v2/portal", tags=["Portal – Training"])

_staff = require_roles(Role.ADMIN, Role.MANAGER, Role.BUSINESS_MANAGER, Role.INSTRUCTOR)
_manage = require_roles(Role.ADMIN, Role.MANAGER)
_session_writer = require_roles(Role.ADMIN, Role.MANAGER, Role.INSTRUCTOR)
_instructor = require_roles(Role.INSTRUCTOR)
_portal = require_roles(Role.CUSTOMER, Role.LEARNER)
_learner = require_roles(Role.LEARNER)


def _out(db: Session, c, response: Response | None = None) -> ContractOut:
    if response is not None:
        response.headers["ETag"] = etag(c.row_version)
    return ContractOut.model_validate(c).model_copy(
        update={"learner_ids": [r.learner_id for r in service.learners_of(db, c.id)]}
    )


@router.get("/training-contracts", response_model=list[ContractOut], summary="Hợp đồng đào tạo")
def list_contracts(
    status_: str | None = Query(None, alias="status", pattern=r"^(draft|active|completed|cancelled)$"),
    user: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return [_out(db, c) for c in service.list_contracts(db, user, status_)]


@router.post(
    "/training-contracts", response_model=ContractOut, status_code=status.HTTP_201_CREATED, summary="Tạo hợp đồng"
)
def create_contract(
    body: ContractCreate,
    response: Response,
    actor: SysUser = Depends(_manage),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _out(db, service.create_contract(db, body.model_dump(), actor), response)


@router.get("/training-contracts/{contract_id:int}", response_model=ContractOut, summary="Chi tiết hợp đồng")
def get_contract(
    contract_id: int,
    response: Response,
    user: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _out(db, service.get_contract(db, contract_id, user), response)


@router.patch("/training-contracts/{contract_id:int}", response_model=ContractOut, summary="Sửa hợp đồng nháp")
def update_contract(
    contract_id: int,
    body: ContractUpdate,
    response: Response,
    if_match: str | None = Header(None),
    actor: SysUser = Depends(_manage),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    changes = body.model_dump(exclude_unset=True)
    return _out(db, service.update_contract(db, contract_id, changes, actor, parse_if_match(if_match)), response)


@router.post("/training-contracts/{contract_id:int}/learners", response_model=ContractOut, summary="Ghi danh")
def add_learners(
    contract_id: int,
    body: LearnersIn,
    actor: SysUser = Depends(_manage),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    service.add_learners(db, contract_id, body.learner_ids, actor)
    return _out(db, service.get_contract(db, contract_id, actor))


@router.post("/training-contracts/{contract_id:int}:activate", response_model=ContractOut, summary="Kích hoạt")
def activate(contract_id: int, actor: SysUser = Depends(_manage), db: Session = Depends(get_db)):  # noqa: B008
    return _out(db, service.activate(db, contract_id, actor))


@router.post("/training-contracts/{contract_id:int}:complete", response_model=ContractOut, summary="Hoàn tất")
def complete(contract_id: int, actor: SysUser = Depends(_manage), db: Session = Depends(get_db)):  # noqa: B008
    return _out(db, service.close(db, contract_id, "completed", None, actor))


@router.post("/training-contracts/{contract_id:int}:cancel", response_model=ContractOut, summary="Hủy (lý do)")
def cancel(
    contract_id: int,
    body: ReasonIn,
    actor: SysUser = Depends(_manage),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return _out(db, service.close(db, contract_id, "cancelled", body.reason, actor))


@router.get("/training-contracts/{contract_id:int}/sessions", response_model=list[SessionOut], summary="Buổi học")
def list_sessions(
    contract_id: int,
    user: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    service.get_contract(db, contract_id, user)
    return service.sessions_of(db, contract_id)


@router.post(
    "/training-contracts/{contract_id:int}/sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo buổi học",
)
def create_session(
    contract_id: int,
    body: SessionCreate,
    actor: SysUser = Depends(_session_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return service.create_session(db, contract_id, body.model_dump(), actor)


@router.post("/sessions/{session_id:int}:cancel", response_model=SessionOut, summary="Hủy buổi học (lý do)")
def cancel_session(
    session_id: int,
    body: ReasonIn,
    actor: SysUser = Depends(_session_writer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    return service.cancel_session(db, session_id, body.reason, actor)


@router.get("/sessions/{session_id:int}/attendance", response_model=list[AttendanceOut], summary="Điểm danh")
def get_attendance(
    session_id: int,
    user: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    session, _ = service.get_session(db, session_id, user)
    return service.attendance_of(db, session.id)


@router.put(
    "/sessions/{session_id:int}/attendance",
    response_model=list[AttendanceOut],
    summary="Điểm danh + ghi nhận hoàn thành (người hướng dẫn) → EV-303",
)
def put_attendance(
    session_id: int,
    body: AttendanceIn,
    actor: SysUser = Depends(_instructor),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items = [i.model_dump(mode="json") for i in body.items]
    return service.record_attendance(db, session_id, items, actor)


# --- Cổng -------------------------------------------------------------------------------------------------------


@portal_router.get("/training-contracts", response_model=list[PortalContractOut], summary="Hợp đồng đào tạo của tôi")
def portal_contracts(user: SysUser = Depends(_portal), db: Session = Depends(get_db)):  # noqa: B008
    return [
        PortalContractOut.model_validate(c).model_copy(update={"learner_count": len(service.learners_of(db, c.id))})
        for c in service.list_contracts(db, user)
    ]


@portal_router.get("/learning/sessions", response_model=list[MySessionOut], summary="Buổi học của tôi (người học)")
def my_sessions(user: SysUser = Depends(_learner), db: Session = Depends(get_db)):  # noqa: B008
    return service.my_sessions(db, user)
