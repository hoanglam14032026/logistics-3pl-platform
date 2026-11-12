# ruff: noqa: F811  (fixture world nhập từ module test khác)
"""Giai đoạn 23 – kiểm thử giao diện nhập tay giấy tờ theo hợp đồng (`/static/doc_entry.html`) bằng Playwright.

Chạy trình duyệt thật (Chromium headless) với máy chủ uvicorn thật trên CSDL test. Bỏ qua nếu chưa cài:
    pip install playwright && python -m playwright install chromium
"""

import socket
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api")
import uvicorn  # noqa: E402
from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from app.main import create_app  # noqa: E402
from tests.conftest import DEFAULT_PASSWORD  # noqa: E402
from tests.flows.operations.test_shipments import world  # noqa: E402,F401


@pytest.fixture()
def live_server(world):  # noqa: F811
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "uvicorn không khởi động"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture()
def page(live_server):
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except PlaywrightError as exc:  # chưa cài trình duyệt
            pytest.skip(f"Chưa cài Chromium cho Playwright: {exc}")
        context = browser.new_context(timezone_id="Asia/Ho_Chi_Minh", locale="vi-VN")
        page = context.new_page()
        yield page
        browser.close()


def _login_and_open_contract(page, base, world):
    page.goto(f"{base}/static/doc_entry.html")
    page.fill("#user", world["users"]["ops_staff"].username)
    page.fill("#pass", DEFAULT_PASSWORD)
    page.click("#btnLogin")
    expect(page.locator("#app")).to_be_visible()
    page.fill("#contractQ", world["contract_a"].contract_no)
    page.click("#btnOpenContract")
    expect(page.locator("#docBar")).to_be_visible()


def test_static_page_served_without_login(live_server):
    import httpx

    r = httpx.get(f"{live_server}/static/doc_entry.html")
    assert r.status_code == 200 and 'id="btnLogin"' in r.text


def test_declare_autosave_confirm_flow(page, live_server, world):
    patches: list[str] = []
    page.on("request", lambda req: patches.append(req.url) if req.method == "PATCH" else None)
    _login_and_open_contract(page, live_server, world)

    # khai báo giấy tờ cấp hợp đồng từ danh mục
    page.click("#btnNew")
    page.select_option("#nCatalog", "customer_business_license")
    page.click("#nOk")
    expect(page.locator("#msg")).to_contain_text("Đã khai báo")
    expect(page.locator("#formArea [data-path='company_name']")).to_be_visible()

    # nhập từng trường → tự động lưu
    page.fill("[data-path='company_name']", "Công ty Minh An")
    expect(page.locator("#saveState")).to_contain_text("Đã lưu", timeout=5000)
    page.fill("[data-path='tax_code']", "0100000001")
    expect(page.locator("#saveState")).to_contain_text("Đã lưu", timeout=5000)
    assert len(patches) >= 2

    # sau khi lưu: không còn bộ đếm treo / cờ chưa lưu (không hỏi "rời trang?")
    assert page.evaluate("S.saveTimer === null && S.dirty === false && S.saving === false")

    # blur / bấm qua lại mà không sửa gì → không gửi thêm PATCH
    sent = len(patches)
    page.click("[data-path='company_name']")
    page.click("[data-path='tax_code']")
    page.click("[data-meta='note']")
    page.wait_for_timeout(1200)
    assert len(patches) == sent

    # xác nhận hoàn tất → khóa form
    page.click("#btnConfirm")
    expect(page.locator("#msg")).to_contain_text("Đã xác nhận")
    expect(page.locator("[data-path='company_name']")).to_be_disabled()
    expect(page.locator("#btnReopen")).to_be_enabled()

    # mở lại để sửa (hộp thoại nhập lý do – bỏ trống không cho đồng ý)
    page.click("#btnReopen")
    expect(page.locator("#dlgReason")).to_be_visible()
    page.click("#rOk")
    expect(page.locator("#rErr")).to_contain_text("Nhập lý do")
    page.fill("#rText", "Nhập nhầm mã số thuế")
    page.click("#rOk")
    expect(page.locator("#msg")).to_contain_text("Đã mở lại")
    expect(page.locator("[data-path='company_name']")).to_be_enabled()


def test_confirm_missing_required_shows_field_error(page, live_server, world):
    _login_and_open_contract(page, live_server, world)
    page.click("#btnNew")
    page.select_option("#nCatalog", "customer_business_license")
    page.click("#nOk")
    expect(page.locator("#formArea [data-path='company_name']")).to_be_visible()
    page.click("#btnConfirm")
    expect(page.locator("#msg")).to_contain_text("Thiếu trường bắt buộc")


def test_datetime_uses_browser_timezone(page, live_server):
    page.goto(f"{live_server}/static/doc_entry.html")
    # giờ nhập theo múi giờ trình duyệt (Asia/Ho_Chi_Minh) → UTC khi gửi, hiển thị lại đúng giờ đã nhập
    iso = page.evaluate(
        "(() => { const el = document.createElement('input'); el.value = '2026-11-12T09:30'; "
        "el.dataset.type = 'datetime'; return readEl(el); })()"
    )
    assert iso == "2026-11-12T02:30:00.000Z"
    assert page.evaluate("toLocalInput('2026-11-12T02:30:00+00:00')") == "2026-11-12T09:30"
    assert page.evaluate("toLocalInput('2026-11-12T09:30:00+07:00')") == "2026-11-12T09:30"


def _declare_license(page):
    page.click("#btnNew")
    page.select_option("#nCatalog", "customer_business_license")
    page.click("#nOk")
    expect(page.locator("#formArea [data-path='company_name']")).to_be_visible()


def test_missing_required_counter_updates_after_save(page, live_server, world):
    _login_and_open_contract(page, live_server, world)
    _declare_license(page)
    info = page.locator("#missingInfo")
    expect(info).to_contain_text("trường bắt buộc trống")
    start = int(info.inner_text().split()[1])
    page.fill("[data-path='company_name']", "Công ty Minh An")
    expect(page.locator("#saveState")).to_contain_text("Đã lưu", timeout=5000)
    expect(info).to_contain_text(f"Còn {start - 1} trường") if start > 1 else expect(info).to_contain_text("Đủ")
    assert page.locator("label[for='f_company_name']").count() == 1  # nhãn gắn với ô nhập


def test_expired_access_token_is_refreshed_and_edit_saved(page, live_server, world):
    _login_and_open_contract(page, live_server, world)
    _declare_license(page)
    page.evaluate("S.token = 'het-han'")  # giả lập mã truy cập hết hạn
    page.fill("[data-path='company_name']", "Sau khi làm mới phiên")
    expect(page.locator("#saveState")).to_contain_text("Đã lưu", timeout=5000)
    expect(page.locator("#app")).to_be_visible()
    assert page.evaluate("S.token !== 'het-han'")


def test_session_lost_keeps_typed_values_until_relogin(page, live_server, world):
    _login_and_open_contract(page, live_server, world)
    _declare_license(page)
    page.evaluate("S.token = 'het-han'; S.refresh = null")
    page.fill("[data-path='company_name']", "Gõ khi mất phiên")
    expect(page.locator("#login")).to_be_visible(timeout=5000)
    page.fill("#user", world["users"]["ops_staff"].username)
    page.fill("#pass", DEFAULT_PASSWORD)
    page.click("#btnLogin")
    expect(page.locator("#saveState")).to_contain_text("Đã lưu", timeout=5000)
    assert page.input_value("[data-path='company_name']") == "Gõ khi mất phiên"


def test_switching_contract_flushes_pending_edit(page, live_server, world):
    patches: list[str] = []
    page.on("request", lambda req: patches.append(req.url) if req.method == "PATCH" else None)
    _login_and_open_contract(page, live_server, world)
    _declare_license(page)
    page.evaluate("S.saveTimer = null")
    page.fill("[data-path='company_name']", "Chưa kịp lưu")
    page.fill("#contractQ", world["contract_b"].contract_no)
    page.click("#btnOpenContract")  # bấm ngay – trước khi hết 700 ms chờ tự động lưu
    expect(page.locator("#contractInfo")).to_contain_text(world["contract_b"].contract_no)
    assert patches and page.evaluate("S.dirty === false")


def test_shipment_level_document_needs_a_shipment(page, live_server, world):
    _login_and_open_contract(page, live_server, world)  # contract_a chưa có lô trong world
    page.click("#btnNew")
    page.select_option("#nCatalog", "bill_of_lading")
    if page.evaluate("S.shipments.length") == 0:
        expect(page.locator("#nShipErr")).to_contain_text("chưa có lô")
        expect(page.locator("#nOk")).to_be_disabled()


def test_boolean_field_renders_yes_no_select(page, live_server):
    page.goto(f"{live_server}/static/doc_entry.html")
    html = page.evaluate(
        "(() => { S.doc = {editable: true}; return inputFor({key: 'is_dg', type: 'boolean'}, true, 'is_dg'); })()"
    )
    assert "<select" in html and 'value="true" selected' in html and "Không" in html


def test_contract_dossier_attachments_are_view_only(page, live_server, world, client):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (240, 160), "white").save(buf, format="PNG")
    r = client.post(
        f"/api/v2/ops/service-contracts/{world['contract_a'].id}/attachments",
        data={"kind": "signed_contract", "description": "Bản hợp đồng đã ký 2 bên"},
        files={"file": ("hop-dong-ky.png", buf.getvalue(), "image/png")},
        headers=world["h"]["ops_staff"],
    )
    assert r.status_code == 201, r.text
    att_id = r.json()["id"]

    _login_and_open_contract(page, live_server, world)
    expect(page.locator("#progress")).to_contain_text("Hồ sơ hợp đồng: ")
    page.select_option("#docSelect", f"att:{att_id}")
    expect(page.locator("#formArea")).to_contain_text("chỉ xem")
    expect(page.locator("#formArea")).to_contain_text("Bản hợp đồng đã ký 2 bên")
    expect(page.locator("#pageBox img")).to_be_visible()
    for b in ("#btnConfirm", "#btnReopen", "#btnVoid"):
        expect(page.locator(b)).to_be_disabled()
    # quay lại khai báo giấy tờ nhập tay vẫn bình thường
    _declare_license(page)
    expect(page.locator("#btnConfirm")).to_be_enabled()
