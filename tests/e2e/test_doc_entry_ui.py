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

    # mở lại để sửa (prompt nhập lý do)
    page.once("dialog", lambda d: d.accept("Nhập nhầm mã số thuế"))
    page.click("#btnReopen")
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
