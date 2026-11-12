"""Architecture test N4 - Luồng không import nội bộ luồng khác.
Chi được giao tiếp qua events + snapshot, không import trực tiếp.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Map thư mục -> luồng. Dự án logistics: 4 luồng = 4 module
FLOWS = {
    "app/api/customer_transaction": "customer_transaction",
    "app/api/transport": "transport",
    "app/api/warehouse": "warehouse",
    "app/api/customs": "customs",
    "app/services/customer_transaction": "customer_transaction",
    "app/services/transport": "transport",
    "app/services/warehouse": "warehouse",
    "app/services/customs": "customs",
    "app/models/customer_transaction": "customer_transaction",
    "app/models/transport": "transport",
    "app/models/warehouse": "warehouse",
    "app/models/customs": "customs",
}
ALLOWED = {"app.core", "app.shared", "app.db", "app.events"}

def _flow_of(path: Path) -> str | None:
    s = str(path)
    for prefix, flow in FLOWS.items():
        if prefix in s:
            return flow
    return None

def _imports_of(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append(node.module)
    return out

def test_no_cross_flow_import():
    violations = []
    for py in ROOT.rglob("*.py"):
        if "tests/" in str(py):
            continue
        src_flow = _flow_of(py)
        if not src_flow:
            continue
        for imp in _imports_of(py):
            # Tìm luồng đích trong import
            for prefix, dst_flow in FLOWS.items():
                if imp.startswith(prefix.replace("/", ".")) or imp.startswith(prefix):
                    if dst_flow != src_flow:
                        violations.append(f"{py.relative_to(ROOT)} ({src_flow}) -> {imp} ({dst_flow})")
    assert not violations, "Vi pham N4 - import cheo luong:\n" + "\n".join(violations)