"""Markdown + JSON report generation.

The report answers, in order: what was the verdict, why did it stop, what did
each agent do, which issues remain, and how much did it cost.  It is generated
from the persisted state and event stream only, so it can be regenerated after
the fact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import __version__
from ..contracts import RunState, canonical_json
from ..events import read_events
from ..spec import WorkflowSpec
from .mermaid import write_mermaid
from .replay import write_replay

FAILURE_HINTS = {
    "F-VERIFY-FALSEPASS": "The model's claim and the command disagree. Trust the command; inspect stderr and the holdout.",
    "F-VERIFY-WEAK": "The gate cannot distinguish pass from fail. Add a holdout or set red_before: true.",
    "F-STOP-EARLY": "The run stopped before its goal. Raise rounds.max or add a loop back to the fixer.",
    "F-STOP-LATE": "Rounds were spent without progress. Tighten the progress key or the reviewer rubric.",
    "F-COORD-HANDOFF": "A node failed or broke its output contract. Check the node_end error and the artifact schema.",
    "F-CONTEXT-OVERFLOW": "Trim context: narrow context.sources or raise context.max_tokens.",
    "F-SPEC-AMBIG": "No goal is defined for a workflow without verify; add converge.goal or verify.cmd.",
}


def _fmt_cost(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "¥0"
    return f"¥{amount:.4f}" if 0 < amount < 0.01 else f"¥{amount:.2f}"


def _issues(state: RunState) -> list[dict[str, Any]]:
    """Every review pass, not just the latest artifact.

    Artifacts are keyed by id, so a second review overwrites the first one.
    Node outputs are keyed by node name, which keeps ``review`` and
    ``review_final`` separate; use them first so the report can show the
    blocker that triggered the fix and the later clean pass.
    """
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    rounds = {entry.node: entry.round for entry in reversed(state.history)}
    order = {entry.node: index for index, entry in enumerate(state.history)}
    clean_after: list[int] = []
    for node, output in state.outputs.items():
        if isinstance(output, dict) and output.get("blockers") == 0:
            clean_after.append(order.get(node, -1))
    for node, output in state.outputs.items():
        if not isinstance(output, dict):
            continue
        raw = output.get("issues")
        if not isinstance(raw, list):
            continue
        for issue in raw:
            if not isinstance(issue, dict):
                continue
            key = (
                str(issue.get("id", "")),
                str(issue.get("file", "")),
                str(issue.get("line", "")),
                str(issue.get("message", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            position = order.get(node, -1)
            found.append(
                {
                    **issue,
                    "_artifact": issue.get("id", "review"),
                    "_producer": node,
                    "_round": rounds.get(node),
                    "_resolved": any(index > position for index in clean_after),
                }
            )
    for artifact in state.artifacts.values():
        raw = artifact.content.get("issues")
        if not isinstance(raw, list):
            continue
        for issue in raw:
            if not isinstance(issue, dict):
                continue
            key = (
                str(issue.get("id", "")),
                str(issue.get("file", "")),
                str(issue.get("line", "")),
                str(issue.get("message", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    **issue,
                    "_artifact": artifact.id,
                    "_producer": artifact.producer,
                    "_round": artifact.round,
                    "_resolved": False,
                }
            )
    return found


def _node_rows(state: RunState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in state.history:
        usage = entry.usage
        rows.append(
            {
                "round": entry.round,
                "node": entry.node,
                "status": entry.status,
                "duration_ms": int((entry.ended_at - entry.started_at).total_seconds() * 1000),
                "tokens": (usage.prompt_tokens + usage.completion_tokens) if usage else 0,
                "cost": str(usage.cost) if usage else "0",
                "artifacts": list(entry.artifact_ids),
                "error": entry.error or "",
            }
        )
    return rows


def _stop_detail(state: RunState, events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("type") == "stop_decision":
            return dict(event.get("data") or {})
    return dict(state.stop_detail or {})


def _why_stopped(state: RunState, events: list[dict[str, Any]]) -> list[str]:
    detail = _stop_detail(state, events)
    counters = detail.get("counters") or {
        "rounds": state.runtime.counters.rounds,
        "steps": state.runtime.counters.steps,
        "attempts": state.runtime.counters.attempts,
        "cost_used": str(state.runtime.counters.cost_used),
    }
    lines = [
        f"- **Terminal**: `{state.terminal}` (reason: `{state.stop_reason or detail.get('reason', '-')}`)",
        f"- **Failure class**: `{state.failure_class or detail.get('failure_class') or '-'}`",
        f"- **Counters**: rounds {counters.get('rounds')}, steps {counters.get('steps')}, "
        f"attempts {counters.get('attempts')}, cost {_fmt_cost(counters.get('cost_used'))}",
    ]
    if detail.get("goal_expression"):
        lines.append(
            f"- **Goal**: `{detail.get('goal_expression')}` -> `{detail.get('goal')}`; "
            f"streak {detail.get('streak', state.runtime.streak)} (needs {detail.get('required_streak', '-')})"
        )
    if state.verify is not None:
        lines.append(
            f"- **Verify**: `{state.verify.command}` exit {state.verify.exit_code}, "
            f"holdout {state.verify.holdout_passed}, level {state.verify.level}"
        )
        if not state.verify.passed and state.verify.stderr_tail:
            tail = state.verify.stderr_tail.strip().splitlines()[-6:]
            lines.append("- **Verify stderr (tail)**:")
            lines.extend(f"  `{line}`" for line in tail)
    hint = FAILURE_HINTS.get(state.failure_class)
    if hint:
        lines.append(f"- **Hint**: {hint}")
    return lines


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(cell(item) for item in row) + " |")
    return "\n".join(lines)


def build_summary(state: RunState, spec: WorkflowSpec, events: list[dict[str, Any]]) -> dict[str, Any]:
    detail = _stop_detail(state, events)
    return {
        "run_id": state.run_id,
        "workflow": spec.name,
        "workflow_version": spec.version,
        "spec_hash": spec.spec_hash,
        "terminal": state.terminal,
        "reason": state.stop_reason,
        "failure_class": state.failure_class,
        "goal": detail.get("goal"),
        "counters": {
            "rounds": state.runtime.counters.rounds,
            "steps": state.runtime.counters.steps,
            "attempts": state.runtime.counters.attempts,
            "cost_used": str(state.runtime.counters.cost_used),
        },
        "usage": state.usage.model_dump(mode="json"),
        "verify": state.verify.model_dump(mode="json") if state.verify else None,
        "nodes": _node_rows(state),
        "issues": _issues(state),
        "messages": [message.model_dump(mode="json") for message in state.messages],
        "progress": [point.model_dump(mode="json") for point in state.runtime.progress_trace],
        "escalations": [record.model_dump(mode="json") for record in state.runtime.escalations],
        "artifacts": [
            {"id": artifact.id, "type": artifact.type, "producer": artifact.producer, "digest": artifact.digest}
            for artifact in state.artifacts.values()
        ],
    }


def generate_report(run_dir: Path, state: RunState, spec: WorkflowSpec) -> dict[str, str]:
    run_dir = Path(run_dir)
    events = read_events(run_dir / "events.jsonl")
    summary = build_summary(state, spec, events)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8", newline="\n"
    )
    views = write_mermaid(run_dir, spec, events)
    write_replay(run_dir, state, spec, events)

    rows = _node_rows(state)
    issues = _issues(state)
    lines: list[str] = []
    lines.append(f"# Run report: `{state.run_id}`")
    lines.append("")
    lines.append(f"**Workflow** `{spec.name}` v{spec.version} - **spec hash** `{spec.spec_hash[:16]}` - "
                 f"**engine** awengine {__version__}")
    lines.append("")
    terminal = state.terminal or "running"
    icon = {"success": "PASS", "no_op": "NO-OP", "blocked": "BLOCKED", "stalled": "STALLED", "exhausted": "EXHAUSTED"}.get(
        terminal, terminal.upper()
    )
    lines.append(f"## Verdict: {icon}")
    lines.append("")
    if state.verify is not None:
        l1 = "PASS" if state.verify.passed else "FAIL"
        lines.append(f"- **Verification level**: L{state.verify.level}")
        lines.append(f"- **Verify command** (`{state.verify.command}`): **{l1}** (exit {state.verify.exit_code})")
        holdout = state.verify.holdout_passed
        lines.append(f"- **Holdout**: {'passed' if holdout else ('failed' if holdout is False else 'not configured')}")
        lines.append(f"- **Red before**: {state.verify.red_before_failed}")
        claims = [
            f"{name}: {output.get('verdict')}"
            for name, output in state.outputs.items()
            if isinstance(output, dict) and output.get("verdict")
        ]
        lines.append(f"- **Agent verdicts (advisory)**: {', '.join(claims) if claims else 'none'}")
    else:
        lines.append("- No `verify` command configured; the terminal reflects the declarative goal only.")
    lines.append("")
    lines.append("## Why it stopped")
    lines.append("")
    lines.extend(_why_stopped(state, events))
    lines.append("")
    lines.append("## Flow")
    lines.append("")
    lines.append("```mermaid")
    lines.append(views["flow"].strip())
    lines.append("```")
    lines.append("")
    lines.append("## Collaboration timeline")
    lines.append("")
    if state.messages:
        lines.append(
            _md_table(
                ["src", "dst", "intent", "artifacts", "round"],
                [
                    [message.src, message.dst, message.intent, ", ".join(message.artifacts), message.round]
                    for message in state.messages
                ],
            )
        )
    else:
        lines.append("No artifact handoffs were recorded.")
    lines.append("")
    lines.append("## Nodes")
    lines.append("")
    if rows:
        lines.append(
            _md_table(
                ["round", "node", "status", "duration (ms)", "tokens", "cost", "artifacts", "error"],
                [
                    [row["round"], row["node"], row["status"], row["duration_ms"], row["tokens"],
                     _fmt_cost(row["cost"]), ", ".join(row["artifacts"]), row["error"]]
                    for row in rows
                ],
            )
        )
    else:
        lines.append("No node executions recorded.")
    lines.append("")
    lines.append("## Issues")
    lines.append("")
    if issues:
        lines.append(
            _md_table(
                ["round", "status", "severity", "id", "file:line", "message", "evidence", "producer"],
                [
                    [
                        issue.get("_round", ""),
                        "resolved" if issue.get("_resolved") else "open",
                        issue.get("severity", ""),
                        issue.get("id", ""),
                        f"{issue.get('file', '')}:{issue.get('line', '')}",
                        issue.get("message", ""),
                        "; ".join(
                            f"{item.get('path')}:{item.get('line')} `{item.get('quote')}`"
                            for item in issue.get("evidence") or []
                        ),
                        issue.get("_producer", ""),
                    ]
                    for issue in issues
                ],
            )
        )
        blockers = sum(1 for issue in issues if issue.get("severity") == "blocker")
        lines.append("")
        lines.append(
            f"{len(issues)} issue(s), {blockers} blocker(s); "
            f"{sum(1 for issue in issues if issue.get('_resolved'))} resolved by a later review."
        )
    else:
        lines.append("No issues were reported.")
    lines.append("")
    lines.append("## Progress")
    lines.append("")
    if state.runtime.progress_trace:
        lines.append(
            _md_table(
                ["round", "key", "value", "metric"],
                [[point.round, point.key, point.key_value, point.metric] for point in state.runtime.progress_trace],
            )
        )
    else:
        lines.append("No round completed.")
    if state.runtime.escalations:
        lines.append("")
        lines.append("### Escalations")
        lines.append(
            _md_table(
                ["round", "from", "to", "temperature", "rubric"],
                [
                    [record.round, record.from_model, record.to_model, record.temperature, record.rubric]
                    for record in state.runtime.escalations
                ],
            )
        )
    lines.append("")
    lines.append("## Cost")
    lines.append("")
    by_model: dict[str, dict[str, Any]] = {}
    for entry in state.history:
        usage = entry.usage
        if usage is None or not usage.model:
            continue
        bucket = by_model.setdefault(usage.model, {"calls": 0, "prompt": 0, "completion": 0, "cost": 0.0})
        bucket["calls"] += usage.calls or 1
        bucket["prompt"] += usage.prompt_tokens
        bucket["completion"] += usage.completion_tokens
        bucket["cost"] += float(usage.cost or 0)
    if by_model:
        lines.append(
            _md_table(
                ["model", "calls", "prompt tokens", "completion tokens", "cost"],
                [[model, data["calls"], data["prompt"], data["completion"], _fmt_cost(data["cost"])] for model, data in by_model.items()],
            )
        )
    else:
        lines.append("No model calls (offline fake or deterministic nodes only).")
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    if state.artifacts:
        lines.append(
            _md_table(
                ["id", "type", "producer", "round", "digest"],
                [[artifact.id, artifact.type, artifact.producer, artifact.round, artifact.digest[:16]]
                 for artifact in state.artifacts.values()],
            )
        )
    else:
        lines.append("No artifacts.")
    lines.append("")
    lines.append("## Reproduce")
    lines.append("")
    lines.append("```text")
    lines.append(f"awf show {state.run_id}")
    lines.append(f"awf replay {state.run_id}")
    if state.terminal is None:
        lines.append(f"awf resume {state.run_id}")
    lines.append("```")
    lines.append("")
    lines.append(f"Views: [flow.mmd](flow.mmd) - [sequence.mmd](sequence.mmd) - [replay.html](replay.html)")
    lines.append("")
    report = "\n".join(lines)
    (run_dir / "report.md").write_text(report, encoding="utf-8", newline="\n")
    return {"report.md": report, "summary.json": canonical_json(summary)}
