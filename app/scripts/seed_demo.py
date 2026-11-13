"""Nạp dữ liệu demo (chạy lại không nhân đôi):

    python -m app.scripts.seed_demo

Tạo nếu chưa có: 4 tài khoản demo (admin, ops_staff, learner, customer), 1 khách hàng mẫu, 1 hợp đồng mẫu (nháp).
Chủ đề T01..T07 do migration 0024 nạp sẵn – script chỉ kiểm tra đủ 7 chủ đề, không nạp lại.
Mật khẩu demo công khai: CHỈ dùng cho bản thử nghiệm, không dùng cho môi trường thật.
"""

import sys
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, engine
from app.core.migrations import upgrade_to_head
from app.core.rbac import Role
from app.flows.learning.models import LrnTopic
from app.flows.operations import service as contract_service
from app.flows.operations.models import OpsServiceContract
from app.flows.operations.schemas import ContractCreate
from app.shared.identity import service as identity_service
from app.shared.identity.models import SysUser
from app.shared.identity.schemas import UserCreate
from app.shared.masterdata import service as masterdata_service
from app.shared.masterdata.models import MdParty
from app.shared.masterdata.schemas import PartyCreate

DEMO_PASSWORD = "Demo@123"
DEMO_CUSTOMER_TAX_CODE = "0101234567"
DEMO_CONTRACT_REF = "DEMO-HD-001"
EXPECTED_TOPICS = [f"T0{i}" for i in range(1, 8)]

# (username, họ tên, vai trò)
DEMO_USERS = [
    ("admin", "Quản trị demo", Role.ADMIN),
    ("ops_staff", "Nhân viên vận hành demo", Role.OPS_STAFF),
    ("learner", "Người học demo", Role.LEARNER),
    ("customer", "Khách hàng demo", Role.CUSTOMER),
]


def _get_user(db: Session, username: str) -> SysUser | None:
    return db.scalar(select(SysUser).where(SysUser.username == username))


def _ensure_user(db: Session, username: str, full_name: str, role: Role, party_id: int | None, actor_id: int | None):
    user = _get_user(db, username)
    if user is not None:
        return user, False
    data = UserCreate(
        username=username,
        full_name=full_name,
        password=DEMO_PASSWORD,
        roles=[role],
        party_id=party_id if role is Role.CUSTOMER else None,
        is_customer_admin=role is Role.CUSTOMER,
    )
    return identity_service.create_user(db, data, actor_id=actor_id), True


def _ensure_customer_party(db: Session, actor_id: int) -> MdParty:
    party = db.scalar(select(MdParty).where(MdParty.tax_code == DEMO_CUSTOMER_TAX_CODE))
    if party is not None:
        return party
    return masterdata_service.create_party(
        db,
        PartyCreate(
            party_type="customer",
            name="Công ty TNHH Xuất nhập khẩu Demo",
            tax_code=DEMO_CUSTOMER_TAX_CODE,
            country_code="VN",
            address="12 Trần Hưng Đạo, Hoàn Kiếm, Hà Nội",
            contact_name="Nguyễn Thị Demo",
            phone="024 3826 1234",
            email="demo@example.vn",
        ),
        actor_id,
    )


def _ensure_contract(db: Session, customer_id: int, actor_id: int) -> tuple[OpsServiceContract, bool]:
    contract = db.scalar(
        select(OpsServiceContract).where(OpsServiceContract.customer_contract_ref == DEMO_CONTRACT_REF)
    )
    if contract is not None:
        return contract, False
    year = date.today().year
    data = ContractCreate(
        customer_id=customer_id,
        customer_contract_ref=DEMO_CONTRACT_REF,
        signed_date=date(year, 1, 1),
        valid_from=date(year, 1, 1),
        valid_to=date(year + 1, 12, 31),
        payment_terms="Thanh toán trong 30 ngày kể từ ngày nhận bảng kê",
        payment_due_days=30,
        portal_enabled=True,
        service_scopes=[{"service_code": "SEA_FREIGHT"}, {"service_code": "CUSTOMS_CLEARANCE"}],
    )
    return contract_service.create_contract(db, data, actor_id), True


def _check_topics(db: Session) -> int:
    codes = set(db.scalars(select(LrnTopic.topic_code).where(LrnTopic.topic_code.in_(EXPECTED_TOPICS))).all())
    missing = [c for c in EXPECTED_TOPICS if c not in codes]
    if missing:
        raise SystemExit(f"Thiếu chủ đề {', '.join(missing)}: migration 0024 chưa chạy đúng.")
    return len(codes)


def seed() -> list[str]:
    upgrade_to_head(engine)
    lines: list[str] = []
    with SessionLocal() as db:
        admin_name, admin_full_name, admin_role = DEMO_USERS[0]
        admin, created = _ensure_user(db, admin_name, admin_full_name, admin_role, None, None)
        lines.append(f"admin: {'đã tạo' if created else 'đã có'}")
        party = _ensure_customer_party(db, admin.id)
        for username, full_name, role in DEMO_USERS[1:]:
            _, created = _ensure_user(db, username, full_name, role, party.id, admin.id)
            lines.append(f"{username}: {'đã tạo' if created else 'đã có'}")
        contract, created = _ensure_contract(db, party.id, admin.id)
        lines.append(f"hợp đồng mẫu {contract.contract_no}: {'đã tạo' if created else 'đã có'}")
        lines.append(f"chủ đề: đủ {_check_topics(db)}/7 (T01..T07)")
    return lines


def main() -> int:
    for line in seed():
        print(line)
    print("\nTài khoản demo (mật khẩu chung):")
    for username, _, role in DEMO_USERS:
        print(f"  {username:<10} / {DEMO_PASSWORD}   (vai trò: {role.value})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
