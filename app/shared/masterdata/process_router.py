"""
API đọc quy trình mẫu `/api/v2/md/process-templates` (02 §4; Giai đoạn 11) – mọi vai trò nội bộ.

Thêm, sửa, duyệt quy trình mẫu qua API: chưa làm (CẦN XÁC NHẬN) – quy trình seed bằng migration `0012`.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.core.rbac import INTERNAL_ROLES, require_roles
from app.shared.identity.models import SysUser
from app.shared.masterdata.models import MdProcessStep, MdProcessTemplate

router = APIRouter(prefix="/api/v2/md/process-templates", tags=["Master data – Process templates"])
_reader = require_roles(*INTERNAL_ROLES)


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_code: str
    seq: int
    name: str
    responsible_role: str
    required_skill: str | None
    standard_duration_hours: Decimal
    depends_on: list[str]
    is_checkpoint: bool
    checkpoint_type: str | None


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    process_code: str
    version: int
    name: str
    service_codes: list[str]
    direction: str
    transport_modes: list[str]
    status: str


class TemplateDetailOut(TemplateOut):
    steps: list[StepOut] = []


def steps_of(db: Session, template_id: int) -> list[MdProcessStep]:
    return list(
        db.scalars(select(MdProcessStep).where(MdProcessStep.template_id == template_id).order_by(MdProcessStep.seq))
    )


@router.get("", response_model=list[TemplateOut], summary="Danh sách quy trình mẫu")
def list_templates(_: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    return db.scalars(
        select(MdProcessTemplate).order_by(MdProcessTemplate.process_code, MdProcessTemplate.version)
    ).all()


@router.get("/{template_id:int}", response_model=TemplateDetailOut, summary="Quy trình mẫu + các bước")
def get_template(template_id: int, _: SysUser = Depends(_reader), db: Session = Depends(get_db)):  # noqa: B008
    template = db.get(MdProcessTemplate, template_id)
    if template is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy quy trình mẫu")
    steps = [StepOut.model_validate(s) for s in steps_of(db, template.id)]
    return TemplateDetailOut.model_validate(template).model_copy(update={"steps": steps})
