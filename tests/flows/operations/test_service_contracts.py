"""Hợp đồng dịch vụ /api/v2/ops/service-contracts (02 §3.1, §6.1, §8; 06 §4) trên PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.core.timeutil import today_vn
from app.flows.operations import service
from app.flows.operations.models import OpsServiceContract
from app.flows.operations.schemas import ContractCreate
from app.shared.audit.models import SysAuditLog
from tests.conftest import DEFAULT_RATE, DEFAULT_SCOPE, attach

URL = "/api/v2/ops/service-contracts"
TODAY = today_vn()
YEAR = TODAY.year
READERS = ["ops_staff", "manager", "business_manager", "instructor", "admin"]
NO_ACCESS = ["learner", "content_editor", "customer"]


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


@pytest.fixture()
def party(make_party):
    return make_party("Công ty TNHH Minh An", tax_code="0101234567")


@pytest.fixture()
def as_role(client, make_user, make_party, auth_headers):
    cache = {}

    def _headers(role: str) -> dict[str, str]:
        if role not in cache:
            extra = {"party_id": make_party(f"KH {role}").id} if role == "customer" else {}
            make_user(f"u.{role}", roles=[role], **extra)
            cache[role] = auth_headers(f"u.{role}")
        return cache[role]

    return _headers


def _body(party_id: int, **override) -> dict:
    return {
        "customer_id": party_id,
        "customer_contract_ref": "MA-2026/07",
        "signed_date": str(TODAY - timedelta(days=2)),
        "valid_from": str(TODAY - timedelta(days=1)),
        "valid_to": str(TODAY + timedelta(days=364)),
        "payment_due_days": 30,
        "service_scopes": [DEFAULT_SCOPE],
        "rates": [DEFAULT_RATE],
        **override,
    }


def _create(client, headers, party_id, **override) -> dict:
    r = client.post(URL, json=_body(party_id, **override), headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _actions(contract_id: int) -> list[str]:
    with SessionLocal() as db:
        q = select(SysAuditLog.action).where(
            SysAuditLog.entity_type == "service_contract", SysAuditLog.entity_id == contract_id
        )
        return list(db.scalars(q.order_by(SysAuditLog.id)))


# --- Quyền (06 §4 – L1 Hợp đồng dịch vụ) -------------------------------------------------------------------


@pytest.mark.parametrize("role", READERS)
def test_readers(client, as_role, party, role):
    c = _create(client, as_role("manager"), party.id)
    assert client.get(URL, headers=as_role(role)).status_code == 200
    assert client.get(f"{URL}/{c['id']}", headers=as_role(role)).status_code == 200


@pytest.mark.parametrize("role", NO_ACCESS)
def test_no_access(client, as_role, party, role):
    c = _create(client, as_role("manager"), party.id)
    _error(client.get(URL, headers=as_role(role)), 403, "FORBIDDEN")
    _error(client.get(f"{URL}/{c['id']}", headers=as_role(role)), 403, "FORBIDDEN")


@pytest.mark.parametrize(
    ("role", "can"),
    [("ops_staff", True), ("manager", True), ("business_manager", False), ("instructor", False), ("admin", False)],
)
def test_who_creates_and_edits_drafts(client, as_role, party, role, can):
    r = client.post(URL, json=_body(party.id), headers=as_role(role))
    if not can:
        _error(r, 403, "FORBIDDEN")
        return
    assert r.status_code == 201
    assert (
        client.patch(f"{URL}/{r.json()['id']}", json={"payment_due_days": 45}, headers=as_role(role)).status_code == 200
    )


@pytest.mark.parametrize("role", ["ops_staff", "business_manager", "admin"])
def test_only_manager_changes_status(client, as_role, party, role):
    c = _create(client, as_role("manager"), party.id)
    headers = as_role(role)
    _error(client.post(f"{URL}/{c['id']}:activate", headers=headers), 403, "FORBIDDEN")
    _error(client.post(f"{URL}/{c['id']}:suspend", json={"reason": "Tạm dừng"}, headers=headers), 403, "FORBIDDEN")
    _error(client.post(f"{URL}/{c['id']}:resume", headers=headers), 403, "FORBIDDEN")


# --- Tạo ----------------------------------------------------------------------------------------------------


def test_create_draft(client, as_role, party):
    r = client.post(URL, json=_body(party.id), headers=as_role("ops_staff"))
    body = r.json()
    assert r.status_code == 201
    assert r.headers["Location"] == f"{URL}/{body['id']}" and r.headers["ETag"] == '"1"'
    assert body["contract_no"] == f"HD-{YEAR}-0001"
    assert (body["status"], body["mode"], body["portal_enabled"]) == ("draft", "production", True)
    assert (body["customer_name"], body["customer_tax_code"]) == ("Công ty TNHH Minh An", "0101234567")
    assert _create(client, as_role("ops_staff"), party.id)["contract_no"] == f"HD-{YEAR}-0002"
    assert _actions(body["id"]) == ["contract.create"]


def test_contract_numbers_are_unique_under_concurrency(client, party):
    def make(_):
        with SessionLocal() as db:
            return service.create_contract(db, ContractCreate(**_body(party.id)), actor_id=1).contract_no

    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(make, range(20)))
    assert sorted(numbers) == [f"HD-{YEAR}-{i:04d}" for i in range(1, 21)]


def test_snapshot_is_kept_when_party_changes(client, as_role, party):
    c = _create(client, as_role("manager"), party.id)
    client.patch(f"/api/v2/md/parties/{party.id}", json={"name": "Tên mới"}, headers=as_role("manager"))
    assert client.get(f"{URL}/{c['id']}", headers=as_role("manager")).json()["customer_name"] == "Công ty TNHH Minh An"


def test_customer_must_be_active_customer(client, as_role, make_party):
    manager = as_role("manager")
    agent = make_party("Đại lý", party_type="agent")
    archived = make_party("Khách cũ")
    client.post(f"/api/v2/md/parties/{archived.id}:archive", headers=manager)
    for pid in (agent.id, archived.id, 99999):
        err = _error(client.post(URL, json=_body(pid), headers=manager), 422, "VALIDATION_ERROR")
        assert err["details"][0]["field"] == "body.customer_id"


@pytest.mark.parametrize(
    "override",
    [
        {"mode": "training"},  # Giai đoạn 3: chỉ production
        {"valid_from": str(TODAY), "valid_to": str(TODAY - timedelta(days=1))},
        {"valid_to": None},
        {"customer_id": 0},
        {"payment_due_days": -1},
        {"status": "active"},
        {"contract_no": "HD-TU-DAT"},
        {"customer_name": "Sửa bản chụp"},
    ],
)
def test_create_validation(client, as_role, party, override):
    _error(client.post(URL, json=_body(party.id, **override), headers=as_role("manager")), 422, "VALIDATION_ERROR")


# --- Sửa nháp ------------------------------------------------------------------------------------------------


def test_patch_draft(client, as_role, party, make_party):
    manager = as_role("manager")
    c = _create(client, manager, party.id)
    other = make_party("Khách khác", tax_code="0109999999")
    url = f"{URL}/{c['id']}"
    r = client.patch(
        url, json={"customer_id": other.id, "portal_enabled": False}, headers={**manager, "If-Match": '"1"'}
    )
    body = r.json()
    assert r.status_code == 200 and r.headers["ETag"] == '"2"'
    assert (body["customer_id"], body["customer_name"], body["customer_tax_code"]) == (
        other.id,
        "Khách khác",
        "0109999999",
    )
    assert body["portal_enabled"] is False and body["payment_due_days"] == 30
    _error(
        client.patch(url, json={"payment_due_days": 1}, headers={**manager, "If-Match": '"1"'}), 409, "VERSION_CONFLICT"
    )
    # Chỉ gửi một ngày: kiểm thứ tự với ngày còn lại đang lưu
    err = _error(
        client.patch(url, json={"valid_to": str(TODAY - timedelta(days=10))}, headers=manager), 422, "VALIDATION_ERROR"
    )
    assert err["details"][0]["field"] == "body.valid_to"
    for bad in ({"valid_from": None}, {"portal_enabled": None}, {"status": "active"}, {"mode": "training"}):
        _error(client.patch(url, json=bad, headers=manager), 422, "VALIDATION_ERROR")
    assert _actions(c["id"]) == ["contract.create", "contract.update"]


def test_cannot_patch_after_activation(client, as_role, party):
    manager = as_role("manager")
    c = _create(client, manager, party.id)
    attach(c["id"])
    assert client.post(f"{URL}/{c['id']}:activate", headers=manager).status_code == 200
    _error(
        client.patch(f"{URL}/{c['id']}", json={"payment_due_days": 1}, headers=manager), 409, "INVALID_STATE_TRANSITION"
    )


# --- Máy trạng thái (02 §6.1) -----------------------------------------------------------------------------------


def test_activate_requirements(client, as_role, party):
    manager = as_role("manager")
    c = _create(
        client,
        manager,
        party.id,
        signed_date=None,
        valid_from=str(TODAY - timedelta(days=60)),
        valid_to=str(TODAY - timedelta(days=1)),
    )
    err = _error(client.post(f"{URL}/{c['id']}:activate", headers=manager), 422, "VALIDATION_ERROR")
    assert {d["field"] for d in err["details"]} == {"body.signed_date", "body.valid_to", "body.attachments"}
    client.patch(f"{URL}/{c['id']}", json={"signed_date": str(TODAY), "valid_to": str(TODAY)}, headers=manager)
    attach(c["id"])
    r = client.post(f"{URL}/{c['id']}:activate", headers=manager)  # valid_to = hôm nay: còn hiệu lực
    assert r.status_code == 200 and r.json()["status"] == "active" and r.json()["activated_at"].endswith("Z")
    _error(client.post(f"{URL}/{c['id']}:activate", headers=manager), 409, "INVALID_STATE_TRANSITION")


def test_activate_requires_active_customer(client, as_role, party):
    manager = as_role("manager")
    c = _create(client, manager, party.id)
    with SessionLocal() as db:  # khách hàng bị lưu trữ sau khi lập nháp (bỏ qua quy tắc chặn – mô phỏng dữ liệu cũ)
        from app.shared.masterdata.models import MdParty

        db.execute(update(MdParty).where(MdParty.id == party.id).values(status="archived", archived_at=TODAY))
        db.commit()
    _error(client.post(f"{URL}/{c['id']}:activate", headers=manager), 422, "VALIDATION_ERROR")


def test_suspend_and_resume(client, as_role, party):
    manager = as_role("manager")
    c = _create(client, manager, party.id)
    url = f"{URL}/{c['id']}"
    _error(
        client.post(f"{url}:suspend", json={"reason": "Khách nợ phí"}, headers=manager), 409, "INVALID_STATE_TRANSITION"
    )
    attach(c["id"])
    client.post(f"{url}:activate", headers=manager)
    _error(client.post(f"{url}:suspend", json={}, headers=manager), 422, "VALIDATION_ERROR")  # bắt buộc lý do
    _error(client.post(f"{url}:suspend", json={"reason": "x"}, headers=manager), 422, "VALIDATION_ERROR")
    r = client.post(f"{url}:suspend", json={"reason": "Khách nợ phí quá hạn"}, headers={**manager, "If-Match": '"2"'})
    assert (r.json()["status"], r.json()["status_reason"]) == ("suspended", "Khách nợ phí quá hạn")
    _error(client.post(f"{url}:suspend", json={"reason": "Lần nữa"}, headers=manager), 409, "INVALID_STATE_TRANSITION")
    r = client.post(f"{url}:resume", headers=manager)
    assert (r.json()["status"], r.json()["status_reason"]) == ("active", None)
    _error(client.post(f"{url}:resume", headers=manager), 409, "INVALID_STATE_TRANSITION")
    assert _actions(c["id"]) == [
        "contract.create",
        "contract.attachment_add",
        "event.EV-101",  # sự kiện ops.contract.activated (Giai đoạn 8) – cùng giao dịch với kích hoạt
        "contract.activate",
        "contract.suspend",
        "contract.resume",
    ]


def test_auto_expire(client, as_role, party, make_contract):
    manager = as_role("manager")
    contract = make_contract(party.id)
    with SessionLocal() as db:
        db.execute(
            update(OpsServiceContract)
            .where(OpsServiceContract.id == contract.id)
            .values(valid_from=TODAY - timedelta(days=60), valid_to=TODAY - timedelta(days=1))
        )
        db.commit()
    body = client.get(f"{URL}/{contract.id}", headers=manager).json()
    assert body["status"] == "expired"
    with SessionLocal() as db:
        log = db.scalar(select(SysAuditLog).where(SysAuditLog.action == "contract.expire"))
    assert (log.entity_id, log.actor_id, log.after) == (contract.id, None, {"status": "expired"})
    _error(
        client.post(f"{URL}/{contract.id}:suspend", json={"reason": "Quá hạn"}, headers=manager),
        409,
        "INVALID_STATE_TRANSITION",
    )


def test_resume_after_valid_to_means_expired(client, as_role, party, make_contract):
    manager = as_role("manager")
    contract = make_contract(party.id)
    client.post(f"{URL}/{contract.id}:suspend", json={"reason": "Tạm dừng dài"}, headers=manager)
    with SessionLocal() as db:
        db.execute(
            update(OpsServiceContract)
            .where(OpsServiceContract.id == contract.id)
            .values(valid_from=TODAY - timedelta(days=60), valid_to=TODAY - timedelta(days=1))
        )
        db.commit()
    assert client.get(f"{URL}/{contract.id}", headers=manager).json()["status"] == "suspended"  # chỉ active tự hết hạn
    assert client.post(f"{URL}/{contract.id}:resume", headers=manager).json()["status"] == "expired"


def test_not_found(client, as_role):
    _error(client.get(f"{URL}/99999", headers=as_role("manager")), 404, "NOT_FOUND")
    _error(client.post(f"{URL}/99999:activate", headers=as_role("manager")), 404, "NOT_FOUND")


# --- Danh sách ----------------------------------------------------------------------------------------------------


def test_list_filters(client, as_role, party, make_party, make_contract):
    manager = as_role("manager")
    other = make_party("Bình Minh", tax_code="0109999999")
    a = make_contract(party.id)
    b = make_contract(other.id, portal_enabled=False, customer_contract_ref="BM-01")
    c = _create(client, manager, party.id)

    def nos(**params):
        r = client.get(URL, params={"sort": "contract_no", **params}, headers=manager)
        assert r.status_code == 200, r.text
        return [x["contract_no"] for x in r.json()["items"]]

    assert nos() == [a.contract_no, b.contract_no, c["contract_no"]]
    assert nos(status="draft") == [c["contract_no"]]
    assert nos(customer_id=other.id) == [b.contract_no]
    assert nos(portal_enabled=False) == [b.contract_no]
    assert nos(q="bm-01") == [b.contract_no]
    assert nos(q="bình minh") == [b.contract_no]
    assert nos(sort="-contract_no") == [c["contract_no"], b.contract_no, a.contract_no]
    for bad in ({"status": "deleted"}, {"sort": "payment_terms"}, {"q": ""}, {"customer_id": 0}):
        _error(client.get(URL, params=bad, headers=manager), 422, "VALIDATION_ERROR")
