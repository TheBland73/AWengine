"""Offline evaluation helpers: sequence fidelity, messages, retries, cost.

The coursework asks for measurable behaviour, so ``metrics_from_run`` is shared
by tests and by ``scripts/eval.py``.  It never calls a model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .contracts import RunState
from .spec import WorkflowSpec


@dataclass
class Metrics:
    run_id: str
    workflow: str
    terminal: str
    reason: str
    rounds: int
    steps: int
    attempts: int
    retries: int
    escalations: int
    tool_calls: int
    denied_tool_calls: int
    messages: int
    artifacts: int
    cost_used: str
    sequence_coverage: float
    edge_accuracy: float
    invalid_messages: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def metrics_from_run(state: RunState, spec: WorkflowSpec, events: list[dict[str, Any]] | None = None) -> Metrics:
    events = events or []
    allowed = {(edge.src, "END" if edge.dst == "END" else edge.dst) for edge in spec.edges}
    allowed.update({("END", "END")})
    observed: list[tuple[str, str]] = []
    for event in events:
        if event.get("type") == "cursor":
            data = event.get("data") or {}
            observed.append((str(event.get("node")), str(data.get("next"))))
    valid = sum(1 for src, dst in observed if (src, dst) in allowed)
    expected_nodes = {node.name for node in spec.nodes}
    visited = {entry.node for entry in state.history}
    invalid_messages = 0
    for message in state.messages:
        if message.src not in expected_nodes or message.dst not in expected_nodes:
            invalid_messages += 1
        for artifact_id in message.artifacts:
            if artifact_id not in state.artifacts:
                invalid_messages += 1
    retries = sum(1 for event in events if event.get("type") == "node_retry")
    tool_calls = sum(1 for event in events if event.get("type") == "tool_call")
    denied = sum(
        1
        for event in events
        if event.get("type") == "tool_call" and (event.get("data") or {}).get("denied")
    )
    return Metrics(
        run_id=state.run_id,
        workflow=spec.name,
        terminal=str(state.terminal or "running"),
        reason=str(state.stop_reason or ""),
        rounds=state.runtime.counters.rounds,
        steps=state.runtime.counters.steps,
        attempts=state.runtime.counters.attempts,
        retries=retries,
        escalations=len(state.runtime.escalations),
        tool_calls=tool_calls,
        denied_tool_calls=denied,
        messages=len(state.messages),
        artifacts=len(state.artifacts),
        cost_used=str(state.runtime.counters.cost_used),
        sequence_coverage=round(len(visited & expected_nodes) / max(1, len(expected_nodes)), 4),
        edge_accuracy=round(valid / max(1, len(observed)), 4),
        invalid_messages=invalid_messages,
    )


def compare(baseline: Metrics, variant: Metrics) -> dict[str, Any]:
    keys = ("terminal", "rounds", "steps", "attempts", "cost_used", "messages", "edge_accuracy")
    return {
        "baseline": baseline.to_dict(),
        "variant": variant.to_dict(),
        "delta": {
            key: (
                f"{getattr(baseline, key)} -> {getattr(variant, key)}"
                if getattr(baseline, key) != getattr(variant, key)
                else "same"
            )
            for key in keys
        },
    }


def render_metrics(metrics: Metrics) -> str:
    rows = [
        ("workflow", metrics.workflow),
        ("terminal", f"{metrics.terminal} ({metrics.reason})"),
        ("rounds / steps", f"{metrics.rounds} / {metrics.steps}"),
        ("attempts / retries", f"{metrics.attempts} / {metrics.retries}"),
        ("escalations", metrics.escalations),
        ("tool calls", f"{metrics.tool_calls} ({metrics.denied_tool_calls} denied)"),
        ("messages / artifacts", f"{metrics.messages} / {metrics.artifacts}"),
        ("cost", metrics.cost_used),
        ("sequence coverage", f"{metrics.sequence_coverage:.0%}"),
        ("edge accuracy", f"{metrics.edge_accuracy:.0%}"),
        ("invalid messages", metrics.invalid_messages),
    ]
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label:<{width}}  {value}" for label, value in rows) + "\n"
