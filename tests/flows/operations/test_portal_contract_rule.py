"""Manager/customer admin chỉ tạo được tài khoản customer khi khách hàng có hợp đồng còn hiệu lực, portal_enabled
(02 §9.2; 06 §4); ràng buộc hợp đồng ↔ danh mục khách hàng. Trên PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal
from app.core.errors import AppError
from app.core.timeutil import today_vn
from app.flows.operations import service as ops_service
from app.flows.operations.models import OpsServiceContract
from app.shared.audit.models import SysAuditLog
from app.shared.identity.schemas import CustomerAccountCreate
from app.shared.masterdata import service as md_service
from app.shared.masterdata.models import MdParty

TODAY = today_vn()
NO_CONTRACT = "Khách hàng chưa có hợp đồng đang hiệu lực cho phép dùng cổng (portal_enabled)"
ACCOUNT = {"username": "minhan.lan", "full_name": "Nguyễn Thị Lan", "email": "minhan.lan@minhan.vn"}


@pytest.fixture()
def manager(client, make_user, auth_headers):
    make_user("quanly", roles=["manager"])
    make_user("quantri", roles=["admin"])
    return auth_headers("quanly")


@pytest.fixture()
def party(make_party):
    return make_party("Công ty TNHH Minh An", tax_code="0101234567")


def _create_account(client, headers, party_id, username="minhan.lan"):
    body = {**ACCOUNT, "username": username, "email": f"{username}@minhan.vn"}
    return client.post(f"/api/v2/md/parties/{party_id}/users", json=body, headers=headers)


def _rejected(r):
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert (err["code"], err["message"]) == ("VALIDATION_ERROR", NO_CONTRACT)
    assert err["details"][0]["field"] == "path.party_id"


def _set(contract_id, **values):
    with SessionLocal() as db:
        db.execute(update(OpsServiceContract).where(OpsServiceContract.id == contract_id).values(**values))
        db.commit()


# --- Điều kiện hợp đồng -------------------------------------------------------------------------------------------


def test_no_contract(client, manager, party):
    _rejected(_create_account(client, manager, party.id))


def test_draft_contract_not_enough(client, manager, party, make_contract):
    make_contract(party.id, status="draft")
    _rejected(_create_account(client, manager, party.id))


def test_portal_disabled(client, manager, party, make_contract):
    make_contract(party.id, portal_enabled=False)
    _rejected(_create_account(client, manager, party.id))


def test_not_yet_effective(client, manager, party, make_contract):
    make_contract(party.id, valid_from=TODAY + timedelta(days=1), valid_to=TODAY + timedelta(days=90))
    _rejected(_create_account(client, manager, party.id))


def test_expired(client, manager, party, make_contract):
    c = make_contract(party.id)
    _set(c.id, valid_from=TODAY - timedelta(days=90), valid_to=TODAY - timedelta(days=1))
    # Trạng thái còn ghi `active` (chưa chạy chuyển hết hạn) nhưng quá valid_to → vẫn bị từ chối: điều kiện so ngày
    with SessionLocal() as db:
        assert db.get(OpsServiceContract, c.id).status == "active"
    _rejected(_create_account(client, manager, party.id))


def test_suspended(client, manager, party, make_contract):
    c = make_contract(party.id)
    client.post(f"/api/v2/ops/service-contracts/{c.id}:suspend", json={"reason": "Khách nợ phí"}, headers=manager)
    _rejected(_create_account(client, manager, party.id))


def test_contract_of_other_customer_does_not_count(client, manager, party, make_party, make_contract):
    make_contract(make_party("Khách khác", tax_code="0109999999").id)
    _rejected(_create_account(client, manager, party.id))


def test_valid_contract_allows_account(client, manager, party, make_contract):
    make_contract(party.id, valid_to=TODAY)  # hết hạn cuối ngày hôm nay: vẫn còn hiệu lực
    r = _create_account(client, manager, party.id)
    assert r.status_code == 201, r.text
    assert r.json()["party_id"] == party.id


def test_one_valid_contract_among_others_is_enough(client, manager, party, make_contract):
    make_contract(party.id, portal_enabled=False)
    make_contract(party.id, status="draft")
    make_contract(party.id)
    assert _create_account(client, manager, party.id).status_code == 201


def test_customer_admin_invite_follows_same_rule(client, manager, party, make_contract, auth_headers, accept_invite):
    c = make_contract(party.id)
    _create_account(client, manager, party.id)
    with SessionLocal() as db:
        from app.shared.identity.models import SysUser

        db.execute(update(SysUser).where(SysUser.username == "minhan.lan").values(is_customer_admin=True))
        db.commit()
    accept_invite("minhan.lan@minhan.vn")
    headers = auth_headers("minhan.lan")
    invite = {**ACCOUNT, "username": "minhan.hung", "email": "hung@minhan.vn"}
    assert client.post("/api/v2/portal/users", json=invite, headers=headers).status_code == 201
    client.post(f"/api/v2/ops/service-contracts/{c.id}:suspend", json={"reason": "Khách nợ phí"}, headers=manager)
    mai = {**invite, "username": "minhan.mai", "email": "mai@minhan.vn"}
    _rejected(client.post("/api/v2/portal/users", json=mai, headers=headers))


def test_admin_is_not_restricted(client, manager, auth_headers, party):
    """06 §4: admin toàn quyền người dùng – tạo tài khoản customer qua /api/v2/users không cần hợp đồng."""
    body = {**ACCOUNT, "password": "MatKhau@2026", "roles": ["customer"], "party_id": party.id}
    r = client.post("/api/v2/users", json=body, headers=auth_headers("quantri"))
    assert r.status_code == 201, r.text


def test_party_type_and_status_errors_take_precedence(client, manager, make_party):
    agent = make_party("Đại lý", party_type="agent")
    r = _create_account(client, manager, agent.id)
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "body.party_id"


# --- Hợp đồng ↔ danh mục khách hàng ---------------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["draft", "active"])
def test_cannot_archive_customer_with_open_contract(client, manager, party, make_contract, status):
    make_contract(party.id, status=status)
    r = client.post(f"/api/v2/md/parties/{party.id}:archive", headers=manager)
    assert (
        r.status_code == 409 and r.json()["error"]["message"] == "Khách hàng còn hợp đồng chưa đóng; không lưu trữ được"
    )


def test_closed_contract_does_not_block_archive(client, manager, party, make_contract):
    c = make_contract(party.id)
    _set(c.id, status="closed")
    assert client.post(f"/api/v2/md/parties/{party.id}:archive", headers=manager).status_code == 200


def test_cannot_change_type_of_customer_with_contract(client, manager, party, make_contract):
    c = make_contract(party.id, status="draft")
    _set(c.id, status="closed")  # kể cả hợp đồng đã đóng
    r = client.patch(f"/api/v2/md/parties/{party.id}", json={"party_type": "agent"}, headers=manager)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


def test_cannot_delete_customer_with_contract(client, party, make_contract):
    make_contract(party.id, status="draft")
    with SessionLocal() as db, pytest.raises(IntegrityError, match="fk_ops_service_contracts_customer_id_md_parties"):
        db.execute(delete(MdParty).where(MdParty.id == party.id))
        db.commit()


# --- Song song: tạm dừng hợp đồng trong lúc đang tạo tài khoản -------------------------------------------------------


def test_suspension_and_account_creation_do_not_race(client, manager, party, make_contract):
    """Tạo tài khoản khóa FOR SHARE hợp đồng; tạm dừng khóa FOR UPDATE → mọi tài khoản được tạo đều xong trước khi
    hợp đồng bị tạm dừng (nhật ký user.create có id nhỏ hơn contract.suspend)."""
    contract = make_contract(party.id)

    def create(i):
        with SessionLocal() as db:
            data = CustomerAccountCreate(**{**ACCOUNT, "username": f"song.song{i}", "email": f"ss{i}@minhan.vn"})
            try:
                md_service.create_customer_account(db, party.id, data, actor_id=1, tasks=BackgroundTasks())
            except AppError:
                pass

    def suspend(_):
        with SessionLocal() as db:
            ops_service.suspend_contract(db, contract.id, "Tạm dừng song song", actor_id=1, expected_version=None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: (suspend if i == 3 else create)(i), range(8)))
    with SessionLocal() as db:
        suspend_id = db.scalar(select(SysAuditLog.id).where(SysAuditLog.action == "contract.suspend"))
        create_ids = db.scalars(select(SysAuditLog.id).where(SysAuditLog.action == "user.create")).all()
    assert suspend_id is not None
    assert all(i < suspend_id for i in create_ids)
