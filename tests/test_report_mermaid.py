from __future__ import annotations

from pathlib import Path

from awengine.loader import load_spec
from awengine.trace.mermaid import flow_mermaid

ROOT = Path(__file__).resolve().parent.parent


def test_flow_mermaid_declares_end_once() -> None:
    spec = load_spec(ROOT / "workflows" / "bugfix.yaml")
    flow = flow_mermaid(spec)
    assert flow.count('__END__(["END"])') == 1
    assert flow.count("-->|") >= 2
    assert "n_coder" in flow
    assert "n_review_final" in flow


def test_flow_mermaid_hello_declares_end_once() -> None:
    spec = load_spec(ROOT / "workflows" / "hello.yaml")
    flow = flow_mermaid(spec)
    assert flow.count('__END__(["END"])') == 1
    assert flow.count("__END__") == 3  # declaration + one edge + class line
