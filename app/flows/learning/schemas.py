"""Schema API Luồng 3 – đào tạo (Giai đoạn 18)."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.timeutil import UtcDatetime
from app.flows.learning.models import AttendanceStatus, CompletionStatus

Text300 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class ContractCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int = Field(gt=0, description="Khách hàng (md_parties loại customer)")
    title: Text300
    instructor_id: int = Field(gt=0, description="Người hướng dẫn (vai trò instructor)")
    learner_ids: list[int] = Field(default_factory=list, max_length=500)
    start_date: date
    end_date: date
    schedule: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    tuition_fee: Money
    currency: Currency = "VND"
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None

    @model_validator(mode="after")
    def _period(self):
        if self.start_date > self.end_date:
            raise ValueError("start_date phải ≤ end_date")
        return self


class ContractUpdate(BaseModel):
    """PATCH khi `draft`."""

    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = Field(None, gt=0)
    title: Text300 | None = None
    instructor_id: int | None = Field(None, gt=0)
    start_date: date | None = None
    end_date: date | None = None
    schedule: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None
    tuition_fee: Money | None = None
    currency: Currency | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None

    @model_validator(mode="after")
    def _not_null(self):
        for f in self.model_fields_set - {"note"}:
            if getattr(self, f) is None:
                raise ValueError(f"{f} không được null")
        return self


class LearnersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    learner_ids: list[int] = Field(min_length=1, max_length=500)


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Reason


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_no: str
    contract_type: str
    customer_id: int
    title: str
    instructor_id: int
    start_date: date
    end_date: date
    schedule: str
    tuition_fee: Decimal
    currency: str
    note: str | None
    status: str
    status_reason: str | None
    activated_at: UtcDatetime | None
    created_at: UtcDatetime
    row_version: int
    learner_ids: list[int] = []


class PortalContractOut(BaseModel):
    """Cổng (khách hàng / người học): không có ghi chú nội bộ, người tạo, row_version."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_no: str
    title: str
    start_date: date
    end_date: date
    schedule: str
    tuition_fee: Decimal
    currency: str
    status: str
    learner_count: int = 0


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Text300
    topic: Text300 | None = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    location_id: int | None = Field(None, gt=0)
    is_required: bool = True

    @model_validator(mode="after")
    def _time(self):
        if self.starts_at >= self.ends_at:
            raise ValueError("starts_at phải trước ends_at")
        return self


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_id: int
    seq: int
    title: str
    topic: str | None
    starts_at: UtcDatetime
    ends_at: UtcDatetime
    location_id: int | None
    is_required: bool
    status: str
    cancel_reason: str | None


class AttendanceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learner_id: int = Field(gt=0)
    attendance: AttendanceStatus
    completion: CompletionStatus | None = Field(None, description="Người hướng dẫn ghi nhận hoàn thành / chưa")
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class AttendanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AttendanceItem] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _unique(self):
        ids = [i.learner_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Mỗi người học một dòng")
        return self


class AttendanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    learner_id: int
    attendance: str
    completion: str | None
    note: str | None
    recorded_by: int
    recorded_at: UtcDatetime


class MySessionOut(BaseModel):
    session_id: int
    contract_id: int
    contract_no: str
    seq: int
    title: str
    starts_at: UtcDatetime
    ends_at: UtcDatetime
    is_required: bool
    status: str
    attendance: str | None
    completion: str | None
