"""State transitions: the only code that mutates a :class:`RunState`.

Keeping this in one module makes checkpointing auditable: after each call the
engine writes the whole state atomically.  It also keeps :mod:`awengine.engine`
free of business vocabulary.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from .contracts import (
    Artifact,
    HistoryEntry,
    Message,
    NodeResult,
    RunState,
    Terminal,
    Usage,
    digest_of,
    utc_now,
)
from .events import EventBus


def usage_cost(usage: Usage | None) -> Decimal:
    if usage is None:
        return Decimal("0")
    return Decimal(str(usage.cost or 0))


class StateManager:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus

    def record_result(
        self,
        state: RunState,
        node: str,
        result: NodeResult,
        *,
        round_no: int,
        input_digest: str,
        reads: Iterable[str] = (),
        emit: bool = True,
    ) -> HistoryEntry:
        """Apply one completed node attempt to the run state."""
        runtime = state.runtime
        runtime.counters.steps += 1
        runtime.visit_count[node] = runtime.visit_count.get(node, 0) + 1
        if result.status == "fail":
            runtime.counters.attempts += 1
        if result.usage is not None:
            state.usage = state.usage.plus(result.usage)
            runtime.counters.cost_used += usage_cost(result.usage)
        for artifact in result.artifacts:
            state.add_artifact(artifact)
            if emit:
                self.bus.emit(
                    "artifact",
                    node=node,
                    round=round_no,
                    data={
                        "id": artifact.id,
                        "type": artifact.type,
                        "digest": artifact.digest,
                        "producer": artifact.producer or node,
                    },
                )
        if result.status in ("ok", "skip"):
            state.outputs[node] = dict(result.output)
        for read in reads:
            artifact = state.artifacts.get(read)
            if artifact is None:
                continue
            message = Message(
                src=artifact.producer or node,
                dst=node,
                intent=f"consume:{artifact.type}",
                artifacts=(artifact.id,),
                round=round_no,
                correlation_id=digest_of([artifact.id, artifact.digest, node, round_no])[:16],
            )
            _append_unique_message(state, message)
            if emit:
                self.bus.emit(
                    "message",
                    node=node,
                    round=round_no,
                    data={
                        "src": message.src,
                        "dst": message.dst,
                        "intent": message.intent,
                        "artifacts": list(message.artifacts),
                    },
                )
        entry = HistoryEntry(
            node=node,
            round=round_no,
            status=result.status,
            input_digest=input_digest,
            output_digest=digest_of(result.output),
            started_at=result.started_at,
            ended_at=result.ended_at,
            error=result.error,
            usage=result.usage,
            artifact_ids=tuple(artifact.id for artifact in result.artifacts),
        )
        state.history = state.history + (entry,)
        state.updated_at = utc_now()
        return entry

    def finalize(
        self,
        state: RunState,
        terminal: Terminal | str,
        *,
        reason: str,
        detail: dict[str, Any] | None = None,
        failure_class: str = "",
    ) -> None:
        state.terminal = str(getattr(terminal, "value", terminal))
        state.stop_reason = reason
        state.stop_detail = detail or {}
        state.failure_class = failure_class
        state.cursor = None
        state.updated_at = utc_now()


def _append_unique_message(state: RunState, message: Message) -> None:
    for existing in state.messages:
        if existing.correlation_id == message.correlation_id:
            return
    state.messages = state.messages + (message,)


def find_replayable(state: RunState, node: str, input_digest: str) -> HistoryEntry | None:
    """Return a completed history entry that can be replayed instead of re-run."""
    for entry in reversed(state.history):
        if entry.node == node and entry.input_digest == input_digest and entry.status in ("ok", "skip"):
            return entry
    return None


def last_node(state: RunState) -> str | None:
    if not state.history:
        return None
    return state.history[-1].node


def node_output(state: RunState, node: str) -> dict[str, Any]:
    return state.outputs.get(node, {})


def started_at(state: RunState) -> datetime:
    return state.created_at
