# ruff: noqa: F401, F811  (fixture world nhập từ module test khác)
"""Giai đoạn 20 – sổ thu – chi (02 §3.10), tạm ứng nhân viên, bảng kê đối soát định kỳ (SOA) – PostgreSQL thật."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.db import SessionLocal
from app.core.timeutil import today_vn
from app.flows.control import advance_alerts
from app.flows.control.models import CtlAlert
from app.shared.audit.models import SysAuditLog
from app.shared.events import consumer
from app.shared.events.models import SysEvent
from tests.conftest import pdf_bytes
from tests.flows.operations.test_shipments import _error, world

V = "/api/v2/ops/cash-vouchers"
A = "/api/v2/ops/staff-advances"
S = "/api/v2/ops/periodic-statements"
P = "/api/v2/portal/statements"
INV = "/api/v2/ops/commercial-invoices"
TODAY = today_vn()
YEAR = TODAY.year


def _month_first(months_back: int) -> date:
    d = TODAY.replace(day=1)
    for _ in range(months_back):
        d = (d - timedelta(days=1)).replace(day=1)
    return d


M1, M2 = _month_first(1), _month_first(2)  # tháng trước, hai tháng trước


def _logs(prefix: str, entity_id: int | None = None) -> list[SysAuditLog]:
    with SessionLocal() as db:
        stmt = select(SysAuditLog).where(SysAuditLog.action.like(f"{prefix}%")).order_by(SysAuditLog.id)
        if entity_id is not None:
            stmt = stmt.where(SysAuditLog.entity_id == entity_id)
        return list(db.scalars(stmt))


def _events(code: str) -> list[dict]:
    with SessionLocal() as db:
        return [
            e.payload for e in db.scalars(select(SysEvent).where(SysEvent.event_code == code).order_by(SysEvent.id))
        ]


def _voucher(world, **kw) -> dict:
    return {
        "voucher_type": "receipt",
        "contract_id": world["contract_a"].id,
        "category_code": "T01",
        "counterparty_type": "customer",
        "counterparty_id": world["a"].id,
        "counterparty_name": "Công ty Minh An",
        "description": "Thu phí dịch vụ thông quan tháng này",
        "voucher_date": str(TODAY),
        "amount": "5000000",
        "payment_method": "bank_transfer",
        **kw,
    }


def _upload(client, url: str, headers) -> int:
    r = client.post(
        f"{url}/files", files={"file": ("ct.pdf", pdf_bytes("chung tu"), "application/pdf")}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _recorded(client, world, who="ops_staff", **kw) -> dict:
    """Phiếu đi hết đường: nháp → chứng từ → gửi → (duyệt) → ghi sổ."""
    h = world["h"]
    v = client.post(V, json=_voucher(world, **kw), headers=h[who])
    assert v.status_code == 201, v.text
    v = v.json()
    _upload(client, f"{V}/{v['id']}", h[who])
    assert client.post(f"{V}/{v['id']}:submit", headers=h[who]).status_code == 200
    if v["voucher_type"] == "payment":
        assert client.post(f"{V}/{v['id']}:approve", headers=h["business_manager"]).status_code == 200
    r = client.post(f"{V}/{v['id']}:record", headers=h["manager"])
    assert r.status_code == 200, r.text
    return r.json()


def _balance(client, world, contract_id=None) -> dict:
    q = f"?contract_id={contract_id}" if contract_id else ""
    return client.get(f"/api/v2/ops/cashbook/balance{q}", headers=world["h"]["manager"]).json()


# --- Khoản mục --------------------------------------------------------------------------------------------------


def test_categories_seed_and_subcategory(client, world):
    h = world["h"]
    cats = {c["code"]: c for c in client.get("/api/v2/ops/cashbook-categories", headers=h["ops_staff"]).json()}
    assert {f"T0{i}" for i in range(1, 6)} | {f"C0{i}" for i in range(1, 9)} == set(cats)
    assert (cats["C03"]["direction"], cats["C03"]["result_class"]) == ("payment", "direct_cost")
    body = {"code": "C03.1", "name": "Chi nâng hạ container"}
    _error(client.post("/api/v2/ops/cashbook-categories", json=body, headers=h["ops_staff"]), 403, "FORBIDDEN")
    r = client.post("/api/v2/ops/cashbook-categories", json=body, headers=h["manager"])
    assert r.status_code == 201 and (r.json()["parent_code"], r.json()["direction"]) == ("C03", "payment")
    _error(client.post("/api/v2/ops/cashbook-categories", json=body, headers=h["manager"]), 409, "DUPLICATE")
    bad = {"code": "C08.1", "name": "Không cho tạm ứng"}
    _error(client.post("/api/v2/ops/cashbook-categories", json=bad, headers=h["manager"]), 422, "VALIDATION_ERROR")
    _error(client.delete("/api/v2/ops/cashbook-categories/C03", headers=h["manager"]), 409, "INVALID_STATE_TRANSITION")
    _recorded(client, world, voucher_type="payment", category_code="C03.1", counterparty_type="supplier",
              counterparty_id=None, counterparty_name="Cảng Hải Phòng", amount="300000")  # fmt: skip
    _error(
        client.delete("/api/v2/ops/cashbook-categories/C03.1", headers=h["manager"]), 409, "INVALID_STATE_TRANSITION"
    )
    client.post("/api/v2/ops/cashbook-categories", json={"code": "C03.2", "name": "Lưu bãi"}, headers=h["manager"])
    assert client.delete("/api/v2/ops/cashbook-categories/C03.2", headers=h["manager"]).status_code == 204


# --- Phiếu thu / chi, số dư ---------------------------------------------------------------------------------------


def test_voucher_flow_balance_and_rules(client, world):
    h, cid = world["h"], world["contract_a"].id
    for who in ("business_manager", "customer", "admin", "instructor"):
        _error(client.post(V, json=_voucher(world), headers=h[who]), 403, "FORBIDDEN")
    for bad in (
        {"amount": "0"},
        {"voucher_date": str(TODAY + timedelta(days=1))},
        {"category_code": "C02"},  # phiếu thu dùng khoản mục chi
        {"category_code": "T05"},  # chỉ sinh từ tạm ứng
        {"voucher_type": "payment", "category_code": "C08"},
        {"payment_method": "offset"},
        {"category_code": "X99"},
        {"currency": "VND", "exchange_rate": "25000"},
    ):
        _error(client.post(V, json=_voucher(world, **bad), headers=h["ops_staff"]), 422, "VALIDATION_ERROR")

    v = client.post(V, json=_voucher(world), headers=h["ops_staff"]).json()
    assert (v["voucher_no"], v["status"], v["amount_base"]) == (f"PT-{YEAR}-00001", "draft", "5000000.00")
    url = f"{V}/{v['id']}"
    _error(client.post(f"{url}:submit", headers=h["ops_staff"]), 422, "VALIDATION_ERROR")  # chưa có chứng từ
    fid = _upload(client, url, h["ops_staff"])
    _error(client.post(f"{url}:submit", headers=h["manager"]), 403, "FORBIDDEN")  # không phải người lập
    assert client.post(f"{url}:submit", headers=h["ops_staff"]).json()["supporting_file_ids"] == [fid]
    assert _balance(client, world)["balance"] == "0.00"  # chưa ghi sổ → chưa tính
    _error(client.post(f"{url}:approve", headers=h["manager"]), 409, "INVALID_STATE_TRANSITION")  # phiếu thu
    _error(client.post(f"{url}:record", headers=h["ops_staff"]), 403, "FORBIDDEN")
    assert client.post(f"{url}:record", headers=h["manager"]).json()["status"] == "recorded"

    # Phiếu chi: phải duyệt trước khi ghi sổ (BR-114), người duyệt khác người lập
    pay = client.post(
        V,
        json=_voucher(
            world,
            voucher_type="payment",
            category_code="C02",
            counterparty_type="supplier",
            counterparty_id=None,
            counterparty_name="Vận tải Bắc Nam",
            amount="2000000",
            payment_method="cash",
        ),  # fmt: skip
        headers=h["manager"],
    ).json()
    assert pay["voucher_no"] == f"PC-{YEAR}-00001"
    purl = f"{V}/{pay['id']}"
    _upload(client, purl, h["manager"])
    client.post(f"{purl}:submit", headers=h["manager"])
    _error(client.post(f"{purl}:record", headers=h["business_manager"]), 409, "INVALID_STATE_TRANSITION")
    _error(client.post(f"{purl}:approve", headers=h["manager"]), 403, "FORBIDDEN")  # tự duyệt
    r = client.post(f"{purl}:reject", json={"reason": "Thiếu hóa đơn VAT"}, headers=h["business_manager"])
    assert (r.json()["status"], r.json()["decision_note"]) == ("draft", "Thiếu hóa đơn VAT")
    client.post(f"{purl}:submit", headers=h["manager"])
    assert client.post(f"{purl}:approve", headers=h["business_manager"]).json()["status"] == "approved"
    assert client.post(f"{purl}:record", headers=h["business_manager"]).json()["status"] == "recorded"

    b = _balance(client, world, cid)
    assert (b["receipts"], b["payments"], b["balance"]) == ("5000000.00", "2000000.00", "3000000.00")
    assert b["by_method"]["cash"]["balance"] == "-2000000.00" and b["by_method"]["bank_transfer"]["balance"] == (
        "5000000.00"
    )
    assert _balance(client, world, world["contract_b"].id)["balance"] == "0.00"

    # BR-112: phiếu đã ghi sổ không sửa / xóa; sai → hủy kèm lý do + phiếu thay thế
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("UPDATE ops_cash_vouchers SET amount = 1, amount_base = 1 WHERE id = :i"), {"i": v["id"]})
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("DELETE FROM ops_cash_vouchers WHERE id = :i"), {"i": v["id"]})
    _error(client.post(f"{url}:void", json={"reason": "Sai số tiền"}, headers=h["ops_staff"]), 403, "FORBIDDEN")
    r = client.post(f"{url}:void", json={"reason": "Sai số tiền"}, headers=h["manager"])
    assert (r.json()["status"], r.json()["void_reason"]) == ("void", "Sai số tiền")
    _error(
        client.post(f"{url}:void", json={"reason": "Lần nữa"}, headers=h["manager"]), 409, "INVALID_STATE_TRANSITION"
    )
    assert _balance(client, world)["balance"] == "-2000000.00"
    _error(client.post(V, json=_voucher(world, replaces_id=pay["id"]), headers=h["ops_staff"]), 409,
           "INVALID_STATE_TRANSITION")  # fmt: skip
    fix = _recorded(client, world, amount="5500000", replaces_id=v["id"])
    assert fix["replaces_id"] == v["id"]
    _error(client.post(V, json=_voucher(world, replaces_id=v["id"]), headers=h["ops_staff"]), 409, "DUPLICATE")
    assert _balance(client, world)["balance"] == "3500000.00"

    ledger = client.get(f"/api/v2/ops/cashbook/ledger?contract_id={cid}", headers=h["ops_staff"]).json()
    assert [r["running_balance"] for r in ledger["rows"]] == ["-2000000.00", "3500000.00"]
    page = client.get(f"{V}?contract_id={cid}&status=void", headers=h["admin"]).json()
    assert page["total"] == 1 and page["items"][0]["id"] == v["id"]
    actions = [a.action for a in _logs("cash_voucher.", v["id"])]
    assert actions == [
        "cash_voucher.create",
        "cash_voucher.attach_file",
        "cash_voucher.submit",
        "cash_voucher.record",
        "cash_voucher.void",
    ]
    void_log = _logs("cash_voucher.void", v["id"])[0]
    assert (void_log.before, void_log.after["status"], void_log.after["reason"]) == (
        {"status": "recorded"},
        "void",
        "Sai số tiền",
    )
    f = client.get(f"/api/v2/files/{fid}", headers=h["business_manager"])
    assert f.status_code == 200
    assert client.get(f"/api/v2/files/{fid}", headers=h["customer"]).status_code in (403, 404)


def test_closed_contract_only_settlement_vouchers(client, world):
    with SessionLocal() as db:
        db.execute(
            text("UPDATE ops_service_contracts SET status = 'closed' WHERE id = :i"), {"i": world["contract_a"].id}
        )
        db.commit()
    _error(client.post(V, json=_voucher(world), headers=world["h"]["ops_staff"]), 409, "INVALID_STATE_TRANSITION")


# --- Tạm ứng ------------------------------------------------------------------------------------------------------


def _advance(client, world, amount="10000000", who="ops_staff", **kw) -> dict:
    body = {"contract_id": world["contract_a"].id, "purpose": "Nộp phí cảng, cược container lô nhập", "amount": amount}
    r = client.post(A, json={**body, **kw}, headers=world["h"][who])
    assert r.status_code == 201, r.text
    return r.json()


def _disbursed(client, world, amount="10000000") -> tuple[dict, int]:
    h = world["h"]
    a = _advance(client, world, amount)
    assert client.post(f"{A}/{a['id']}:approve", headers=h["manager"]).status_code == 200
    fid = _upload(client, f"{A}/{a['id']}", h["manager"])
    r = client.post(f"{A}/{a['id']}:disburse", json={"payment_method": "cash", "file_ids": [fid]}, headers=h["manager"])
    assert r.status_code == 200, r.text
    return r.json(), fid


def _expenses(fid: int, *amounts: tuple[str, str]) -> dict:
    return {
        "expenses": [
            {"category_code": cat, "description": f"Chi {cat}", "amount": amt, "file_ids": [fid]}
            for cat, amt in amounts
        ]
    }


def test_advance_end_to_end_excess(client, world):
    """E2E-07: tạm ứng 10.000.000 → hoàn ứng 8.500.000 (C03 + 2×C01) → nhân viên nộp lại 1.500.000 (T05)."""
    h, me = world["h"], world["users"]["ops_staff"]
    a = _advance(client, world)
    assert (a["advance_no"], a["status"], a["staff_id"]) == (f"TU-{YEAR}-0001", "requested", me.id)
    assert a["due_date"] == str(TODAY + timedelta(days=7))
    url = f"{A}/{a['id']}"
    _error(client.post(f"{url}:approve", headers=h["ops_staff"]), 403, "FORBIDDEN")
    _error(client.post(f"{url}:disburse", json={"payment_method": "cash", "file_ids": [1]}, headers=h["manager"]), 409,
           "INVALID_STATE_TRANSITION")  # fmt: skip
    assert client.post(f"{url}:approve", headers=h["manager"]).json()["status"] == "approved"
    _error(client.post(f"{url}:disburse", json={"payment_method": "cash", "file_ids": [999]}, headers=h["manager"]),
           422, "VALIDATION_ERROR")  # fmt: skip
    fid = _upload(client, url, h["ops_staff"])
    d = client.post(f"{url}:disburse", json={"payment_method": "cash", "file_ids": [fid]}, headers=h["manager"]).json()
    assert (d["status"], d["outstanding"], len(d["vouchers"])) == ("disbursed", "10000000.00", 1)
    c08 = d["vouchers"][0]
    assert (c08["category_code"], c08["status"], c08["voucher_type"], c08["prepared_by"]) == (
        "C08",
        "recorded",
        "payment",
        me.id,
    )
    ev = _events("EV-115")
    assert len(ev) == 1 and (ev[0]["data"]["advance_id"], ev[0]["data"]["amount"]) == (a["id"], "10000000.00")
    _error(client.post(f"{V}/{c08['id']}:void", json={"reason": "Thử hủy"}, headers=h["manager"]), 409,
           "INVALID_STATE_TRANSITION")  # fmt: skip

    body = _expenses(fid, ("C03", "1500000"), ("C01", "5000000"), ("C01", "2000000"))
    _error(client.post(f"{url}:submit-settlement", json=body, headers=h["manager"]), 403, "FORBIDDEN")  # không phải NV
    bad = _expenses(fid, ("C06", "100"))
    _error(client.post(f"{url}:submit-settlement", json=bad, headers=h["ops_staff"]), 422, "VALIDATION_ERROR")
    s = client.post(f"{url}:submit-settlement", json=body, headers=h["ops_staff"]).json()
    assert (s["status"], s["spent_total"], s["outstanding"]) == ("settling", "8500000.00", "10000000.00")
    offsets = [v for v in s["vouchers"] if v["payment_method"] == "offset"]
    assert [(v["category_code"], v["status"]) for v in offsets] == [
        ("C03", "submitted"),
        ("C01", "submitted"),
        ("C01", "submitted"),
    ]
    _error(client.post(f"{url}:approve-settlement", json={}, headers=h["manager"]), 422, "VALIDATION_ERROR")
    _error(client.post(f"{url}:approve-settlement", json={}, headers=h["ops_staff"]), 403, "FORBIDDEN")
    r = client.post(
        f"{url}:approve-settlement", json={"payment_method": "cash", "file_ids": [fid]}, headers=h["manager"]
    )
    done = r.json()
    assert (done["status"], done["outstanding"]) == ("settled", "0.00"), r.text
    t05 = [v for v in done["vouchers"] if v["category_code"] == "T05"]
    assert len(t05) == 1 and (t05[0]["amount"], t05[0]["status"]) == ("1500000.00", "recorded")
    assert all(v["status"] == "recorded" for v in done["vouchers"])
    assert len(_events("EV-116")) == 1
    # quỹ: chi C08 10tr, thu T05 1,5tr; bù trừ không đổi dòng tiền
    b = _balance(client, world)
    assert (b["balance"], b["offset_total"]) == ("-8500000.00", "8500000.00")
    actions = [x.action for x in _logs("staff_advance.", a["id"])]
    assert actions == [
        "staff_advance.request",
        "staff_advance.approve",
        "staff_advance.attach_file",
        "staff_advance.disburse",
        "staff_advance.submit_settlement",
        "staff_advance.settle",
    ]
    settle = _logs("staff_advance.settle", a["id"])[0]
    assert settle.before == {"status": "settling"} and settle.after["difference"] == "1500000.00"


def test_advance_shortage_and_return(client, world):
    h = world["h"]
    a, fid = _disbursed(client, world, "10000000")
    url = f"{A}/{a['id']}"
    client.post(f"{url}:submit-settlement", json=_expenses(fid, ("C02", "9000000")), headers=h["ops_staff"])
    r = client.post(f"{url}:return-settlement", json={"reason": "Thiếu hóa đơn vận tải"}, headers=h["manager"]).json()
    assert (r["status"], r["spent_total"]) == ("disbursed", None)
    assert [v["status"] for v in r["vouchers"] if v["payment_method"] == "offset"] == ["void"]
    body = _expenses(fid, ("C02", "9000000"), ("C05", "3000000"))  # 12tr > 10tr tạm ứng
    client.post(f"{url}:submit-settlement", json=body, headers=h["ops_staff"])
    done = client.post(
        f"{url}:approve-settlement", json={"payment_method": "bank_transfer", "file_ids": [fid]}, headers=h["manager"]
    ).json()
    assert (done["status"], done["outstanding"]) == ("settled", "0.00")
    top_up = [v for v in done["vouchers"] if v["category_code"] == "C08" and v["id"] != a["vouchers"][0]["id"]]
    assert len(top_up) == 1 and (top_up[0]["amount"], top_up[0]["payment_method"]) == ("2000000.00", "bank_transfer")
    assert _balance(client, world)["balance"] == "-12000000.00"


def test_br115_open_advance_limit_and_visibility(client, world):
    h = world["h"]
    first = _advance(client, world)
    _advance(client, world, "500000")
    _error(client.post(A, json={"contract_id": world["contract_a"].id, "purpose": "Lần thứ ba", "amount": "1"},
                       headers=h["ops_staff"]), 409, "INVALID_STATE_TRANSITION")  # fmt: skip
    r = client.post(f"{A}/{first['id']}:reject", json={"reason": "Không đúng mục đích"}, headers=h["manager"])
    assert r.json()["status"] == "rejected"
    assert _advance(client, world, "700000")["status"] == "requested"  # khoản bị từ chối không tính BR-115
    other = _advance(client, world, "100", who="ops_staff_2")
    _error(client.get(f"{A}/{other['id']}", headers=h["ops_staff"]), 404, "NOT_FOUND")
    assert client.get(A, headers=h["ops_staff"]).json()["total"] == 3
    assert client.get(A, headers=h["manager"]).json()["total"] == 4
    past = {"contract_id": world["contract_a"].id, "purpose": "Hạn quá khứ", "amount": "1",
            "due_date": str(TODAY - timedelta(days=1))}  # fmt: skip
    _error(client.post(A, json=past, headers=h["ops_staff_2"]), 422, "VALIDATION_ERROR")


def test_m10_overdue_alert_and_auto_resolve(client, world):
    h = world["h"]
    a, fid = _disbursed(client, world)
    with SessionLocal() as db:
        assert advance_alerts.scan(db) == {"raised": 0, "resolved": 0}  # chưa quá hạn
        db.execute(text("UPDATE ops_staff_advances SET due_date = :d WHERE id = :i"),
                   {"d": TODAY - timedelta(days=3), "i": a["id"]})  # fmt: skip
        db.commit()
        assert advance_alerts.scan(db)["raised"] == 1
        assert advance_alerts.scan(db)["raised"] == 0  # không lặp
    assert client.get(f"{A}?overdue=true", headers=h["manager"]).json()["total"] == 1
    with SessionLocal() as db:
        alert = db.scalar(select(CtlAlert).where(CtlAlert.alert_type == "staff_advance_overdue"))
        assert (alert.status, alert.contract_id, alert.details["overdue_days"]) == ("open", a["contract_id"], 3)
    url = f"{A}/{a['id']}"
    client.post(f"{url}:submit-settlement", json=_expenses(fid, ("C03", "10000000")), headers=h["ops_staff"])
    client.post(f"{url}:approve-settlement", json={}, headers=h["manager"])
    ev = _events("EV-116")
    assert len(ev) == 1
    assert consumer.process("control", ev[0]) == "processed"
    with SessionLocal() as db:
        alert = db.scalar(select(CtlAlert).where(CtlAlert.alert_type == "staff_advance_overdue"))
        assert alert.status == "resolved"


# --- Bảng kê đối soát (SOA) ---------------------------------------------------------------------------------------


@pytest.fixture()
def soa(world, make_user, auth_headers):
    other = make_user("kh.b", roles=["customer"], party_id=world["b"].id, email="kh@b.vn")
    return {"customer_b": auth_headers(other.username)}


def _invoice(client, world, no: str, day: date, amount: str, currency="VND", who="manager"):
    body = {
        "contract_id": world["contract_a"].id,
        "invoice_no": no,
        "invoice_date": str(day),
        "seller_name": "Shanghai Export Co.",
        "buyer_name": "Công ty Minh An",
        "currency": currency,
        "total_amount": amount,
    }
    return client.post(INV, json=body, headers=world["h"][who])


def test_statement_lifecycle_lock_and_reissue(client, world, soa):
    h = world["h"]
    assert _invoice(client, world, "INV-1", M1 + timedelta(days=2), "10000000").status_code == 201
    assert _invoice(client, world, "INV-2", M1 + timedelta(days=5), "4000000").status_code == 201
    assert _invoice(client, world, "INV-USD", M1 + timedelta(days=6), "500", "USD").status_code == 201
    assert _invoice(client, world, "INV-OLD", M2 + timedelta(days=1), "1000000").status_code == 201
    _recorded(client, world, amount="3000000", voucher_date=str(M1 + timedelta(days=10)))  # T01
    _recorded(client, world, category_code="T03", amount="1000000", voucher_date=str(M1 + timedelta(days=11)))
    _recorded(client, world, voucher_type="payment", category_code="C07", amount="500000",
              voucher_date=str(M1 + timedelta(days=12)))  # fmt: skip

    # Tháng M2: lập, gửi, manager ghi nhận thay khách (bắt buộc ghi chú) → số dư cuối kỳ làm đầu kỳ M1
    s2 = client.post(S, json={"customer_id": world["a"].id, "month": M2.strftime("%Y-%m")}, headers=h["ops_staff"])
    assert s2.status_code == 201, s2.text
    s2 = s2.json()
    assert (s2["opening_balance"], s2["invoice_total"], s2["balance_due"]) == ("0.00", "1000000.00", "1000000.00")
    client.post(f"{S}/{s2['id']}:send", headers=h["manager"])
    _error(client.post(f"{S}/{s2['id']}:confirm", json={}, headers=h["manager"]), 422, "VALIDATION_ERROR")
    r = client.post(f"{S}/{s2['id']}:confirm", json={"note": "Khách xác nhận qua email ngày 05"}, headers=h["manager"])
    assert (r.json()["status"], r.json()["response_via"]) == ("confirmed", "staff")

    body = {"customer_id": world["a"].id, "month": M1.strftime("%Y-%m")}
    _error(client.post(S, json=body, headers=h["customer"]), 403, "FORBIDDEN")
    _error(client.post(S, json={**body, "month": TODAY.strftime("%Y-%m")}, headers=h["ops_staff"]), 422,
           "VALIDATION_ERROR")  # fmt: skip
    s = client.post(S, json=body, headers=h["ops_staff"]).json()
    assert s["settlement_no"] == f"BK-{YEAR}-0002" and s["status"] == "draft"
    assert (s["opening_balance"], s["invoice_count"], s["invoice_total"], s["paid_amount"], s["balance_due"]) == (
        "1000000.00",
        2,
        "14000000.00",
        "3500000.00",  # 3tr T01 + 1tr T03 − 0,5tr C07
        "11500000.00",
    )
    assert s["other_currency_totals"] == {"USD": "500.00"}
    assert [i["invoice_no"] for i in s["statement_lines"]["invoices"]] == ["INV-1", "INV-2"]
    _error(client.post(S, json=body, headers=h["ops_staff"]), 409, "DUPLICATE")
    url = f"{S}/{s['id']}"
    _error(client.get(f"{P}/{s['id']}", headers=h["customer"]), 404, "NOT_FOUND")  # nháp: khách không thấy
    _error(client.post(f"{url}:confirm", json={}, headers=h["customer"]), 404, "NOT_FOUND")
    _error(client.post(f"{url}:send", headers=h["ops_staff"]), 403, "FORBIDDEN")
    assert client.post(f"{url}:send", headers=h["manager"]).json()["status"] == "sent"
    _error(client.post(f"{url}:recompute", headers=h["ops_staff"]), 409, "INVALID_STATE_TRANSITION")

    portal = client.get(P, headers=h["customer"]).json()
    assert portal["total"] == 2 and "prepared_by" not in portal["items"][0]
    _error(client.get(f"{P}/{s['id']}", headers=soa["customer_b"]), 404, "NOT_FOUND")
    _error(client.post(f"{url}:confirm", json={}, headers=soa["customer_b"]), 404, "NOT_FOUND")
    _error(client.post(f"{url}:confirm", json={}, headers=h["business_manager"]), 403, "FORBIDDEN")

    # Khách khiếu nại → không khóa hóa đơn; bản cũ giữ nguyên, phát hành lại bản mới
    r = client.post(f"{url}:dispute", json={"reason": "Thiếu khoản thanh toán ngày 20"}, headers=h["customer"])
    assert (r.json()["status"], r.json()["response_via"], r.json()["dispute_reason"]) == (
        "disputed",
        "customer",
        "Thiếu khoản thanh toán ngày 20",
    )
    assert _invoice(client, world, "INV-3", M1 + timedelta(days=20), "2000000").status_code == 201
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("UPDATE ops_contract_settlements SET status = 'draft' WHERE id = :i"), {"i": s["id"]})
    _error(client.post(S, json=body, headers=h["ops_staff"]), 409, "INVALID_STATE_TRANSITION")  # phải :reissue
    _error(client.post(f"{url}:reissue", headers=h["customer"]), 403, "FORBIDDEN")
    n = client.post(f"{url}:reissue", headers=h["ops_staff"])
    assert n.status_code == 201, n.text
    n = n.json()
    assert (n["previous_id"], n["status"], n["invoice_count"], n["balance_due"]) == (
        s["id"],
        "draft",
        3,
        "13500000.00",
    )
    _error(client.post(f"{url}:reissue", headers=h["ops_staff"]), 409, "DUPLICATE")
    old = client.get(url, headers=h["manager"]).json()
    assert (old["status"], old["balance_due"]) == ("disputed", "11500000.00")  # bản cũ không đổi

    nurl = f"{S}/{n['id']}"
    client.post(f"{nurl}:send", headers=h["business_manager"])
    r = client.post(f"{nurl}:confirm", json={"note": "Đồng ý số liệu"}, headers=h["customer"])
    assert (r.json()["status"], r.json()["response_via"]) == ("confirmed", "customer")
    assert "prepared_by" not in r.json()

    # CONFIRMED → khóa hóa đơn thương mại thuộc kỳ (service + trigger); kỳ khác vẫn mở
    err = _error(_invoice(client, world, "INV-4", M1 + timedelta(days=21), "1"), 409, "INVALID_STATE_TRANSITION")
    assert n["settlement_no"] in err["message"]
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(text("UPDATE ops_commercial_invoices SET total_amount = 1 WHERE invoice_no = 'INV-1'"))
    with pytest.raises(DBAPIError), SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO ops_commercial_invoice_items (invoice_id, line_no, description) "
                "SELECT id, 99, 'x' FROM ops_commercial_invoices WHERE invoice_no = 'INV-1'"
            )
        )
    assert _invoice(client, world, "INV-NOW", TODAY, "1").status_code == 201
    err = _error(_invoice(client, world, "INV-M2", M2 + timedelta(days=3), "1"), 409, "INVALID_STATE_TRANSITION")
    assert s2["settlement_no"] in err["message"]

    _error(client.post(f"{nurl}:close", headers=h["ops_staff"]), 403, "FORBIDDEN")
    assert client.post(f"{nurl}:close", headers=h["manager"]).json()["status"] == "closed"
    _error(_invoice(client, world, "INV-5", M1 + timedelta(days=22), "1"), 409, "INVALID_STATE_TRANSITION")

    csv_r = client.get(f"{nurl}/export.csv", headers=h["ops_staff"])
    assert csv_r.status_code == 200 and csv_r.headers["content-type"].startswith("text/csv")
    assert n["settlement_no"] in csv_r.text and "INV-3" in csv_r.text and "13500000.00" in csv_r.text
    page = client.get(f"{nurl}/print", headers=h["manager"])
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "BẢNG KÊ ĐỐI SOÁT" in page.text and "Công ty Minh An" in page.text
    assert client.get(f"{P}/{n['id']}/export.csv", headers=h["customer"]).status_code == 200
    assert client.get(f"{P}/{n['id']}/print", headers=soa["customer_b"]).status_code == 404

    assert [x.action for x in _logs("statement.", s["id"])] == [
        "statement.create",
        "statement.send",
        "statement.dispute",
        "statement.reissue",
    ]
    assert [x.action for x in _logs("statement.", n["id"])] == [
        "statement.create",
        "statement.send",
        "statement.confirm",
        "statement.close",
    ]
    disp = _logs("statement.dispute", s["id"])[0]
    assert disp.before == {"status": "sent"} and disp.after["response_via"] == "customer"


def test_final_settlement_still_unaffected(client, world):
    """Bảng kê (contract_id NULL) không lẫn vào quyết toán `final` của Giai đoạn 19 theo hợp đồng."""
    h = world["h"]
    _invoice(client, world, "INV-1", M1 + timedelta(days=2), "1000")
    client.post(S, json={"customer_id": world["a"].id, "month": M1.strftime("%Y-%m")}, headers=h["ops_staff"])
    r = client.get(f"/api/v2/ops/service-contracts/{world['contract_a'].id}/settlement", headers=h["manager"])
    _error(r, 404, "NOT_FOUND")
    with pytest.raises(DBAPIError), SessionLocal() as db:  # bảng kê phải theo khách hàng, không theo hợp đồng
        db.execute(
            text("UPDATE ops_contract_settlements SET contract_id = :c WHERE settlement_type = 'periodic_statement'"),
            {"c": world["contract_a"].id},
        )
