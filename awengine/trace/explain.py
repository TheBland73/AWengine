"""Explain a finished (or interrupted) run in human terms."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts import RunState
from ..events import read_events
from ..spec import WorkflowSpec


def _find(events: list[dict[str, Any]], etype: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") == etype:
            return event
    return None


def explain_state(state: RunState, spec: WorkflowSpec, events: list[dict[str, Any]]) -> dict[str, Any]:
    stop = _find(events, "stop_decision")
    converge = _find(events, "converge_eval")
    detail = dict(stop.get("data") or {}) if stop else dict(state.stop_detail or {})
    counters = detail.get("counters") or {
        "rounds": state.runtime.counters.rounds,
        "steps": state.runtime.counters.steps,
        "attempts": state.runtime.counters.attempts,
        "cost_used": str(state.runtime.counters.cost_used),
    }
    goal = detail.get("goal")
    if goal is None and converge:
        goal = (converge.get("data") or {}).get("goal")
    return {
        "run_id": state.run_id,
        "workflow": spec.name,
        "terminal": state.terminal or "interrupted",
        "reason": state.stop_reason or detail.get("reason"),
        "failure_class": state.failure_class or detail.get("failure_class") or "",
        "goal_expression": spec.converge.goal,
        "goal": goal,
        "streak": state.runtime.streak,
        "required_streak": spec.converge.streak,
        "counters": counters,
        "limits": {
            "rounds": spec.converge.rounds.model_dump(),
            "steps": spec.converge.steps.model_dump(),
            "attempts": spec.converge.attempts.model_dump(),
            "cost": spec.converge.cost.model_dump(),
        },
        "verify": state.verify.model_dump(mode="json") if state.verify else None,
        "last_events": [
            {"seq": event.get("seq"), "type": event.get("type"), "node": event.get("node"), "round": event.get("round")}
            for event in events[-8:]
        ],
        "suggestions": _suggestions(state, spec, goal),
    }


def _suggestions(state: RunState, spec: WorkflowSpec, goal: Any) -> list[str]:
    tips: list[str] = []
    terminal = state.terminal
    if terminal == "success":
        return ["The run met its goal and the deterministic verify gate agreed."]
    if terminal == "blocked" and state.stop_reason == "verify_failed":
        tips.append("Read the verify stderr tail in report.md; the model's claim is not evidence.")
        tips.append("If the public command cannot catch the bug, add a holdout (verify.level: 3).")
    if terminal == "exhausted" and state.stop_reason == "rounds":
        tips.append("Raise converge.rounds.max or make the reviewer report fewer false blockers.")
    if terminal == "stalled":
        tips.append("The progress key did not change across rounds. Tighten the reviewer rubric or progress.key.")
    if terminal == "blocked" and state.stop_reason in ("node_failed", "node_missing"):
        tips.append("Inspect the failed node's event and its contract; add retry or a recovery edge.")
    if goal is False and state.verify is not None and state.verify.passed:
        tips.append("Verify passed but the declarative goal is still false; inspect the goal expression and reviewers.")
    if not tips:
        tips.append("Open report.md for the per-node table and replay.html for the event timeline.")
    tips.append(f"Reproduce: awf replay {state.run_id}")
    return tips


def render_explain(info: dict[str, Any]) -> str:
    def line(label: str, value: Any) -> str:
        return f"{label:<18}{value}"

    out = [
        line("run", info["run_id"]),
        line("workflow", info["workflow"]),
        line("terminal", f"{info['terminal']}  ({info['reason'] or '-'})"),
        line("failure class", info["failure_class"] or "-"),
        line("goal", f"{info['goal_expression'] or '-'}  ->  {info['goal']}"),
        line("streak", f"{info['streak']} / {info['required_streak']}"),
        line(
            "counters",
            f"rounds {info['counters'].get('rounds')}, steps {info['counters'].get('steps')}, "
            f"attempts {info['counters'].get('attempts')}, cost {info['counters'].get('cost_used')}",
        ),
    ]
    verify = info.get("verify")
    if verify:
        out.append(
            line(
                "verify",
                f"level {verify.get('level')} exit {verify.get('exit_code')} "
                f"{'PASS' if verify.get('passed') else 'FAIL'} holdout={verify.get('holdout_passed')}",
            )
        )
    out.append("")
    out.append("Why:")
    out.append(f"  terminal={info['terminal']}, reason={info['reason']}, goal={info['goal']}")
    if verify and not verify.get("passed") and verify.get("stderr_tail"):
        tail = verify["stderr_tail"].strip().splitlines()[-5:]
        for entry in tail:
            out.append(f"  | {entry}")
    out.append("")
    out.append("Suggestions:")
    for tip in info["suggestions"]:
        out.append(f"  - {tip}")
    return "\n".join(out) + "\n"


def explain_run(run_dir: Path, state: RunState, spec: WorkflowSpec) -> tuple[dict[str, Any], str]:
    events = read_events(Path(run_dir) / "events.jsonl")
    info = explain_state(state, spec, events)
    return info, render_explain(info)
