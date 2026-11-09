"""
API bước thực hiện ở Luồng 1 (Giai đoạn 13) – đồng bộ hai chiều với Luồng 2.

- `GET /api/v2/ops/shipments/{shipment_id}/tasks` – đọc như lô hàng (`ops_staff`, `manager`, `business_manager`,
  `instructor`, `admin`).
- `POST /api/v2/ops/tasks/{task_id}:start` · `:complete` · `:block` (lý do) · `:unblock` – `ops_staff` phụ trách
  lô / bước.
  Phát EV-131 / EV-132 / EV-133 / `ops.task.unblocked`; Luồng 2 xác nhận (`ctl.task.synced`) hoặc trả trạng thái chuẩn
  khi xung đột – `pending_local` cho biết thay đổi chưa được xác nhận.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import Role, require_roles
from app.core.timeutil import UtcDatetime
from app.flows.operations import shipment_service, task_service
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops", tags=["Ops – Tasks"])

_reader = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.INSTRUCTOR, Role.ADMIN)
_staff = require_roles(Role.OPS_STAFF)


class OpsTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_id: int
    ctl_task_id: int
    step_code: str
    seq: int
    name: str
    responsible_role: str
    depends_on: list[str]
    is_checkpoint: bool
    planned_start: UtcDatetime
    planned_end: UtcDatetime
    assignee_id: int | None
    status: str
    progress_pct: int
    actual_start: UtcDatetime | None
    actual_end: UtcDatetime | None
    blocked_reason: str | None
    revision: int
    pending_local: bool
    last_synced_at: datetime | None


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] | None = None


class BlockBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


@router.get("/shipments/{shipment_id:int}/tasks", response_model=list[OpsTaskOut], summary="Các bước của lô")
def list_tasks(shipment_id: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):
    shipment_service.get_shipment(db, shipment_id)
    return task_service.tasks_of(db, shipment_id)


@router.post("/tasks/{task_id:int}:start", response_model=OpsTaskOut, summary="Bắt đầu bước → EV-131")
def start(
    task_id: int,
    body: NoteBody | None = None,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),
):
    return task_service.act(db, task_id, "start", actor, note=body.note if body else None)


@router.post("/tasks/{task_id:int}:complete", response_model=OpsTaskOut, summary="Hoàn thành bước → EV-132")
def complete(
    task_id: int,
    body: NoteBody | None = None,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),
):
    return task_service.act(db, task_id, "complete", actor, note=body.note if body else None)


@router.post("/tasks/{task_id:int}:block", response_model=OpsTaskOut, summary="Báo bước bị chặn → EV-133")
def block(
    task_id: int,
    body: BlockBody,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),
):
    return task_service.act(db, task_id, "block", actor, reason=body.reason)


@router.post("/tasks/{task_id:int}:unblock", response_model=OpsTaskOut, summary="Gỡ chặn → ops.task.unblocked")
def unblock(
    task_id: int,
    body: NoteBody | None = None,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),
):
    return task_service.act(db, task_id, "unblock", actor, note=body.note if body else None)
