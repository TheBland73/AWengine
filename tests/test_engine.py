"""End-to-end engine tests using the offline cassette provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from awengine.budget import Budget, load_pricing
from awengine.checkpoint import RunStore
from awengine.engine import RunOptions, WorkflowEngine
from awengine.events import EventBus
from awengine.llm.fake import CassetteStore, FakeLLM, ScriptedLLM, response
from awengine.loader import load_spec


def build_engine(
    spec_path: Path,
    tmp_path: Path,
    *,
    workdir: Path,
    cassette_dir: Path,
    project_root: Path,
    fake: bool = True,
    extra: dict | None = None,
):
    spec = load_spec(spec_path)
    workdir.mkdir(parents=True, exist_ok=True)
    runs_root = tmp_path / "runs"
    run_id = "test-run"
    bus = EventBus(run_id, run_dir=runs_root / run_id)
    budget = Budget(load_pricing(project_root / "pricing.toml"), None)
    options = RunOptions(
        workdir=workdir,
        runs_root=runs_root,
        fake=fake,
        project_dir=project_root,
        cassette_dir=cassette_dir,
        extra=extra or {},
    )
    provider = FakeLLM(CassetteStore(cassette_dir))
    engine = WorkflowEngine(spec, options=options, bus=bus, provider=provider, budget=budget)
    return engine, bus, runs_root / run_id


def test_hello_workflow_runs_offline(
    tmp_path: Path,
    workflow_dir: Path,
    cassette_dir: Path,
    fixture_workdir: Path,
    project_root: Path,
) -> None:
    engine, bus, run_dir = build_engine(
        workflow_dir / "hello.yaml",
        tmp_path,
        workdir=fixture_workdir,
        cassette_dir=cassette_dir,
        project_root=project_root,
    )
    try:
        state = engine.start({"name": "world"}, run_id="test-run")
    finally:
        bus.close()
    assert state.terminal == "success"
    assert state.stop_reason == "goal_met"
    assert (run_dir / "report.md").is_file()
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "replay.html").is_file()
    assert (run_dir / "flow.mmd").is_file()
    assert (run_dir / "sequence.mmd").is_file()


def test_bugfix_workflow_records_handoffs_and_review(
    tmp_path: Path,
    workflow_dir: Path,
    cassette_dir: Path,
    fixture_workdir: Path,
    project_root: Path,
) -> None:
    engine, bus, run_dir = build_engine(
        workflow_dir / "bugfix.yaml",
        tmp_path,
        workdir=fixture_workdir,
        cassette_dir=cassette_dir,
        project_root=project_root,
    )
    try:
        state = engine.start({}, run_id="test-run")
    finally:
        bus.close()
    assert state.terminal == "success", state.stop_detail
    assert state.verify is not None and state.verify.passed and state.verify.holdout_passed
    handoffs = {(message.src, message.dst) for message in state.messages}
    assert ("coder", "apply") in handoffs
    assert ("coder", "review") in handoffs
    assert "review" in state.artifacts
    assert "patch" in state.artifacts
    assert [entry.node for entry in state.history][-2:] == ["apply_fix", "review_final"]
    assert {"fix", "apply_fix", "review_final"}.issubset({entry.node for entry in state.history})
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Collaboration timeline" in report
    assert "coder" in report
    assert "R1-1" in report  # the blocker from the first review pass
    assert "| resolved |" in report
    assert "mul()" in report
    assert "review_final" in report


def test_inline_agents_workflow_runs_offline(
    tmp_path: Path,
    workflow_dir: Path,
    cassette_dir: Path,
    docsync_workdir: Path,
    project_root: Path,
) -> None:
    spec = load_spec(workflow_dir / "docsync.yaml")
    assert spec.node("analyst").impl == ""
    assert spec.node("writer").impl == ""
    assert spec.node("editor").impl == ""
    engine, bus, run_dir = build_engine(
        workflow_dir / "docsync.yaml",
        tmp_path,
        workdir=docsync_workdir,
        cassette_dir=cassette_dir,
        project_root=project_root,
    )
    try:
        state = engine.start({}, run_id="test-run")
    finally:
        bus.close()
    assert state.terminal == "success", state.stop_detail
    assert state.verify is not None and state.verify.passed and state.verify.holdout_passed
    assert [entry.node for entry in state.history] == ["plan", "analyst", "writer", "write", "editor"]
    assert "brief" in state.artifacts
    assert "doc" in state.artifacts
    notes = (run_dir / "workdir" / "NOTES.md").read_text(encoding="utf-8")
    assert "AWEngine" in notes and "Collaboration" in notes


class _InterruptingLLM:
    def complete(self, *args, **kwargs):
        raise KeyboardInterrupt


def test_resume_continues_from_cursor(
    tmp_path: Path,
    workflow_dir: Path,
    cassette_dir: Path,
    fixture_workdir: Path,
    project_root: Path,
) -> None:
    spec = load_spec(workflow_dir / "hello.yaml")
    runs_root = tmp_path / "runs"
    run_id = "resume-run"
    budget = Budget(load_pricing(project_root / "pricing.toml"), None)
    options = RunOptions(
        workdir=fixture_workdir,
        runs_root=runs_root,
        fake=True,
        project_dir=project_root,
        cassette_dir=cassette_dir,
    )
    bus = EventBus(run_id, run_dir=runs_root / run_id)
    engine = WorkflowEngine(spec, options=options, bus=bus, provider=_InterruptingLLM(), budget=budget)
    try:
        with pytest.raises(KeyboardInterrupt):
            engine.start({"name": "world"}, run_id=run_id)
    finally:
        bus.close()
    store = RunStore(runs_root)
    state = store.load(run_id)
    assert state.terminal is None
    assert state.cursor == "greet"
    assert "plan" in state.outputs

    bus2 = EventBus(run_id, run_dir=store.run_dir(run_id))
    engine2 = WorkflowEngine(
        spec,
        options=options,
        bus=bus2,
        provider=ScriptedLLM([response('{"greeting": "hi world", "words": 2}')]),
        budget=budget,
    )
    try:
        resumed = engine2.resume(state)
    finally:
        bus2.close()
    assert resumed.terminal == "success", resumed.stop_detail
    assert resumed.stop_reason == "goal_met"
    assert resumed.outputs["greet"]["words"] == 2


def test_visible_pass_but_hidden_failure_is_falsepass(
    tmp_path: Path, project_root: Path, cassette_dir: Path
) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    holdout_dir = workflows / "hold"
    holdout_dir.mkdir()
    (holdout_dir / "test_hidden.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    spec_path = workflows / "fp.yaml"
    spec_path.write_text(
        """
name: fp
version: 1
nodes:
  a: {type: builtin, impl: noop}
edges:
  - {src: a, dst: END}
converge:
  goal: verify.passed
verify:
  level: 2
  cmd: "python -c \\"pass\\""
  holdout: hold/test_hidden.py
""",
        encoding="utf-8",
    )
    engine, bus, _ = build_engine(
        spec_path,
        tmp_path,
        workdir=tmp_path / "work",
        cassette_dir=cassette_dir,
        project_root=project_root,
    )
    try:
        state = engine.start({}, run_id="test-run")
    finally:
        bus.close()
    assert state.terminal == "blocked"
    assert state.verify is not None
    assert state.verify.failure_class == "F-VERIFY-FALSEPASS"
    assert state.failure_class == "F-VERIFY-FALSEPASS"


def test_red_before_gate_blocks_a_verify_that_passes_untouched(
    tmp_path: Path, project_root: Path, cassette_dir: Path
) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    spec_path = workflows / "weak.yaml"
    spec_path.write_text(
        """
name: weak
version: 1
nodes:
  a: {type: builtin, impl: noop}
edges:
  - {src: a, dst: END}
converge: {goal: verify.passed}
verify:
  level: 1
  cmd: "python -c \\"pass\\""
  red_before: true
""",
        encoding="utf-8",
    )
    engine, bus, _ = build_engine(
        spec_path,
        tmp_path,
        workdir=tmp_path / "work",
        cassette_dir=cassette_dir,
        project_root=project_root,
    )
    try:
        state = engine.start({}, run_id="test-run")
    finally:
        bus.close()
    assert state.terminal == "blocked"
    assert state.stop_reason == "verify_weak"


def test_run_store_roundtrip(tmp_path: Path, project_root: Path) -> None:
    store = RunStore(tmp_path / "runs")
    state = store.create(
        workflow="demo",
        spec_hash="abc",
        inputs={"k": "v"},
        run_id="roundtrip",
        workdir=str(project_root),
    )
    store.save(state)
    loaded = store.load("roundtrip")
    assert loaded.run_id == "roundtrip"
    assert loaded.inputs == {"k": "v"}
    assert store.manifest("roundtrip")["workflow"] == "demo"
    assert [item.run_id for item in store.list_runs()] == ["roundtrip"]
