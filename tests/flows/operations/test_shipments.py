"""Lô hàng / vận đơn `/api/v2/ops/shipments` (Giai đoạn 8; 02 §3.2, §6.2; 06 §4) và sự kiện EV-101/111/112/142,
trên PostgreSQL."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import SessionLocal
from app.core.timeutil import VN_TZ, today_vn
from app.flows.operations import service as ops_service
from app.shared.audit.models import SysAuditLog
from tests.conftest import attach

URL = "/api/v2/ops/shipments"
ROLES = ("ops_staff", "manager", "business_manager", "instructor", "admin", "learner", "content_editor")
READERS = {"ops_staff", "manager", "business_manager", "instructor", "admin"}


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


def _iso(days: float) -> str:
    return (datetime.now(VN_TZ) + timedelta(days=days)).replace(microsecond=0).isoformat()


def _logs(entity_id: int | None = None, prefix: str = "") -> list[SysAuditLog]:
    with SessionLocal() as db:
        stmt = select(SysAuditLog).where(SysAuditLog.action.startswith(prefix)).order_by(SysAuditLog.id)
        if entity_id is not None:
            stmt = stmt.where(SysAuditLog.entity_id == entity_id)
        return list(db.scalars(stmt))


def _events(code: str, entity_id: int | None = None) -> list[dict]:
    return [log.after for log in _logs(entity_id, f"event.{code}")]


@pytest.fixture()
def world(client, make_party, make_contract, make_user, auth_headers):
    a = make_party("Công ty Minh An", tax_code="0100000001")
    b = make_party("Công ty Bình Minh", tax_code="0100000002")
    seller = make_party("Shanghai Export Co.", party_type="foreign_seller", country_code="CN")
    contract_a, contract_b = make_contract(a.id), make_contract(b.id)
    users = {}
    for role in ROLES:
        users[role] = make_user(f"u.{role}", roles=[role])
    users["ops_staff_2"] = make_user("u.ops2", roles=["ops_staff"])
    users["customer"] = make_user("kh.a", roles=["customer"], party_id=a.id, email="kh@a.vn")
    headers = {role: auth_headers(user.username) for role, user in users.items()}
    return {
        "a": a,
        "b": b,
        "seller": seller,
        "contract_a": contract_a,
        "contract_b": contract_b,
        "users": users,
        "h": headers,
    }


def _payload(world, contract="contract_a", **overrides) -> dict:
    party = world["a"] if contract == "contract_a" else world["b"]
    return {
        "contract_id": world[contract].id,
        "direction": "import",
        "transport_mode": "sea_fcl",
        "incoterm": "cif",
        "incoterm_place": "Hải Phòng",
        "shipper_id": world["seller"].id,
        "consignee_id": party.id,
        "origin_location": "CNSHA – Thượng Hải",
        "destination_location": "VNHPH – Hải Phòng",
        "cargo_summary": {
            "packages": 120,
            "gross_weight_kg": 18500,
            "containers": [{"no": "MSKU1234567", "type": "40HC"}],
        },
        "etd": _iso(3),
        "eta": _iso(10),
        "required_delivery_at": _iso(14),
        **overrides,
    }


def _create(client, world, **overrides) -> dict:
    r = client.post(URL, json=_payload(world, **overrides), headers=world["h"]["ops_staff"])
    assert r.status_code == 201, r.text
    return r.json()


def pass_document_check(client, headers, shipment_id) -> dict:
    """BR-103 (Giai đoạn 16): tải một chứng từ + ghi kết quả kiểm tra đạt – để chuyển docs_verified được."""
    from tests.conftest import pdf_bytes

    r = client.post(
        f"/api/v2/ops/shipments/{shipment_id}/documents",
        files={"file": ("bl.pdf", pdf_bytes("B/L"), "application/pdf")},
        data={"doc_type": "bill_of_lading"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    check = r.json()["check"]
    done = client.post(
        f"/api/v2/ops/document-checks/{check['id']}:complete",
        json={"items": [{"doc_type": "bill_of_lading", "outcome": "ok"}]},
        headers=headers,
    )
    assert done.status_code == 200, done.text
    return done.json()


def _act(client, world, shipment_id, action, json=None, who="ops_staff"):
    if action == "verify-documents" and who == "ops_staff":
        status = client.get(f"{URL}/{shipment_id}", headers=world["h"][who]).json().get("status")
        if status == "docs_collecting":
            pass_document_check(client, world["h"][who], shipment_id)
    return client.post(f"{URL}/{shipment_id}:{action}", json=json, headers=world["h"][who])


DELIVERY = {"receiver_name": "Kho Minh An", "in_full": True, "damage_free": False, "remarks": "Móp 1 kiện"}


def _deliver_body(hours_ago: float = 1) -> dict:
    return {**DELIVERY, "delivered_at": _iso(-hours_ago / 24)}


# --- Tạo ----------------------------------------------------------------------------------------------------------


def test_create_shipment_draft_with_number_audit_and_ev111(client, world):
    r = client.post(URL, json=_payload(world), headers=world["h"]["ops_staff"])
    assert r.status_code == 201, r.text
    s = r.json()
    year = today_vn().year
    assert s["shipment_no"] == f"LH-{year}-000001"
    assert r.headers["Location"] == f"{URL}/{s['id']}" and r.headers["ETag"] == '"1"'
    staff = world["users"]["ops_staff"]
    assert (s["status"], s["assignee_id"], s["created_by"]) == ("draft", staff.id, staff.id)
    assert (s["contract_id"], s["contract_version"], s["customer_id"]) == (world["contract_a"].id, 1, world["a"].id)
    assert (s["incoterm"], s["shipper_name"], s["consignee_name"]) == ("CIF", "Shanghai Export Co.", "Công ty Minh An")
    assert s["mode"] == "production" and s["priority"] == "normal"
    assert [(h["from_status"], h["to_status"]) for h in s["history"]] == [(None, "draft")]
    assert _create(client, world)["shipment_no"] == f"LH-{year}-000002"

    actions = [log.action for log in _logs(s["id"]) if log.entity_type == "shipment"]
    assert actions == ["shipment.create", "event.EV-111"]
    [create_log] = [log for log in _logs(s["id"], "shipment.create")]
    assert create_log.after["shipment_no"] == s["shipment_no"] and create_log.actor_id == staff.id
    [event] = _events("EV-111", s["id"])
    assert uuid.UUID(event["event_id"]) and event["occurred_at"].endswith("Z")
    assert {k: event[k] for k in ("event_code", "event_name", "schema_version", "mode", "actor_id")} == {
        "event_code": "EV-111",
        "event_name": "ops.shipment.created",
        "schema_version": 1,
        "mode": "production",
        "actor_id": staff.id,
    }
    assert event["data"] == {
        "shipment_id": s["id"],
        "shipment_no": s["shipment_no"],
        "contract_id": world["contract_a"].id,
        "customer_id": world["a"].id,
        "process_code": None,
        "process_version": None,
        "planned_tasks": [],
        "scenario_assignment_id": None,
        "learner_id": None,
        # Giai đoạn 11 (thêm trường, giữ schema_version 1): Luồng 2 chọn quy trình mẫu
        "direction": "import",
        "transport_mode": "sea_fcl",
        "required_delivery_at": s["required_delivery_at"],
        "service_codes": ["CUSTOMS_CLEARANCE"],
    }


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"incoterm": None}, "body"),  # nhập khẩu bắt buộc Incoterms
        ({"incoterm": "XYZ"}, "body.incoterm"),
        ({"etd": _iso(10), "eta": _iso(3)}, "body"),
        ({"required_delivery_at": "2026-12-01T08:00:00"}, "body"),  # thiếu múi giờ
        ({"direction": "sideways"}, "body.direction"),
        ({"shipper_id": 999999}, "body.shipper_id"),
        ({"contract_id": 999999}, "body.contract_id"),
        ({"origin_location": "  "}, "body.origin_location"),
        ({"unknown": 1}, "body.unknown"),
    ],
)
def test_create_validation(client, world, overrides, field):
    err = _error(
        client.post(URL, json=_payload(world, **overrides), headers=world["h"]["ops_staff"]), 422, "VALIDATION_ERROR"
    )
    assert any(d["field"].startswith(field) for d in err["details"]), err


def test_domestic_shipment_without_incoterm(client, world):
    s = _create(client, world, direction="domestic", transport_mode="road", incoterm=None, incoterm_place=None)
    assert s["incoterm"] is None


def test_create_requires_active_contract(client, world, make_contract, make_party):
    draft = make_contract(world["a"].id, status="draft")
    err = _error(
        client.post(URL, json=_payload(world, contract_id=draft.id), headers=world["h"]["ops_staff"]),
        422,
        "VALIDATION_ERROR",
    )
    assert err["details"][0]["field"] == "body.contract_id" and "draft" in err["message"]
    with SessionLocal() as db:
        ops_service.suspend_contract(db, world["contract_a"].id, "Khách nợ phí", actor_id=0, expected_version=None)
    _error(client.post(URL, json=_payload(world), headers=world["h"]["ops_staff"]), 422, "VALIDATION_ERROR")
    assert _logs(prefix="event.EV-111") == []  # không tạo được thì không có sự kiện


def test_archived_party_rejected(client, world, make_party):
    from app.shared.masterdata import service as md_service

    other = make_party("Đại lý cũ", party_type="agent")
    with SessionLocal() as db:
        md_service.archive_party(db, other.id, actor_id=0, expected_version=None)
    err = _error(
        client.post(URL, json=_payload(world, notify_party_id=other.id), headers=world["h"]["ops_staff"]),
        422,
        "VALIDATION_ERROR",
    )
    assert err["details"][0]["field"] == "body.notify_party_id"


def test_concurrent_creation_gets_unique_numbers(client, world):
    from app.flows.operations import shipment_service
    from app.flows.operations.shipment_schemas import ShipmentCreate

    data = ShipmentCreate(**_payload(world))
    staff_id = world["users"]["ops_staff"].id

    def create(_):
        with SessionLocal() as db:
            from app.shared.identity.models import SysUser

            return shipment_service.create_shipment(db, data, db.get(SysUser, staff_id)).shipment_no

    with ThreadPoolExecutor(max_workers=10) as pool:
        numbers = list(pool.map(create, range(10)))
    year = today_vn().year
    assert sorted(numbers) == [f"LH-{year}-{i:06d}" for i in range(1, 11)]
    assert len(_events("EV-111")) == 10


# --- Phân quyền (06 §4) -------------------------------------------------------------------------------------------


def test_rbac(client, world):
    s = _create(client, world)
    for role in (*ROLES, "customer"):
        h = world["h"][role]
        expected_create = 201 if role == "ops_staff" else 403
        assert client.post(URL, json=_payload(world), headers=h).status_code == expected_create, role
        expected_read = 200 if role in READERS else 403
        assert client.get(URL, headers=h).status_code == expected_read, role
        assert client.get(f"{URL}/{s['id']}", headers=h).status_code == expected_read, role
    # Ghi lô: chỉ ops_staff được phân công
    for who in ("ops_staff_2", "manager", "admin", "business_manager"):
        assert client.patch(f"{URL}/{s['id']}", json={"priority": "high"}, headers=world["h"][who]).status_code == 403
        assert _act(client, world, s["id"], "submit", who=who).status_code == 403
    err = _error(_act(client, world, s["id"], "submit", who="ops_staff_2"), 403, "FORBIDDEN")
    assert err["message"] == "Lô hàng không được phân công cho bạn"
    assert client.get(URL, headers={}).status_code == 401


# --- Sửa nháp -----------------------------------------------------------------------------------------------------


def test_patch_draft(client, world, make_party):
    s = _create(client, world)
    other = make_party("Ningbo Trading", party_type="foreign_seller", country_code="CN")
    h = world["h"]["ops_staff"]
    r = client.patch(
        f"{URL}/{s['id']}",
        json={"shipper_id": other.id, "priority": "urgent", "eta": _iso(11)},
        headers={**h, "If-Match": '"1"'},
    )
    assert r.status_code == 200, r.text
    assert (r.json()["shipper_name"], r.json()["priority"], r.headers["ETag"]) == ("Ningbo Trading", "urgent", '"2"')
    [log] = _logs(s["id"], "shipment.update")
    assert log.before["shipper_id"] == world["seller"].id and log.after["shipper_id"] == other.id
    assert set(log.after) == {"shipper_id", "priority", "eta"}
    _error(
        client.patch(f"{URL}/{s['id']}", json={"priority": "high"}, headers={**h, "If-Match": '"1"'}),
        409,
        "VERSION_CONFLICT",
    )
    _error(client.patch(f"{URL}/{s['id']}", json={"direction": None}, headers=h), 422, "VALIDATION_ERROR")
    _error(client.patch(f"{URL}/{s['id']}", json={"incoterm": None}, headers=h), 422, "VALIDATION_ERROR")  # vẫn import
    _error(client.patch(f"{URL}/{s['id']}", json={"etd": _iso(20)}, headers=h), 422, "VALIDATION_ERROR")  # etd > eta
    _error(client.patch(f"{URL}/{s['id']}", json={"contract_id": 1}, headers=h), 422, "VALIDATION_ERROR")
    assert _act(client, world, s["id"], "submit").status_code == 200
    err = _error(
        client.patch(f"{URL}/{s['id']}", json={"priority": "high"}, headers=h), 409, "INVALID_STATE_TRANSITION"
    )
    assert "nháp" in err["message"]


# --- Quy trình trạng thái (02 §6.2) + sự kiện ---------------------------------------------------------------------


def test_full_lifecycle_with_events(client, world):
    s = _create(client, world)
    sid = s["id"]
    steps = [
        ("submit", None, "docs_collecting"),
        ("verify-documents", {"note": "Đủ bộ chứng từ"}, "docs_verified"),
        ("start", None, "in_execution"),
        ("deliver", _deliver_body(), "delivered"),
        ("close", None, "closed"),
    ]
    for action, body, expected in steps:
        r = _act(client, world, sid, action, body)
        assert r.status_code == 200, (action, r.text)
        assert r.json()["status"] == expected
    detail = client.get(f"{URL}/{sid}", headers=world["h"]["manager"]).json()
    assert [h["to_status"] for h in detail["history"]] == [
        "draft",
        "docs_collecting",
        "docs_verified",
        "in_execution",
        "delivered",
        "closed",
    ]
    assert detail["history"][2]["note"] == "Đủ bộ chứng từ"
    assert detail["delivery"]["receiver_name"] == "Kho Minh An" and detail["delivery"]["damage_free"] is False
    assert detail["delivered_at"] and detail["closed_at"]

    changes = [(e["data"]["from"], e["data"]["to"]) for e in _events("EV-112", sid)]
    assert changes == [
        ("draft", "docs_collecting"),
        ("docs_collecting", "docs_verified"),
        ("docs_verified", "in_execution"),
        ("in_execution", "delivered"),
        ("delivered", "closed"),
    ]
    [delivered] = _events("EV-142", sid)
    assert delivered["event_name"] == "ops.shipment.delivered"
    assert delivered["data"]["in_full"] is True and delivered["data"]["damage_free"] is False
    assert delivered["data"]["sla_due_at"] == detail["required_delivery_at"]
    assert delivered["data"]["delivered_at"] == detail["delivered_at"]
    audit_actions = [
        log.action for log in _logs(sid) if log.entity_type == "shipment" and not log.action.startswith("event.")
    ]
    assert audit_actions == [
        "shipment.create",
        "shipment.submit",
        "shipment.verify_documents",
        "shipment.start",
        "shipment.deliver",
        "shipment.close",
    ]
    # Lô đã đóng: chỉ đọc
    for action in ("submit", "start", "close"):
        _error(_act(client, world, sid, action), 409, "INVALID_STATE_TRANSITION")
    _error(_act(client, world, sid, "cancel", {"reason": "Thử hủy"}), 409, "INVALID_STATE_TRANSITION")


@pytest.mark.parametrize(
    ("path", "action", "body"),
    [
        ([], "start", None),
        ([], "deliver", None),
        ([], "close", None),
        (["submit"], "submit", None),
        (["submit"], "deliver", None),
        (["submit", "verify-documents"], "close", None),
    ],
)
def test_invalid_transitions(client, world, path, action, body):
    s = _create(client, world)
    for step in path:
        assert _act(client, world, s["id"], step).status_code == 200
    payload = _deliver_body() if action == "deliver" else body
    _error(_act(client, world, s["id"], action, payload), 409, "INVALID_STATE_TRANSITION")


def test_cancel(client, world):
    s = _create(client, world)
    for step in ("submit", "verify-documents", "start"):
        _act(client, world, s["id"], step)
    _error(_act(client, world, s["id"], "cancel", {}), 422, "VALIDATION_ERROR")  # bắt buộc lý do
    _error(_act(client, world, s["id"], "cancel", {"reason": "x"}), 422, "VALIDATION_ERROR")
    r = _act(client, world, s["id"], "cancel", {"reason": "Khách hủy đơn hàng"})
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["status_reason"]) == ("cancelled", "Khách hủy đơn hàng") and body["cancelled_at"]
    assert body["history"][-1]["note"] == "Khách hủy đơn hàng"
    assert _events("EV-112", s["id"])[-1]["data"] == {"shipment_id": s["id"], "from": "in_execution", "to": "cancelled"}
    _error(_act(client, world, s["id"], "submit"), 409, "INVALID_STATE_TRANSITION")
    # Sau delivered không hủy được
    s2 = _create(client, world)
    for step, b in (("submit", None), ("verify-documents", None), ("start", None), ("deliver", _deliver_body())):
        assert _act(client, world, s2["id"], step, b).status_code == 200
    _error(_act(client, world, s2["id"], "cancel", {"reason": "Muộn rồi"}), 409, "INVALID_STATE_TRANSITION")


def test_deliver_validation(client, world):
    s = _create(client, world)
    for step in ("submit", "verify-documents", "start"):
        _act(client, world, s["id"], step)
    _error(_act(client, world, s["id"], "deliver", {**DELIVERY, "delivered_at": _iso(1)}), 422, "VALIDATION_ERROR")
    _error(_act(client, world, s["id"], "deliver", {"delivered_at": _iso(-0.1)}), 422, "VALIDATION_ERROR")
    assert _events("EV-142", s["id"]) == []


def test_submit_requires_active_contract(client, world):
    s = _create(client, world)
    with SessionLocal() as db:
        ops_service.suspend_contract(db, world["contract_a"].id, "Khách nợ phí", actor_id=0, expected_version=None)
    _error(_act(client, world, s["id"], "submit"), 422, "VALIDATION_ERROR")
    with SessionLocal() as db:
        ops_service.resume_contract(db, world["contract_a"].id, actor_id=0, expected_version=None)
    assert _act(client, world, s["id"], "submit").json()["status"] == "docs_collecting"


def test_if_match_on_transition(client, world):
    s = _create(client, world)
    h = {**world["h"]["ops_staff"], "If-Match": '"7"'}
    _error(client.post(f"{URL}/{s['id']}:submit", headers=h), 409, "VERSION_CONFLICT")
    assert client.post(f"{URL}/{s['id']}:submit", headers={**h, "If-Match": '"1"'}).status_code == 200


# --- Phân công lại ------------------------------------------------------------------------------------------------


def test_assign(client, world):
    s = _create(client, world)
    staff2 = world["users"]["ops_staff_2"]
    _error(
        client.post(f"{URL}/{s['id']}:assign", json={"assignee_id": staff2.id}, headers=world["h"]["ops_staff"]),
        403,
        "FORBIDDEN",
    )
    _error(
        client.post(
            f"{URL}/{s['id']}:assign",
            json={"assignee_id": world["users"]["learner"].id},
            headers=world["h"]["manager"],
        ),
        422,
        "VALIDATION_ERROR",
    )
    r = client.post(f"{URL}/{s['id']}:assign", json={"assignee_id": staff2.id}, headers=world["h"]["manager"])
    assert r.status_code == 200 and r.json()["assignee_id"] == staff2.id
    [log] = _logs(s["id"], "shipment.assign")
    assert log.after == {"assignee_id": staff2.id} and log.actor_id == world["users"]["manager"].id
    _error(_act(client, world, s["id"], "submit"), 403, "FORBIDDEN")  # người cũ hết quyền ghi
    assert _act(client, world, s["id"], "submit", who="ops_staff_2").status_code == 200
    listed = client.get(URL, params={"assignee_id": staff2.id}, headers=world["h"]["manager"]).json()
    assert [i["id"] for i in listed["items"]] == [s["id"]]


# --- Danh sách ----------------------------------------------------------------------------------------------------


def test_list_filters(client, world):
    a1 = _create(client, world)
    b1 = _create(client, world, contract="contract_b")
    _act(client, world, b1["id"], "submit")
    h = world["h"]["business_manager"]

    def ids(**params):
        r = client.get(URL, params=params, headers=h)
        assert r.status_code == 200, r.text
        return [i["id"] for i in r.json()["items"]]

    assert set(ids()) == {a1["id"], b1["id"]}
    assert ids(contract_id=world["contract_b"].id) == [b1["id"]]
    assert ids(status="docs_collecting") == [b1["id"]]
    assert ids(q=a1["shipment_no"]) == [a1["id"]]
    today = today_vn().isoformat()
    assert set(ids(**{"from": today, "to": today})) == {a1["id"], b1["id"]}
    assert ids(**{"from": (today_vn() + timedelta(days=1)).isoformat()}) == []
    assert ids(sort="shipment_no") == [a1["id"], b1["id"]]
    _error(client.get(URL, params={"status": "lost"}, headers=h), 422, "VALIDATION_ERROR")


# --- Ràng buộc CSDL, hợp đồng -------------------------------------------------------------------------------------


def test_database_guards(client, world):
    s = _create(client, world)
    _act(client, world, s["id"], "cancel", {"reason": "Khách hủy"})
    statements = [
        f"UPDATE ops_shipments SET priority = 'high' WHERE id = {s['id']}",  # đã hủy: chỉ đọc
        f"DELETE FROM ops_shipments WHERE id = {s['id']}",
        f"UPDATE ops_shipment_status_history SET note = 'sửa' WHERE shipment_id = {s['id']}",
        f"DELETE FROM ops_shipment_status_history WHERE shipment_id = {s['id']}",
    ]
    for sql in statements:
        with SessionLocal() as db, pytest.raises(DBAPIError, match="RestrictViolation"):
            db.execute(text(sql))
            db.commit()
    open_one = _create(client, world)
    with SessionLocal() as db, pytest.raises(DBAPIError):
        db.execute(text(f"DELETE FROM ops_shipments WHERE id = {open_one['id']}"))  # không xóa cứng
        db.commit()


def test_contract_close_requires_closed_shipments(client, world, make_party, make_contract):
    from app.flows.operations.models import AttachmentKind

    s = _create(client, world)
    cid = world["contract_a"].id
    with SessionLocal() as db:
        ops_service.start_liquidation(db, cid, "Kết thúc hợp tác", actor_id=0, expected_version=None)
    attach(cid, kind=AttachmentKind.LIQUIDATION_MINUTES.value)
    _error(client.post(URL, json=_payload(world), headers=world["h"]["ops_staff"]), 422, "VALIDATION_ERROR")
    err = _error(
        client.post(f"/api/v2/ops/service-contracts/{cid}:close", headers=world["h"]["manager"]),
        422,
        "VALIDATION_ERROR",
    )
    assert any(d["field"] == "body.shipments" for d in err["details"])
    _act(client, world, s["id"], "cancel", {"reason": "Hợp đồng thanh lý"})
    r = client.post(f"/api/v2/ops/service-contracts/{cid}:close", headers=world["h"]["manager"])
    assert r.status_code == 200 and r.json()["status"] == "closed"


def test_concurrent_suspend_and_create_do_not_race(client, world):
    """Tạo lô khóa FOR SHARE hợp đồng; tạm dừng khóa FOR UPDATE → mọi lô được tạo đều xong trước khi hợp đồng bị tạm
    dừng (nhật ký shipment.create có id nhỏ hơn contract.suspend)."""
    from app.core.errors import AppError
    from app.flows.operations import shipment_service
    from app.flows.operations.shipment_schemas import ShipmentCreate
    from app.shared.identity.models import SysUser

    data = ShipmentCreate(**_payload(world))
    staff_id, cid = world["users"]["ops_staff"].id, world["contract_a"].id

    def create(_):
        with SessionLocal() as db:
            try:
                shipment_service.create_shipment(db, data, db.get(SysUser, staff_id))
            except AppError:
                pass

    def suspend(_):
        with SessionLocal() as db:
            ops_service.suspend_contract(db, cid, "Tạm dừng song song", actor_id=0, expected_version=None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: (suspend if i == 3 else create)(i), range(8)))
    [suspend_log] = _logs(cid, "contract.suspend")
    assert all(log.id < suspend_log.id for log in _logs(prefix="shipment.create"))


def test_event_is_atomic_with_change(client, world, monkeypatch):
    """Lỗi sau khi đã ghi sự kiện (trước commit): không có lô, không có sự kiện – cùng một giao dịch."""
    from app.flows.operations import shipment_service

    real = shipment_service.publisher.publish

    def publish_then_fail(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("lỗi sau khi phát sự kiện")

    monkeypatch.setattr(shipment_service.publisher, "publish", publish_then_fail)
    r = client.post(URL, json=_payload(world), headers=world["h"]["ops_staff"])
    _error(r, 500, "INTERNAL_ERROR")
    monkeypatch.undo()
    assert client.get(URL, headers=world["h"]["manager"]).json()["total"] == 0
    assert _events("EV-111") == []


# --- EV-101 -------------------------------------------------------------------------------------------------------


def test_contract_activation_publishes_ev101(client, world):
    contract = world["contract_a"]
    [event] = _events("EV-101", contract.id)
    assert (event["event_name"], event["mode"], event["schema_version"]) == ("ops.contract.activated", "production", 1)
    assert event["data"] == {
        "contract_id": contract.id,
        "contract_no": contract.contract_no,
        "customer_id": world["a"].id,
        "service_scope": [{"service_code": "CUSTOMS_CLEARANCE", "params": {"port": "VNHPH"}}],
        "sla": {},
        "valid_from": contract.valid_from.isoformat(),
        "valid_to": contract.valid_to.isoformat(),
    }
    [log] = _logs(contract.id, "event.EV-101")
    assert log.entity_type == "service_contract"


def test_ev101_includes_sla(client, make_party, make_contract):
    party = make_party("Khách SLA", tax_code="0100000009")
    sla = {
        "sla_code": "CLR-24H",
        "description": "Thông quan ≤ 24 giờ",
        "target_value": "24",
        "unit": "hour",
        "kpi_code": "KPI-CLR",
        "measure_level": "per_shipment",
    }
    contract = make_contract(party.id, sla_terms=[sla])
    [event] = _events("EV-101", contract.id)
    assert event["data"]["sla"] == {
        "CLR-24H": {
            "description": "Thông quan ≤ 24 giờ",
            "target_value": "24.000",
            "unit": "hour",
            "kpi_code": "KPI-CLR",
            "measure_level": "per_shipment",
        }
    }
