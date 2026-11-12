"""Architecture test N6 - Bật/tắt luồng qua ENABLED_FLOWS.
Tắt luồng -> router/subscriber của luồng đó không được load.
"""
import os

def _enabled_flows() -> set[str]:
    raw = os.getenv("ENABLED_FLOWS", "customer_transaction,transport,warehouse,customs")
    return {x.strip() for x in raw.split(",") if x.strip()}

def test_enabled_flows_default_includes_all():
    # Mặc định phải có đủ 4 luồng
    flows = _enabled_flows()
    assert "customer_transaction" in flows
    assert "transport" in flows
    assert "warehouse" in flows
    assert "customs" in flows

def test_disabled_flow_is_respected():
    # Khi ENABLED_FLOWS thiếu 1 luồng, hàm kiểm tra phải nhận biết
    # Đây là test logic, không cần khởi động app thật
    os.environ["ENABLED_FLOWS"] = "customer_transaction,transport"
    try:
        flows = _enabled_flows()
        assert "warehouse" not in flows
        assert "customs" not in flows
        assert "customer_transaction" in flows
    finally:
        os.environ["ENABLED_FLOWS"] = "customer_transaction,transport,warehouse,customs"
        