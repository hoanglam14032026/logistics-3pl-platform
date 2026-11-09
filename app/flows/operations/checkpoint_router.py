"""
API điểm kiểm soát của lô (Giai đoạn 16): nhận hàng, thông quan, xuất kho, giao hàng.

- `GET /api/v2/ops/shipments/{id}/checkpoints` – đọc như lô hàng (kèm các lần xác nhận).
- `POST /api/v2/ops/shipments/{id}/checkpoints` – `ops_staff` phụ trách lô (mặc định tạo đủ 4 loại).
- `POST /api/v2/ops/checkpoints/{id}:evaluate` – `ops_staff` phụ trách lô: `passed` / `failed` + thời điểm thực tế →
  `ops.checkpoint.evaluated` (KPI-05); thông quan đạt → EV-141 (KPI-06).
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import Role, require_roles
from app.core.timeutil import UtcDatetime
from app.flows.operations import checkpoint_service as service
from app.flows.operations import shipment_service
from app.flows.operations.models import CheckpointType, CustomsChannel
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/v2/ops", tags=["Ops – Checkpoints"])

_reader = require_roles(Role.OPS_STAFF, Role.MANAGER, Role.BUSINESS_MANAGER, Role.INSTRUCTOR, Role.ADMIN)
_staff = require_roles(Role.OPS_STAFF)


class CheckpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_type: CheckpointType
    planned_at: AwareDatetime | None = None
    location_id: int | None = Field(None, gt=0)


class CheckpointsCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CheckpointIn] = Field(default_factory=list, max_length=4, description="Bỏ trống: tạo đủ 4 loại")

    @model_validator(mode="after")
    def _unique(self):
        types = [i.checkpoint_type for i in self.items]
        if len(types) != len(set(types)):
            raise ValueError("Mỗi loại điểm kiểm soát chỉ một lần")
        return self


class CustomsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    declaration_no: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9A-Za-z/-]{4,40}$")]
    channel: CustomsChannel
    submitted_at: AwareDatetime


class EvaluateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: Literal["passed", "failed"]
    actual_at: AwareDatetime = Field(description="Thời điểm thực tế tại điểm kiểm soát")
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)] | None = None
    customs: CustomsIn | None = Field(None, description="Thông quan đạt: tờ khai, luồng, thời điểm nộp")


class EvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attempt_no: int
    result: str
    actual_at: UtcDatetime
    note: str | None
    declaration_no: str | None
    customs_channel: str | None
    customs_submitted_at: UtcDatetime | None
    evaluated_by: int
    evaluated_at: UtcDatetime


class CheckpointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_id: int
    checkpoint_type: str
    seq: int
    location_id: int | None
    planned_at: UtcDatetime | None
    status: str
    attempts: int
    actual_at: UtcDatetime | None
    evaluated_by: int | None
    created_at: datetime
    evaluations: list[EvaluationOut] = []


def _out(db: Session, cp) -> CheckpointOut:
    return CheckpointOut.model_validate(cp).model_copy(
        update={"evaluations": [EvaluationOut.model_validate(e) for e in service.evaluations_of(db, cp.id)]}
    )


@router.get("/shipments/{shipment_id:int}/checkpoints", response_model=list[CheckpointOut], summary="Điểm kiểm soát")
def list_checkpoints(
    shipment_id: int,
    _: SysUser = Depends(_reader),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    shipment_service.get_shipment(db, shipment_id)
    return [_out(db, c) for c in service.list_checkpoints(db, shipment_id)]


@router.post(
    "/shipments/{shipment_id:int}/checkpoints",
    response_model=list[CheckpointOut],
    status_code=status.HTTP_201_CREATED,
    summary="Tạo điểm kiểm soát cho lô",
)
def create_checkpoints(
    shipment_id: int,
    body: CheckpointsCreateIn | None = None,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    items = [i.model_dump(mode="python") for i in body.items] if body else []
    for i in items:
        i["checkpoint_type"] = i["checkpoint_type"].value
    return [_out(db, c) for c in service.create(db, shipment_id, items, actor)]


@router.post(
    "/checkpoints/{checkpoint_id:int}:evaluate",
    response_model=CheckpointOut,
    summary="Xác nhận qua / trượt tại điểm kiểm soát → ops.checkpoint.evaluated (+ EV-141 khi thông quan đạt)",
)
def evaluate_checkpoint(
    checkpoint_id: int,
    body: EvaluateIn,
    actor: SysUser = Depends(_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    customs = None
    if body.customs:
        customs = {**body.customs.model_dump(), "channel": body.customs.channel.value}
    cp = service.evaluate(db, checkpoint_id, body.result, body.actual_at, body.note, customs, actor)
    return _out(db, cp)
