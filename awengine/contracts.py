"""Data contracts shared by every layer.

All models are pydantic v2 models.  Specifications that are persisted in the
run state are frozen; runtime containers are mutable on purpose and only ever
serialised through :mod:`awengine.checkpoint`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def canonical_json(data: Any) -> str:
    """Canonical JSON used for every digest in the engine."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_of(data: Any) -> str:
    return sha256_text(canonical_json(data))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Terminal(str, Enum):
    SUCCESS = "success"
    NO_OP = "no_op"
    BLOCKED = "blocked"
    STALLED = "stalled"
    EXHAUSTED = "exhausted"


class Usage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0
    latency_ms: int = 0
    cost: Decimal = Decimal("0")

    def plus(self, other: "Usage") -> "Usage":
        return Usage(
            model=self.model or other.model,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            calls=self.calls + other.calls,
            latency_ms=self.latency_ms + other.latency_ms,
            cost=self.cost + other.cost,
        )


class Artifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    type: str
    producer: str
    round: int = 0
    refs: tuple[str, ...] = ()
    content: dict[str, Any] = Field(default_factory=dict)

    @property
    def digest(self) -> str:
        return digest_of(self.content)


class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    src: str
    dst: str
    intent: str
    artifacts: tuple[str, ...] = ()
    round: int = 1
    correlation_id: str = ""


class NodeResult(BaseModel):
    """What a node handler returns.  Handlers never raise across the boundary."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["ok", "skip", "fail"] = "ok"
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: tuple[Artifact, ...] = ()
    usage: Usage | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime = Field(default_factory=utc_now)

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "skip")

    @classmethod
    def failure(cls, error: str, *, started_at: datetime | None = None) -> "NodeResult":
        now = utc_now()
        return cls(
            status="fail",
            error=error,
            started_at=started_at or now,
            ended_at=now,
        )


class Counters(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rounds: int = 0
    steps: int = 0
    attempts: int = 0
    cost_used: Decimal = Decimal("0")


class ProgressPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    round: int = 0
    key: str = ""
    key_value: str | None = None
    metric: float | None = None
    at: datetime = Field(default_factory=utc_now)


class EscalationRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    round: int = 0
    from_model: str = ""
    to_model: str = ""
    temperature: float | None = None
    rubric: str | None = None
    at: datetime = Field(default_factory=utc_now)


class Runtime(BaseModel):
    model_config = ConfigDict(extra="ignore")

    round: int = 0
    round_open: bool = False
    visit_count: dict[str, int] = Field(default_factory=dict)
    counters: Counters = Field(default_factory=Counters)
    streak: int = 0
    progress_trace: tuple[ProgressPoint, ...] = ()
    escalations: tuple[EscalationRecord, ...] = ()
    flags: dict[str, bool] = Field(default_factory=dict)


class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    node: str
    round: int
    status: Literal["ok", "skip", "fail"]
    input_digest: str = ""
    output_digest: str = ""
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime = Field(default_factory=utc_now)
    error: str | None = None
    usage: Usage | None = None
    artifact_ids: tuple[str, ...] = ()


class VerifyResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    command: str = ""
    level: int = 1
    passed: bool = False
    exit_code: int | None = None
    holdout_passed: bool | None = None
    red_before_failed: bool | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    duration_ms: int = 0
    failure_class: str = ""
    checked_at: datetime = Field(default_factory=utc_now)
    detail: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str = ""
    line: int | None = None
    quote: str = ""


class Issue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    severity: Literal["blocker", "major", "minor"] = "minor"
    file: str = ""
    line: int | None = None
    message: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    suggestion: str = ""

    def key(self) -> str:
        return f"{self.severity}:{self.file}:{self.line}:{self.message.strip()}"


class Patch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    diff: str = ""
    rationale: str = ""
    touched: list[str] = Field(default_factory=list)


class BudgetState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cost_limit: Decimal | None = None
    pricing_version: str = ""


class RunState(BaseModel):
    """The single source of truth for one run (``runs/<id>/state.json``)."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 2
    run_id: str
    workflow: str = ""
    spec_hash: str = ""
    inputs: dict[str, str] = Field(default_factory=dict)
    runtime: Runtime = Field(default_factory=Runtime)
    artifacts: dict[str, Artifact] = Field(default_factory=dict)
    messages: tuple[Message, ...] = ()
    history: tuple[HistoryEntry, ...] = ()
    outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    budget: BudgetState = Field(default_factory=BudgetState)
    verify: VerifyResult | None = None
    terminal: str | None = None
    stop_reason: str = ""
    failure_class: str = ""
    stop_detail: dict[str, Any] = Field(default_factory=dict)
    cursor: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def latest_artifact(self, artifact_id: str) -> Artifact | None:
        return self.artifacts.get(artifact_id)

    def add_artifact(self, artifact: Artifact) -> None:
        self.artifacts[artifact.id] = artifact
