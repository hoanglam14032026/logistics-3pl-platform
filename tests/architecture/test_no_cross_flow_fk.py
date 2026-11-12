"""Architecture test N3 - Migration không có FK chéo luồng.
Mỗi luồng chỉ FK tới bảng của chính nó + bảng shared/core.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG_DIR = ROOT / "alembic" / "versions"

# Bảng -> luồng (cập nhật khi thêm bảng mới)
TABLE_FLOW = {
    "ops_documents": "customer_transaction",
    "ops_contracts": "customer_transaction",
    "service_catalog": "customer_transaction",
    "transport_orders": "transport",
    "transport_trips": "transport",
    "warehouse_requests": "warehouse",
    "warehouse_bookings": "warehouse",
    "customs_dossiers": "customs",
    "customs_quotations": "customs",
}
SHARED_TABLES = {"sys_users", "sys_user_roles", "outbox_events", "audit_logs"}

def test_no_cross_flow_fk():
    if not MIG_DIR.exists():
        return  # Chưa có migrations thì bỏ qua
    violations = []
    for f in MIG_DIR.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        # Tìm ForeignKey("bang.cot")
        for m in re.finditer(r"ForeignKey\(\s*[\"'](\w+)\.", text):
            target_table = m.group(1)
            if target_table in SHARED_TABLES:
                continue
            # Tìm bảng nguồn từ create_table("bang"
            for cm in re.finditer(r"create_table\(\s*[\"'](\w+)", text):
                src_table = cm.group(1)
                src_flow = TABLE_FLOW.get(src_table)
                dst_flow = TABLE_FLOW.get(target_table)
                if src_flow and dst_flow and src_flow != dst_flow:
                    violations.append(f"{f.name}: {src_table} ({src_flow}) -> FK {target_table} ({dst_flow})")
    assert not violations, "Vi pham N3 - FK cheo luong:\n" + "\n".join(violations)
