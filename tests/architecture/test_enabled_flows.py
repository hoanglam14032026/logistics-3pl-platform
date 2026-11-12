"""Architecture test N6 - Bật/tắt luồng qua ENABLED_FLOWS.
Luồng thật của dự án: ops, control, learning, assessment (07 §4.2).
"""
import os

def _enabled_flows() -> set[str]:
    raw = os.getenv("ENABLED_FLOWS", "ops,control,learning,assessment")
    return {x.strip() for x in raw.split(",") if x.strip()}

def test_enabled_flows_default_includes_all():
    flows = _enabled_flows()
    assert "ops" in flows
    assert "control" in flows
    assert "learning" in flows
    assert "assessment" in flows

def test_disabled_flow_is_respected():
    os.environ["ENABLED_FLOWS"] = "ops,control"
    try:
        flows = _enabled_flows()
        assert "learning" not in flows
        assert "assessment" not in flows
        assert "ops" in flows
    finally:
        os.environ["ENABLED_FLOWS"] = "ops,control,learning,assessment"