"""Error hierarchy and exit codes.

Every error the CLI shows the user is an :class:`AwfError` with a stable
``code``, a human message and an optional hint.  Anything else is a bug and is
reported as ``INTERNAL``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    OK = 0
    NOT_PASSED = 1
    USAGE = 2
    INTERNAL = 3
    INTERRUPT = 130


@dataclass
class AwfError(Exception):
    code: str
    message: str
    hint: str | None = None
    detail: dict[str, Any] | None = None
    exit_code: ExitCode = ExitCode.INTERNAL

    def __str__(self) -> str:  # pragma: no cover - trivial
        text = f"[{self.code}] {self.message}"
        if self.hint:
            text += f"\n  hint: {self.hint}"
        return text

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        if self.detail:
            data["detail"] = self.detail
        return data


class UsageError(AwfError):
    def __init__(self, code: str, message: str, hint: str | None = None, **kw: Any) -> None:
        super().__init__(code, message, hint, exit_code=ExitCode.USAGE, **kw)


class SpecError(UsageError):
    """A workflow / suite file is invalid or ambiguous."""

    def __init__(
        self,
        code: str,
        message: str,
        hint: str | None = None,
        *,
        line: int | None = None,
        path: str | None = None,
        **kw: Any,
    ) -> None:
        self.line = line
        self.path = path
        if line is not None:
            message = f"{path or '<spec>'}:{line}: {message}"
        elif path:
            message = f"{path}: {message}"
        super().__init__(code, message, hint, **kw)


class ContractError(AwfError):
    def __init__(self, message: str, hint: str | None = None, **kw: Any) -> None:
        super().__init__("F-COORD-HANDOFF", message, hint, exit_code=ExitCode.INTERNAL, **kw)


class ExecutorUnsafe(AwfError):
    def __init__(self, code: str, message: str, hint: str | None = None, **kw: Any) -> None:
        super().__init__(code, message, hint, exit_code=ExitCode.INTERNAL, **kw)


class StateCorrupt(AwfError):
    def __init__(self, message: str, hint: str | None = None, **kw: Any) -> None:
        super().__init__(
            "F-COORD-STATE",
            message,
            hint or "run `awf ls` to inspect runs; corrupt checkpoints fall back to .prev when possible",
            exit_code=ExitCode.INTERNAL,
            **kw,
        )


class CassetteMiss(AwfError):
    def __init__(self, key: str, hint: str | None = None, **kw: Any) -> None:
        super().__init__(
            "F-VERIFY-WEAK",
            f"no cassette for request {key[:16]}...; offline mode cannot call the model",
            hint or "re-run with --record (needs an API key) to add the cassette",
            exit_code=ExitCode.INTERNAL,
            **kw,
        )


class RunLocked(AwfError):
    def __init__(self, run_id: str, owner: str = "unknown", **kw: Any) -> None:
        super().__init__(
            "F-COORD-STATE",
            f"run {run_id} is locked by {owner}",
            "wait for the owner to finish or remove runs/<id>/.lease if it is stale",
            exit_code=ExitCode.NOT_PASSED,
            **kw,
        )


class PricingMissing(UsageError):
    def __init__(self, model: str, **kw: Any) -> None:
        super().__init__(
            "F-SPEC-MISSING",
            f"no pricing entry for model {model!r}",
            "add it to pricing.toml ([models.<name>]) or pass --max-cost 0",
            **kw,
        )


class LLMNoKey(UsageError):
    def __init__(self, model: str, **kw: Any) -> None:
        super().__init__(
            "F-SPEC-MISSING",
            f"model {model!r} needs an API key but none is configured",
            "set DEEPSEEK_API_KEY / OPENAI_API_KEY or run with --fake",
            **kw,
        )


class LLMError(AwfError):
    def __init__(self, message: str, hint: str | None = None, **kw: Any) -> None:
        super().__init__("F-COORD-HANDOFF", message, hint, exit_code=ExitCode.INTERNAL, **kw)


# Failure classes.  These are *classifications*
# attached to stop decisions, not exceptions.
FAILURE_CLASSES = {
    "F-SPEC-AMBIG": "workflow specification is ambiguous",
    "F-SPEC-MISSING": "workflow specification is missing required data",
    "F-COORD-HANDOFF": "agent handoff lost or contradicted information",
    "F-COORD-STATE": "run state is inconsistent",
    "F-VERIFY-WEAK": "verification cannot distinguish pass from fail",
    "F-VERIFY-FALSEPASS": "model claimed success but evidence says otherwise",
    "F-STOP-EARLY": "the run stopped before satisfying its goal",
    "F-STOP-LATE": "the run spent rounds without improving",
    "F-CONTEXT-OVERFLOW": "context exceeded the configured budget",
}


def classify_stop(terminal: str, reason: str, counters: dict[str, Any] | None = None) -> str:
    """Map a terminal + reason to a failure class using rules, never a model."""
    counters = counters or {}
    if terminal == "exhausted":
        if reason in {"rounds", "steps"}:
            return "F-STOP-LATE"
        if reason == "cost":
            return "F-STOP-EARLY"
        return "F-STOP-EARLY"
    if terminal == "stalled":
        return "F-STOP-LATE"
    if terminal == "blocked":
        if reason in {"verify_failed", "holdout_failed", "false_pass"}:
            return "F-VERIFY-FALSEPASS"
        if reason == "cheat":
            return "F-STOP-EARLY"
        if reason == "context_overflow":
            return "F-CONTEXT-OVERFLOW"
        if reason in {"node_failed", "contract"}:
            return "F-COORD-HANDOFF"
        return "F-STOP-EARLY"
    if terminal == "no_op":
        return "F-SPEC-AMBIG"
    return ""
