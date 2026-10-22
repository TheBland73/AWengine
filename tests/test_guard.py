"""Convergence guard unit tests."""

from __future__ import annotations

from awengine.contracts import ProgressPoint, RunState, Runtime
from awengine.guard import evaluate_round, progress_status, record_progress
from awengine.loader import load_spec_text


def make_spec(
    goal: str | None = "review.blockers == 0",
    rounds_max: int = 2,
    rounds_min: int = 1,
    progress: str = "",
):
    goal_line = f'\n  goal: "{goal}"' if goal is not None else ""
    progress_block = f"\n  progress: {progress}" if progress else ""
    text = f"""
name: guard
version: 1
nodes:
  a: {{type: builtin, impl: noop}}
edges:
  - {{src: a, dst: END}}
converge:{goal_line}
  streak: 1
  rounds: {{min: {rounds_min}, max: {rounds_max}}}{progress_block}
"""
    return load_spec_text(text)


def make_state(spec, *, rounds: int = 1, streak: int = 0) -> RunState:
    return RunState(
        run_id="guard-run",
        workflow=spec.name,
        spec_hash=spec.spec_hash,
        runtime=Runtime(round=rounds, counters={"rounds": rounds, "steps": 1}),
    )


def test_goal_met_is_success_at_round_end() -> None:
    spec = make_spec()
    state = make_state(spec)
    verdict = evaluate_round(state, spec, goal=True, at_end=True, verify_failed=False, blockers=0)
    assert verdict is not None
    assert verdict.terminal == "success"
    assert verdict.reason == "goal_met"


def test_verify_failure_blocks_when_goal_is_false() -> None:
    spec = make_spec()
    state = make_state(spec)
    verdict = evaluate_round(state, spec, goal=False, at_end=True, verify_failed=True, blockers=0)
    assert verdict is not None
    assert verdict.terminal == "blocked"
    assert verdict.reason == "verify_failed"


def test_blockers_block_a_goal_less_workflow() -> None:
    spec = make_spec(goal=None)
    state = make_state(spec)
    verdict = evaluate_round(state, spec, goal=None, at_end=True, verify_failed=False, blockers=2)
    assert verdict is not None
    assert verdict.reason == "blockers"


def test_round_max_exhaustion_is_reported() -> None:
    spec = make_spec(rounds_max=2)
    state = make_state(spec, rounds=3)
    verdict = evaluate_round(state, spec, goal=False, at_end=False, verify_failed=False, blockers=0)
    assert verdict is not None
    assert verdict.terminal == "exhausted"
    assert verdict.reason == "rounds"


def test_progress_trace_detects_a_stall() -> None:
    spec = make_spec(rounds_max=5, progress="{key: review.blockers, window: 2}")
    state = make_state(spec, rounds=3)
    state.runtime.progress_trace = (
        ProgressPoint(round=1, key_value="2", metric=None),
        ProgressPoint(round=2, key_value="2", metric=None),
    )
    record_progress(state, spec, {"review": {"blockers": 2}})
    status = progress_status(state, spec)
    assert status["stalled"] is True
    assert status["reason"] == "key_unchanged"


def test_progress_metric_must_strictly_improve() -> None:
    spec = make_spec(rounds_max=5, progress="{key: cost, metric: usage.cost, window: 1}")
    state = make_state(spec, rounds=2)
    state.runtime.progress_trace = (ProgressPoint(round=1, key_value="a", metric=0.5),)
    point = record_progress(state, spec, {"usage": {"cost": 0.6}})
    assert point is not None
    status = progress_status(state, spec)
    assert status["stalled"] is True
