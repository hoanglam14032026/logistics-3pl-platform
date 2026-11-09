"""
Luồng 3 – hợp đồng đào tạo, buổi học, điểm danh (Giai đoạn 18).

- `lrn_training_contracts`: hợp đồng đào tạo với khách hàng (`md_parties`), người hướng dẫn (`instructor`), lịch học,
  học phí (NUMERIC(18,2) + mã tiền tệ – 06 §2.4). `draft` → `active` → `completed`; `cancelled` từ `draft` / `active`.
- `lrn_contract_learners`: người học (`learner`) của hợp đồng – mỗi lần ghi danh phát EV-302.
- `lrn_sessions`: buổi học (bắt buộc / tự chọn); hủy có lý do. `lrn_attendance`: điểm danh + hoàn thành (người hướng
  dẫn ghi) – đổi hoàn thành phát EV-303.
- Chấm điểm **không** thuộc Luồng 3 (04 §1) – ở Luồng 4 (`asm_session_scores`).
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AuditColumnsMixin, Base
from app.core.timeutil import now_utc


def _in(values) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class TrainingStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class LrnTrainingContract(AuditColumnsMixin, Base):
    __tablename__ = "lrn_training_contracts"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(TrainingStatus)})", name="status_valid"),
        CheckConstraint("start_date <= end_date", name="period_valid"),
        CheckConstraint("tuition_fee >= 0", name="tuition_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint("status <> 'cancelled' OR status_reason IS NOT NULL", name="cancel_has_reason"),
        Index("ix_lrn_training_contracts_customer", "customer_id", "status"),
        Index("ix_lrn_training_contracts_instructor", "instructor_id", "status"),
    )

    contract_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)  # DT-yyyy-nnnn
    contract_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="training", server_default="training"
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("md_parties.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    instructor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    schedule: Mapped[str] = mapped_column(String(500), nullable=False)  # lịch học, vd. "Tối thứ 2, 4 – 18:00–20:30"
    tuition_fee: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="VND", server_default="VND")
    note: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    status_reason: Mapped[str | None] = mapped_column(String(500))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LrnContractLearner(Base):
    __tablename__ = "lrn_contract_learners"
    __table_args__ = (UniqueConstraint("contract_id", "learner_id", name="uq_lrn_contract_learners_learner"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("lrn_training_contracts.id", ondelete="RESTRICT"), nullable=False
    )
    learner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    enrolled_by: Mapped[int | None] = mapped_column(BigInteger)


class SessionStatus(StrEnum):
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


class LrnSession(Base):
    __tablename__ = "lrn_sessions"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(SessionStatus)})", name="status_valid"),
        CheckConstraint("starts_at < ends_at", name="time_valid"),
        CheckConstraint("status <> 'cancelled' OR cancel_reason IS NOT NULL", name="cancel_has_reason"),
        UniqueConstraint("contract_id", "seq", name="uq_lrn_sessions_seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("lrn_training_contracts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(300))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("md_locations.id", ondelete="RESTRICT"))
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled", server_default="scheduled")
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger)


class AttendanceStatus(StrEnum):
    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"
    EXCUSED = "excused"


class CompletionStatus(StrEnum):
    COMPLETED = "completed"
    NOT_COMPLETED = "not_completed"


class LrnAttendance(Base):
    __tablename__ = "lrn_attendance"
    __table_args__ = (
        CheckConstraint(f"attendance IN ({_in(AttendanceStatus)})", name="attendance_valid"),
        CheckConstraint(f"completion IS NULL OR completion IN ({_in(CompletionStatus)})", name="completion_valid"),
        # vắng (không phép / có phép) không thể "hoàn thành" buổi
        CheckConstraint(
            "completion IS DISTINCT FROM 'completed' OR attendance IN ('present', 'late')", name="completed_present"
        ),
        UniqueConstraint("session_id", "learner_id", name="uq_lrn_attendance_learner"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("lrn_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    learner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    attendance: Mapped[str] = mapped_column(String(16), nullable=False)
    completion: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(500))
    recorded_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
