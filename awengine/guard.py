"""Convergence and stopping rules.

The priority order is fixed and tested:

1. cheat detected                -> blocked
2. rounds/steps/attempts/cost    -> exhausted (reason names the limit)
3. progress stalled              -> stalled
4. goal true and streak reached  -> success
5. graph ended, no goal defined  -> no_op (or blocked when blockers remain)
6. graph ended, goal false       -> blocked

A verification failure is evidence, not an opinion: when the graph ends and
``verify.passed`` is false the terminal is ``blocked`` even if the model
claimed success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from .contracts import ProgressPoint, RunState, Terminal, canonical_json, utc_now
from .errors import classify_stop
from .expr import CompiledExpr, ExprError, compile_expr
from .spec import ConvergeSpec, WorkflowSpec


@dataclass
class ConvergeVerdict:
    terminal: Terminal
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)
    goal: bool | None = None
    streak: int = 0

    @property
    def failure_class(self) -> str:
        return classify_stop(self.terminal.value, self.reason)


def compiler(spec: WorkflowSpec) -> tuple[CompiledExpr | None, CompiledExpr]:
    goal = compile_expr(spec.converge.goal) if spec.converge.goal else None
    key = compile_expr(spec.converge.progress.key)
    return goal, key


def record_progress(state: RunState, spec: WorkflowSpec, env: Mapping[str, Any]) -> ProgressPoint | None:
    """Append this round's progress key/metric to the trace."""
    converge = spec.converge
    try:
        key_expr = compile_expr(converge.progress.key)
    except ExprError:
        return None
    raw, missing = key_expr.value(env)
    key_value: str | None
    if raw is None or missing:
        key_value = None
    elif isinstance(raw, str):
        key_value = raw
    else:
        key_value = canonical_json(raw)
    metric: float | None = None
    if converge.progress.metric:
        try:
            metric_expr = compile_expr(converge.progress.metric)
            metric_raw, _ = metric_expr.value(env)
            if isinstance(metric_raw, (int, float, Decimal)) and not isinstance(metric_raw, bool):
                metric = float(metric_raw)
        except (ExprError, TypeError, ValueError):
            metric = None
    point = ProgressPoint(
        round=state.runtime.round,
        key=converge.progress.key,
        key_value=key_value,
        metric=metric,
        at=utc_now(),
    )
    state.runtime.progress_trace = tuple(state.runtime.progress_trace) + (point,)
    return point


def progress_status(state: RunState, spec: WorkflowSpec) -> dict[str, Any]:
    converge = spec.converge
    trace = list(state.runtime.progress_trace)
    window = converge.progress.window
    need = window + 1
    status: dict[str, Any] = {
        "window": window,
        "points": len(trace),
        "stalled": False,
        "reason": "",
        "values": [point.key_value for point in trace[-need:]],
        "metrics": [point.metric for point in trace[-need:]],
    }
    if len(trace) < need:
        return status
    recent = trace[-need:]
    if converge.progress.metric:
        metrics = [point.metric for point in recent]
        if any(value is None for value in metrics):
            return status
        values = [float(value) for value in metrics if value is not None]
        # "never strictly decreases" over the window
        if all(values[index + 1] >= values[index] for index in range(len(values) - 1)):
            status["stalled"] = True
            status["reason"] = "metric_never_decreases"
            return status
        return status
    keys = [point.key_value for point in recent]
    if any(key is None for key in keys):
        return status
    if len(set(keys)) == 1:
        status["stalled"] = True
        status["reason"] = "key_unchanged"
    return status


def _limits(state: RunState, spec: WorkflowSpec) -> dict[str, Any]:
    return {
        "rounds": [state.runtime.counters.rounds, spec.converge.rounds.max],
        "steps": [state.runtime.counters.steps, spec.converge.steps.max],
        "attempts": [state.runtime.counters.attempts, spec.converge.attempts.max],
        "cost": [
            str(state.runtime.counters.cost_used),
            str(spec.converge.cost.max_cny) if spec.converge.cost.max_cny is not None else None,
        ],
    }


def _base_detail(state: RunState, spec: WorkflowSpec) -> dict[str, Any]:
    return {
        "counters": {
            "rounds": state.runtime.counters.rounds,
            "steps": state.runtime.counters.steps,
            "attempts": state.runtime.counters.attempts,
            "cost_used": str(state.runtime.counters.cost_used),
        },
        "limits": _limits(state, spec),
        "streak": state.runtime.streak,
        "required_streak": spec.converge.streak,
        "round": state.runtime.round,
        "workflow": spec.name,
    }


def check_limits(state: RunState, spec: WorkflowSpec) -> ConvergeVerdict | None:
    """Cheap checks that can fire before starting a node."""
    converge = spec.converge
    counters = state.runtime.counters
    detail = _base_detail(state, spec)

    if state.runtime.flags.get("cheat"):
        return ConvergeVerdict(Terminal.BLOCKED, "cheat", detail, goal=False)
    if counters.rounds > converge.rounds.max:
        return ConvergeVerdict(Terminal.EXHAUSTED, "rounds", detail, goal=False)
    if counters.steps > converge.steps.max:
        return ConvergeVerdict(Terminal.EXHAUSTED, "steps", detail, goal=False)
    if counters.attempts > converge.attempts.max:
        return ConvergeVerdict(Terminal.EXHAUSTED, "attempts", detail, goal=False)
    if converge.cost.max_cny is not None and counters.cost_used > converge.cost.max_cny:
        return ConvergeVerdict(Terminal.EXHAUSTED, "cost", detail, goal=False)
    return None


def evaluate_round(
    state: RunState,
    spec: WorkflowSpec,
    *,
    goal: bool | None,
    at_end: bool,
    verify_failed: bool,
    blockers: int,
) -> ConvergeVerdict | None:
    """Called exactly once per completed round, after progress is recorded."""
    converge = spec.converge
    limit = check_limits(state, spec)
    if limit is not None:
        return limit

    detail = _base_detail(state, spec)
    detail.update(
        {
            "goal": goal,
            "goal_expression": converge.goal,
            "at_end": at_end,
            "verify_failed": verify_failed,
            "blockers": blockers,
        }
    )
    progress = progress_status(state, spec)
    detail["progress"] = progress

    if progress["stalled"]:
        detail["reason"] = progress["reason"]
        return ConvergeVerdict(Terminal.STALLED, "no_progress", detail, goal=goal)

    if goal:
        state.runtime.streak += 1
    else:
        state.runtime.streak = 0
    detail["streak"] = state.runtime.streak

    if goal and state.runtime.counters.rounds < converge.rounds.min:
        # rounds.min forces another pass even when the goal is already true
        return None if not at_end else None

    if goal and state.runtime.streak < converge.streak:
        if at_end:
            detail["reason"] = "streak_unmet"
            return ConvergeVerdict(Terminal.BLOCKED, "streak_unmet", detail, goal=goal)
        return None

    if goal and state.runtime.streak >= converge.streak and state.runtime.counters.rounds >= converge.rounds.min:
        return ConvergeVerdict(Terminal.SUCCESS, "goal_met", detail, goal=True, streak=state.runtime.streak)

    if not at_end:
        return None

    if verify_failed:
        detail["reason"] = "verify_failed"
        return ConvergeVerdict(Terminal.BLOCKED, "verify_failed", detail, goal=goal)

    if converge.goal is None:
        if blockers > 0:
            detail["reason"] = "blockers"
            return ConvergeVerdict(Terminal.BLOCKED, "blockers", detail, goal=goal)
        return ConvergeVerdict(Terminal.NO_OP, "no_goal", detail, goal=goal)

    detail["reason"] = "goal_not_met"
    return ConvergeVerdict(Terminal.BLOCKED, "goal_not_met", detail, goal=goal)


def should_escalate(state: RunState, spec: WorkflowSpec, *, goal: bool | None) -> bool:
    rule = spec.converge.escalate
    if rule.max_times <= 0 or rule.after_round <= 0:
        return False
    if state.runtime.round < rule.after_round:
        return False
    if goal:
        return False
    return len(state.runtime.escalations) < rule.max_times


def stop_decision_event(
    verdict: ConvergeVerdict,
    *,
    state: RunState,
    spec: WorkflowSpec,
) -> dict[str, Any]:
    detail = dict(verdict.detail)
    detail.update(
        {
            "terminal": verdict.terminal.value,
            "reason": verdict.reason,
            "failure_class": verdict.failure_class,
            "counters": {
                "rounds": state.runtime.counters.rounds,
                "steps": state.runtime.counters.steps,
                "attempts": state.runtime.counters.attempts,
                "cost_used": str(state.runtime.counters.cost_used),
            },
            "streak": state.runtime.streak,
            "goal": verdict.goal,
        }
    )
    return detail
