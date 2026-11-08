"""Phạm vi, SLA, biểu giá theo phiên bản; phiên bản mới; thanh lý, đóng (02 §3.1, §3.8, §3.9.2, §6.1) – PostgreSQL."""

from datetime import timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.core.db import SessionLocal
from app.core.timeutil import today_vn
from app.flows.operations.models import OpsContractRate, OpsServiceContract
from app.shared.audit.models import SysAuditLog
from tests.conftest import DEFAULT_RATE, DEFAULT_SCOPE, attach

URL = "/api/v2/ops/service-contracts"
TODAY = today_vn()
SLA = {
    "sla_code": "TQ-24H",
    "description": "Thông quan ≤ 24 giờ làm việc",
    "target_value": "24",
    "unit": "hour",
    "measure_level": "per_shipment",
    "kpi_code": "KPI-02",
    "penalty_rule": {"percent": 5},
}
RATE2 = {
    **DEFAULT_RATE,
    "rate_code": "VC-20",
    "service_code": "ROAD_TRANSPORT",
    "charge_name": "Vận chuyển cont 20'",
    "basis": "per_container_20",
    "unit_price": "3200000",
    "min_charge": "1000000",
    "tax_rate": "10",
}
SCOPE2 = {"service_code": "ROAD_TRANSPORT", "params": {"route": "HPH-HN"}}


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


@pytest.fixture()
def h(client, make_user, auth_headers):
    for role in ("manager", "ops_staff", "business_manager"):
        make_user(f"u.{role}", roles=[role])
    return {r: auth_headers(f"u.{r}") for r in ("manager", "ops_staff", "business_manager")}


@pytest.fixture()
def party(make_party):
    return make_party("Công ty TNHH Minh An", tax_code="0101234567")


def _body(party_id, **o):
    return {
        "customer_id": party_id,
        "signed_date": str(TODAY - timedelta(days=5)),
        "valid_from": str(TODAY - timedelta(days=5)),
        "valid_to": str(TODAY + timedelta(days=360)),
        "service_scopes": [DEFAULT_SCOPE],
        "sla_terms": [SLA],
        "rates": [DEFAULT_RATE],
        **o,
    }


def _active(client, h, party_id, **o) -> dict:
    c = client.post(URL, json=_body(party_id, **o), headers=h["manager"]).json()
    attach(c["id"])
    r = client.post(f"{URL}/{c['id']}:activate", headers=h["manager"])
    assert r.status_code == 200, r.text
    return client.get(f"{URL}/{c['id']}", headers=h["manager"]).json()


def _amendment(contract_id) -> int:
    return attach(contract_id, kind="amendment").id


def _actions(contract_id) -> list[str]:
    with SessionLocal() as db:
        q = select(SysAuditLog.action).where(
            SysAuditLog.entity_type == "service_contract", SysAuditLog.entity_id == contract_id
        )
        return list(db.scalars(q.order_by(SysAuditLog.id)))


# --- Phạm vi, SLA, biểu giá ------------------------------------------------------------------------------------


def test_create_with_terms(client, h, party):
    r = client.post(
        URL,
        json=_body(party.id, service_scopes=[DEFAULT_SCOPE, SCOPE2], rates=[DEFAULT_RATE, RATE2]),
        headers=h["ops_staff"],
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert [s["service_code"] for s in body["service_scopes"]] == ["CUSTOMS_CLEARANCE", "ROAD_TRANSPORT"]
    assert body["service_scopes"][0]["params"] == {"port": "VNHPH"}
    sla = body["sla_terms"][0]
    assert (sla["sla_code"], sla["target_value"], sla["kpi_code"], sla["penalty_rule"]) == (
        "TQ-24H",
        "24.000",
        "KPI-02",
        {"percent": 5},
    )
    rates = {x["rate_code"]: x for x in body["rates"]}
    # Tiền trả dạng chuỗi – giữ đủ 2 chữ số thập phân, không qua float (06 §2.4)
    assert (rates["TK-01"]["unit_price"], rates["TK-01"]["tax_rate"], rates["TK-01"]["min_charge"]) == (
        "1500000.00",
        "8.00",
        None,
    )
    assert (rates["VC-20"]["unit_price"], rates["VC-20"]["min_charge"], rates["VC-20"]["currency"]) == (
        "3200000.00",
        "1000000.00",
        "VND",
    )
    with SessionLocal() as db:
        assert {r.contract_version for r in db.scalars(select(OpsContractRate))} == {1}


@pytest.mark.parametrize(
    "override",
    [
        {"service_scopes": [DEFAULT_SCOPE, DEFAULT_SCOPE]},
        {"rates": [DEFAULT_RATE, DEFAULT_RATE]},
        {"sla_terms": [SLA, SLA]},
        {"service_scopes": [{"service_code": "SPACE_TRAVEL"}]},
        {"rates": [{**DEFAULT_RATE, "basis": "per_hour"}]},
        {"rates": [{**DEFAULT_RATE, "currency": "VN"}]},
        {"rates": [{**DEFAULT_RATE, "unit_price": "-1"}]},
        {"rates": [{**DEFAULT_RATE, "unit_price": "1.234"}]},  # quá 2 chữ số thập phân
        {"rates": [{**DEFAULT_RATE, "tax_rate": "101"}]},
        {"rates": [{**DEFAULT_RATE, "valid_from": str(TODAY), "valid_to": str(TODAY - timedelta(days=1))}]},
        {"rates": [{**DEFAULT_RATE, "extra": 1}]},
        {"sla_terms": [{**SLA, "measure_level": "per_year"}]},
    ],
)
def test_terms_validation(client, h, party, override):
    _error(client.post(URL, json=_body(party.id, **override), headers=h["manager"]), 422, "VALIDATION_ERROR")


def test_patch_replaces_arrays(client, h, party):
    c = client.post(URL, json=_body(party.id), headers=h["manager"]).json()
    url = f"{URL}/{c['id']}"
    r = client.patch(url, json={"rates": [RATE2], "service_scopes": [DEFAULT_SCOPE, SCOPE2]}, headers=h["manager"])
    assert [x["rate_code"] for x in r.json()["rates"]] == ["VC-20"]  # thay toàn bộ, không gộp
    assert len(r.json()["sla_terms"]) == 1  # không gửi → giữ nguyên
    assert client.patch(url, json={"sla_terms": []}, headers=h["manager"]).json()["sla_terms"] == []
    _error(client.patch(url, json={"rates": None}, headers=h["manager"]), 422, "VALIDATION_ERROR")
    with SessionLocal() as db:
        log = db.scalars(select(SysAuditLog).where(SysAuditLog.action == "contract.update")).all()[0]
    assert [x["rate_code"] for x in log.before["rates"]] == ["TK-01"] and [
        x["rate_code"] for x in log.after["rates"]
    ] == ["VC-20"]


def test_activation_needs_scope_rate_and_consistency(client, h, party):
    c = client.post(URL, json=_body(party.id, service_scopes=[], rates=[]), headers=h["manager"]).json()
    attach(c["id"])
    err = _error(client.post(f"{URL}/{c['id']}:activate", headers=h["manager"]), 422, "VALIDATION_ERROR")
    assert {d["field"] for d in err["details"]} == {"body.service_scopes", "body.rates"}
    client.patch(f"{URL}/{c['id']}", json={"service_scopes": [DEFAULT_SCOPE], "rates": [RATE2]}, headers=h["manager"])
    err = _error(client.post(f"{URL}/{c['id']}:activate", headers=h["manager"]), 422, "VALIDATION_ERROR")
    assert "ROAD_TRANSPORT" in err["details"][0]["message"]  # biểu giá cho dịch vụ ngoài phạm vi


# --- Phiên bản ---------------------------------------------------------------------------------------------------


def test_version_one_snapshot_on_activation(client, h, party):
    c = _active(client, h, party.id)
    versions = client.get(f"{URL}/{c['id']}/versions", headers=h["business_manager"]).json()
    assert [v["contract_version"] for v in versions] == [1]
    snap = versions[0]["snapshot"]
    assert snap["contract_no"] == c["contract_no"] and snap["valid_to"] == c["valid_to"]
    assert [x["rate_code"] for x in snap["rates"]] == ["TK-01"] and snap["rates"][0]["unit_price"] == "1500000.00"
    assert "status" not in snap and "status_reason" not in snap  # bản chụp chỉ gồm điều khoản
    assert versions[0]["effective_from"] == c["valid_from"]


def test_new_version(client, h, party):
    c = _active(client, h, party.id)
    url = f"{URL}/{c['id']}"
    body = {
        "amendment_ref": "PL-01",
        "reason": "Gia hạn và bổ sung vận chuyển nội địa",
        "effective_from": str(TODAY + timedelta(days=1)),
        "attachment_id": _amendment(c["id"]),
        "valid_to": str(TODAY + timedelta(days=720)),
        "service_scopes": [DEFAULT_SCOPE, SCOPE2],
        "rates": [{**DEFAULT_RATE, "unit_price": "1600000"}, RATE2],
    }
    _error(client.post(f"{url}/versions", json=body, headers=h["ops_staff"]), 403, "FORBIDDEN")
    r = client.post(f"{url}/versions", json=body, headers={**h["manager"], "If-Match": f'"{c["row_version"]}"'})
    assert r.status_code == 201, r.text
    assert r.headers["Location"] == f"{url}/versions/2" and r.json()["contract_version"] == 2

    detail = client.get(url, headers=h["manager"]).json()
    assert (detail["contract_version"], detail["valid_to"]) == (2, body["valid_to"])
    assert {x["rate_code"]: x["unit_price"] for x in detail["rates"]} == {"TK-01": "1600000.00", "VC-20": "3200000.00"}
    assert len(detail["sla_terms"]) == 1  # không gửi → mang sang phiên bản 2
    # Phiên bản 1 giữ nguyên
    v1 = client.get(f"{url}/versions/1", headers=h["manager"]).json()
    assert {x["rate_code"]: x["unit_price"] for x in v1["snapshot"]["rates"]} == {"TK-01": "1500000.00"}
    _error(client.get(f"{url}/versions/9", headers=h["manager"]), 404, "NOT_FOUND")
    with SessionLocal() as db:
        log = db.scalar(select(SysAuditLog).where(SysAuditLog.action == "contract.new_version"))
    assert (log.before["contract_version"], log.after["contract_version"], log.after["amendment_ref"]) == (
        1,
        2,
        "PL-01",
    )
    assert log.before["valid_to"] == c["valid_to"]


def test_new_version_rules(client, h, party, make_party, make_contract):
    c = _active(client, h, party.id)
    url = f"{URL}/{c['id']}/versions"
    base = {
        "amendment_ref": "PL-01",
        "reason": "Điều chỉnh giá",
        "effective_from": str(TODAY),
        "rates": [{**DEFAULT_RATE, "unit_price": "1"}],
    }
    other = make_contract(make_party("Khác", tax_code="0109999999").id)
    signed = client.get(f"{URL}/{c['id']}/attachments", headers=h["manager"]).json()[0]["id"]  # loại signed_contract
    for att in (signed, _amendment(other.id), 999999):
        _error(client.post(url, json={**base, "attachment_id": att}, headers=h["manager"]), 422, "VALIDATION_ERROR")
    amend = _amendment(c["id"])
    _error(
        client.post(
            url,
            json={**base, "attachment_id": amend, "effective_from": str(TODAY - timedelta(days=30))},
            headers=h["manager"],
        ),
        422,
        "VALIDATION_ERROR",
    )  # trước ngày hiệu lực phiên bản 1
    _error(
        client.post(
            url, json={k: v for k, v in base.items() if k != "rates"} | {"attachment_id": amend}, headers=h["manager"]
        ),
        422,
        "VALIDATION_ERROR",
    )  # không đổi gì
    _error(
        client.post(url, json={**base, "attachment_id": amend, "rates": [RATE2]}, headers=h["manager"]),
        422,
        "VALIDATION_ERROR",
    )  # biểu giá ngoài phạm vi
    assert client.post(url, json={**base, "attachment_id": amend}, headers=h["manager"]).status_code == 201
    _error(
        client.post(url, json={**base, "attachment_id": amend}, headers=h["manager"]), 422, "VALIDATION_ERROR"
    )  # dùng lại


def test_no_version_for_draft(client, h, party):
    c = client.post(URL, json=_body(party.id), headers=h["manager"]).json()
    body = {
        "amendment_ref": "PL-01",
        "reason": "Thử nháp",
        "effective_from": str(TODAY),
        "attachment_id": 1,
        "valid_to": str(TODAY),
    }
    _error(client.post(f"{URL}/{c['id']}/versions", json=body, headers=h["manager"]), 409, "INVALID_STATE_TRANSITION")


# --- Thanh lý, đóng ------------------------------------------------------------------------------------------------


def test_liquidation_and_close(client, h, party):
    c = _active(client, h, party.id)
    url = f"{URL}/{c['id']}"
    _error(client.post(f"{url}:start-liquidation", json={}, headers=h["ops_staff"]), 403, "FORBIDDEN")
    err = _error(client.post(f"{url}:start-liquidation", json={}, headers=h["manager"]), 422, "VALIDATION_ERROR")
    assert err["details"][0]["field"] == "body.reason"  # còn hạn = thanh lý trước hạn → bắt buộc lý do
    r = client.post(f"{url}:start-liquidation", json={"reason": "Khách hàng đề nghị chấm dứt"}, headers=h["manager"])
    assert r.json()["status"] == "liquidating" and r.json()["liquidation_started_at"].endswith("Z")
    _error(client.post(f"{url}:suspend", json={"reason": "Thử"}, headers=h["manager"]), 409, "INVALID_STATE_TRANSITION")

    _error(client.post(f"{url}:close", headers=h["ops_staff"]), 403, "FORBIDDEN")
    err = _error(client.post(f"{url}:close", headers=h["manager"]), 422, "VALIDATION_ERROR")
    assert err["details"][0]["field"] == "body.attachments"
    attach(c["id"], kind="liquidation_minutes")
    r = client.post(f"{url}:close", headers=h["manager"])
    assert r.status_code == 200 and r.json()["status"] == "closed" and r.json()["closed_at"].endswith("Z")
    assert _actions(c["id"])[-3:] == ["contract.start_liquidation", "contract.attachment_add", "contract.close"]


def test_expired_contract_liquidates_without_reason(client, h, party, make_contract):
    c = make_contract(party.id)
    with SessionLocal() as db:
        db.execute(
            update(OpsServiceContract)
            .where(OpsServiceContract.id == c.id)
            .values(valid_from=TODAY - timedelta(days=90), valid_to=TODAY - timedelta(days=1))
        )
        db.commit()
    r = client.post(f"{URL}/{c.id}:start-liquidation", headers=h["manager"])
    assert r.status_code == 200 and r.json()["status"] == "liquidating"


def test_cannot_close_unless_liquidating(client, h, party):
    c = _active(client, h, party.id)
    _error(client.post(f"{URL}/{c['id']}:close", headers=h["manager"]), 409, "INVALID_STATE_TRANSITION")
    draft = client.post(URL, json=_body(party.id), headers=h["manager"]).json()
    _error(
        client.post(f"{URL}/{draft['id']}:start-liquidation", json={"reason": "Nháp"}, headers=h["manager"]),
        409,
        "INVALID_STATE_TRANSITION",
    )


def _closed(client, h, party_id) -> dict:
    c = _active(client, h, party_id)
    client.post(f"{URL}/{c['id']}:start-liquidation", json={"reason": "Hoàn thành phạm vi"}, headers=h["manager"])
    attach(c["id"], kind="liquidation_minutes")
    assert client.post(f"{URL}/{c['id']}:close", headers=h["manager"]).status_code == 200
    return c


def test_closed_contract_is_read_only_via_api(client, h, party):
    c = _closed(client, h, party.id)
    url = f"{URL}/{c['id']}"
    m = h["manager"]
    _error(client.patch(url, json={"payment_due_days": 1}, headers=m), 409, "INVALID_STATE_TRANSITION")
    for action in (":activate", ":resume", ":close"):
        _error(client.post(f"{url}{action}", headers=m), 409, "INVALID_STATE_TRANSITION")
    _error(client.post(f"{url}:suspend", json={"reason": "Thử"}, headers=m), 409, "INVALID_STATE_TRANSITION")
    _error(client.post(f"{url}:start-liquidation", json={"reason": "Thử"}, headers=m), 409, "INVALID_STATE_TRANSITION")
    files = {"file": ("a.pdf", b"%PDF-1.4 x", "application/pdf")}
    _error(
        client.post(f"{url}/attachments", files=files, data={"kind": "other"}, headers=m),
        409,
        "INVALID_STATE_TRANSITION",
    )
    body = {
        "amendment_ref": "PL-9",
        "reason": "Sau khi đóng",
        "effective_from": str(TODAY),
        "attachment_id": 1,
        "valid_to": str(TODAY),
    }
    _error(client.post(f"{url}/versions", json=body, headers=m), 409, "INVALID_STATE_TRANSITION")
    assert client.get(url, headers=h["business_manager"]).status_code == 200  # vẫn đọc được


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE ops_service_contracts SET payment_due_days = 1 WHERE id = :id",
        "UPDATE ops_service_contracts SET status = 'active' WHERE id = :id",
        "DELETE FROM ops_service_contracts WHERE id = :id",
        "UPDATE ops_contract_rates SET unit_price = 1 WHERE contract_id = :id",
        "DELETE FROM ops_contract_scopes WHERE contract_id = :id",
        "INSERT INTO ops_contract_sla (contract_id, contract_version, sla_code, description, target_value, unit, "
        "measure_level) VALUES (:id, 1, 'X', 'x', 1, 'h', 'per_shipment')",
        "DELETE FROM ops_contract_attachments WHERE contract_id = :id",
        "UPDATE ops_contract_versions SET reason = 'sua' WHERE contract_id = :id",
    ],
)
def test_closed_contract_is_read_only_in_database(client, h, party, sql):
    c = _closed(client, h, party.id)
    with SessionLocal() as db, pytest.raises(DBAPIError, match="đã đóng"):
        db.execute(text(sql), {"id": c["id"]})
        db.commit()


def test_open_contract_children_still_editable_in_database(client, h, party):
    c = _active(client, h, party.id)
    with SessionLocal() as db:
        db.execute(
            text("UPDATE ops_contract_rates SET charge_name = 'Đổi tên' WHERE contract_id = :id"), {"id": c["id"]}
        )
        db.commit()  # trigger chỉ chặn hợp đồng closed
