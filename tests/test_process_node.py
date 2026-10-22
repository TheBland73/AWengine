"""Process-node protocol: JSON stdin -> JSON stdout, artifacts included."""

from __future__ import annotations

from pathlib import Path

from awengine.budget import Budget, Pricing
from awengine.engine import RunOptions, WorkflowEngine
from awengine.events import EventBus
from awengine.llm.fake import ScriptedLLM
from awengine.loader import load_spec_text
from awengine.checkpoint import RunStore

SPEC = """
name: process-demo
version: 1
nodes:
  scan:
    type: process
    cmd: "python node.py"
    contract:
      required: [files]
      types: {files: list}
edges:
  - {src: scan, dst: END}
converge:
  goal: "scan.files[0] == 'a.py'"
  rounds: {min: 1, max: 1}
  steps: {min: 1, max: 2}
"""

NODE = """
import json, sys
envelope = json.load(sys.stdin)
files = ["a.py", "b.py"]
print(json.dumps({
    "status": "ok",
    "output": {"files": files, "node": envelope["node"]},
    "artifacts": [{"id": "listing", "type": "report", "content": {"files": files}}],
}))
"""


def test_process_node_round_trip(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "node.py").write_text(NODE, encoding="utf-8")
    spec = load_spec_text(SPEC, path=str(tmp_path / "process.yaml"))
    store = RunStore(tmp_path / "runs")
    run_id = store.new_run_id("process")
    bus = EventBus(run_id, run_dir=store.run_dir(run_id))
    engine = WorkflowEngine(
        spec,
        options=RunOptions(workdir=workdir, runs_root=tmp_path / "runs", fake=True),
        bus=bus,
        provider=ScriptedLLM([]),
        budget=Budget(Pricing({}, version="test"), None),
    )
    state = engine.start({}, run_id=run_id)
    bus.close()

    assert state.terminal == "success", state.stop_detail
    assert state.outputs["scan"]["files"] == ["a.py", "b.py"]
    assert state.outputs["scan"]["node"] == "scan"
    assert state.artifacts["listing"].content == {"files": ["a.py", "b.py"]}

