"""Project node: uppercase text.  Drop any module with run(ctx) into nodes/."""

from __future__ import annotations

from awengine.contracts import NodeResult


def run(ctx) -> NodeResult:
    text = str(ctx.config.get("text") or ctx.inputs.get("text") or "")
    return NodeResult(status="ok", output={"text": text.upper(), "length": len(text.upper())})
