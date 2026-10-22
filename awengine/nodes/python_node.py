"""``type: python`` node runtime.

The handler is resolved from ``impl`` (``package.module:function``) and called
with the :class:`NodeContext`.  Everything the handler raises becomes a failed
``NodeResult``; exceptions never cross into the engine.
"""

from __future__ import annotations

import time

from ..contracts import NodeResult
from .base import NodeContext, as_result, resolve_impl


def run(ctx: NodeContext) -> NodeResult:
    started = time.perf_counter()
    try:
        handler = resolve_impl(ctx.node.impl, project_dir=ctx.project_dir)
        if not callable(handler):
            return NodeResult.failure(f"E-NODE: {ctx.node.impl!r} is not callable")
        result = as_result(handler(ctx))
    except Exception as exc:  # noqa: BLE001 - node boundary
        return NodeResult.failure(f"E-NODE: {type(exc).__name__}: {exc}")
    result.ended_at = result.ended_at or result.started_at
    elapsed = int((time.perf_counter() - started) * 1000)
    ctx.bus.emit(
        "node_handler",
        node=ctx.node.name,
        round=ctx.round,
        data={"type": "python", "impl": ctx.node.impl, "duration_ms": elapsed, "status": result.status},
    )
    return result
