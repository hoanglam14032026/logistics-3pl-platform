"""
Phân công tự động (Giai đoạn 11) – chọn người theo **vai trò**, **năng lực**, **tải công việc**.

1. Vai trò: người dùng đang hoạt động có `responsible_role` của bước (lô: `ops_staff`; bước điểm kiểm soát: người kiểm
   soát `manager`). Hồ sơ `is_available = false` (nghỉ phép…) bị loại.
2. Phạm vi (BR-204): kế hoạch hợp đồng có thành viên thực hiện **do manager thêm** → chỉ chọn trong số đó; chưa có →
   mọi `ops_staff`. Người được chọn được hệ thống thêm làm thành viên (`added_by` NULL – không giới hạn lần sau).
3. Năng lực: `ctl_staff_profiles.skills` {mã: mức 1–5} so với `required_skill` các bước của lô – ưu tiên người đủ nhiều
   năng lực nhất, rồi mức trung bình cao hơn.
4. Tải công việc: tổng giờ chuẩn các bước **chưa xong** (open, in_progress, blocked) đã giao, so với
   `max_load_hours` (mặc định 40). Ưu tiên người không vượt sức chứa sau khi nhận lô, rồi tỉ lệ tải thấp nhất, ít bước
   mở hơn, id nhỏ hơn.

Thứ tự so sánh: (thiếu năng lực ít nhất, không quá tải, tỉ lệ tải sau khi nhận thấp, mức năng lực cao, ít bước mở, id).
Kết quả kèm giải thích (`score`) – ghi vào `ctl_assignments` và nhật ký.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.flows.control.models import ACTIVE_TASK_STATUSES, CtlShipmentTask, CtlStaffProfile
from app.shared.identity.models import SysUser, SysUserRole

DEFAULT_MAX_LOAD = Decimal("40")
_LOCK_KEY = 3_100_202_611  # khóa advisory: hai lần phân công song song không cùng đọc tải cũ


@dataclass
class Candidate:
    user_id: int
    username: str
    skills_matched: list[str]
    skills_missing: list[str]
    avg_level: float
    load_hours: str
    load_after: str
    max_load_hours: str
    load_ratio_after: float
    over_capacity: bool
    open_tasks: int

    def key(self):
        return (
            len(self.skills_missing),
            self.over_capacity,
            self.load_ratio_after,
            -self.avg_level,
            self.open_tasks,
            self.user_id,
        )


def serialize(c: "Candidate") -> dict:
    return asdict(c)


def lock(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})


def users_with_role(db: Session, role: str) -> list[SysUser]:
    stmt = (
        select(SysUser)
        .join(SysUserRole, SysUserRole.user_id == SysUser.id)
        .where(SysUserRole.role == role, SysUser.is_active.is_(True))
        .order_by(SysUser.id)
    )
    return list(db.scalars(stmt).unique())


def loads(db: Session, exclude_shipment_plan_id: int | None = None) -> dict[int, tuple[Decimal, int]]:
    stmt = select(
        CtlShipmentTask.assignee_id, func.coalesce(func.sum(CtlShipmentTask.standard_duration_hours), 0), func.count()
    ).where(CtlShipmentTask.status.in_(ACTIVE_TASK_STATUSES), CtlShipmentTask.assignee_id.is_not(None))
    if exclude_shipment_plan_id is not None:
        stmt = stmt.where(CtlShipmentTask.shipment_plan_id != exclude_shipment_plan_id)
    return {
        uid: (Decimal(hours), count) for uid, hours, count in db.execute(stmt.group_by(CtlShipmentTask.assignee_id))
    }


def profiles(db: Session) -> dict[int, CtlStaffProfile]:
    return {p.user_id: p for p in db.scalars(select(CtlStaffProfile))}


def rank(
    db: Session,
    role: str,
    required_skills: set[str],
    extra_hours: Decimal,
    restrict_to: set[int] | None = None,
    exclude: set[int] | None = None,
    exclude_shipment_plan_id: int | None = None,
) -> list[Candidate]:
    current = loads(db, exclude_shipment_plan_id)
    profs = profiles(db)
    out = []
    for user in users_with_role(db, role):
        if (restrict_to is not None and user.id not in restrict_to) or (exclude and user.id in exclude):
            continue
        prof = profs.get(user.id)
        if prof is not None and not prof.is_available:
            continue
        skills = (prof.skills if prof else {}) or {}
        matched = sorted(s for s in required_skills if int(skills.get(s, 0)) >= 1)
        missing = sorted(required_skills - set(matched))
        levels = [int(skills[s]) for s in matched]
        hours, open_tasks = current.get(user.id, (Decimal(0), 0))
        capacity = Decimal(prof.max_load_hours) if prof else DEFAULT_MAX_LOAD
        after = hours + extra_hours
        out.append(
            Candidate(
                user_id=user.id,
                username=user.username,
                skills_matched=matched,
                skills_missing=missing,
                avg_level=round(sum(levels) / len(levels), 2) if levels else 0.0,
                load_hours=str(hours),
                load_after=str(after),
                max_load_hours=str(capacity),
                load_ratio_after=round(float(after / capacity), 4),
                over_capacity=after > capacity,
                open_tasks=open_tasks,
            )
        )
    return sorted(out, key=Candidate.key)
