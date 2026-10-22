"""``type: process`` node runtime.

The child gets one JSON object on stdin and must print one JSON object on
stdout::

    {"status": "ok", "output": {...},
     "artifacts": [{"id": "...", "type": "...", "content": {...}}]}

A non-zero exit code, invalid JSON or a timeout becomes a failed NodeResult.
"""

from __future__ import annotations

import json
import time

from ..contracts import Artifact, NodeResult, utc_now
from .base import NodeContext


def run(ctx: NodeContext) -> NodeResult:
    started = utc_now()
    envelope = {
        "node": ctx.node.name,
        "round": ctx.round,
        "attempt": ctx.attempt,
        "inputs": ctx.inputs,
        "workdir": str(ctx.executor.sandbox),
        "artifacts": {
            key: {"type": value.type, "digest": value.digest, "content": value.content}
            for key, value in ctx.state.artifacts.items()
        },
        "counters": {
            "rounds": ctx.state.runtime.counters.rounds,
            "steps": ctx.state.runtime.counters.steps,
            "attempts": ctx.state.runtime.counters.attempts,
        },
    }
    payload = json.dumps(envelope, ensure_ascii=False)
    command = ctx.node.cmd or "python"
    ctx.bus.emit(
        "process_start",
        node=ctx.node.name,
        round=ctx.round,
        data={"cmd": command, "workdir": str(ctx.executor.sandbox)},
    )
    result = ctx.executor.run(
        command,
        timeout=ctx.node.timeout_s,
        node=ctx.node.name,
        stdin_text=payload,
        extra_env={"AW_NODE": ctx.node.name},
    )
    ended = utc_now()
    if result.timed_out:
        return NodeResult(status="fail", output={"stdout": result.stdout[-2000:]}, error="E-PROCESS: timeout",
                          started_at=started, ended_at=ended)
    if result.exit_code != 0:
        return NodeResult(
            status="fail",
            output={"exit_code": result.exit_code, "stdout": result.stdout[-2000:]},
            error=f"E-PROCESS: exit {result.exit_code}: {result.tail[:800]}",
            started_at=started,
            ended_at=ended,
        )
    text = (result.stdout or "").strip()
    if not text:
        return NodeResult(status="ok", output={}, started_at=started, ended_at=ended)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return NodeResult(status="ok", output={"text": text[-20_000:]}, started_at=started, ended_at=ended)
    if not isinstance(data, dict):
        return NodeResult(status="ok", output={"value": data}, started_at=started, ended_at=ended)
    status = str(data.get("status") or "ok")
    if status not in ("ok", "skip", "fail"):
        status = "ok"
    output = data.get("output")
    if not isinstance(output, dict):
        output = {key: value for key, value in data.items() if key not in ("status", "artifacts", "error")}
    artifacts: list[Artifact] = []
    for raw in data.get("artifacts") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        artifacts.append(
            Artifact(
                id=str(raw["id"]),
                type=str(raw.get("type") or "generic"),
                producer=ctx.node.name,
                round=ctx.round,
                refs=tuple(str(item) for item in raw.get("refs") or ()),
                content=raw.get("content") if isinstance(raw.get("content"), dict) else {},
            )
        )
    return NodeResult(
        status=status,  # type: ignore[arg-type]
        output=output,
        artifacts=tuple(artifacts),
        error=str(data.get("error") or "") or None,
        started_at=started,
        ended_at=ended,
    )
