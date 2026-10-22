"""Declarative workflow specification.

``WorkflowSpec`` is frozen: the engine never mutates a workflow definition at
runtime.  Loading a YAML file produces exactly one spec; ``--disable`` produces
a *different* spec (the effective graph), so hashes and reports always describe
what actually ran.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import canonical_json, digest_of

NodeType = Literal["python", "process", "llm_agent", "builtin"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class _StrictFrozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Range(_StrictFrozen):
    min: int = 1
    max: int = 3

    @model_validator(mode="after")
    def _check(self) -> "Range":
        if self.min < 0 or self.max < 1 or self.min > self.max:
            raise ValueError(f"invalid range min={self.min} max={self.max}")
        return self


class CostLimit(_StrictFrozen):
    max_cny: Decimal | None = None


class ProgressRule(_StrictFrozen):
    key: str = "patch.digest"
    window: int = 2
    metric: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ProgressRule":
        if self.window < 1:
            raise ValueError("progress.window must be >= 1")
        return self


class EscalateRule(_StrictFrozen):
    after_round: int = 0
    model: str | None = None
    temperature: float | None = None
    rubric: str | None = None
    max_times: int = 0


class ConvergeSpec(_StrictFrozen):
    goal: str | None = None
    streak: int = 1
    rounds: Range = Field(default_factory=lambda: Range(min=1, max=3))
    steps: Range = Field(default_factory=lambda: Range(min=1, max=40))
    attempts: Range = Field(default_factory=lambda: Range(min=0, max=6))
    cost: CostLimit = Field(default_factory=CostLimit)
    progress: ProgressRule = Field(default_factory=ProgressRule)
    escalate: EscalateRule = Field(default_factory=EscalateRule)
    on_exhausted: Literal["report", "fail"] = "report"

    @field_validator("goal")
    @classmethod
    def _goal_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("converge.goal must not be empty")
        return value


class VerifySpec(_StrictFrozen):
    level: int = 1
    cmd: str = ""
    holdout: str | None = None
    red_before: bool = False
    timeout_s: int = 120

    @model_validator(mode="after")
    def _check(self) -> "VerifySpec":
        if not 1 <= self.level <= 5:
            raise ValueError("verify.level must be 1..5")
        if not self.cmd.strip():
            raise ValueError("verify.cmd must not be empty")
        return self


class Defaults(_Frozen):
    model: str = "deepseek-chat"
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_s: int = 120
    max_steps: int = 6


class Edge(_Frozen):
    src: str
    dst: str
    when: str | None = None

    @field_validator("when", mode="before")
    @classmethod
    def _normalise_when(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)


class NodeSpec(_Frozen):
    name: str
    type: NodeType
    impl: str = ""
    cmd: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    contract: dict[str, Any] | None = None
    retry: int = 0
    timeout_s: int = 120
    enabled: bool = True
    line: int | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _check(self) -> "NodeSpec":
        if self.type in ("python", "builtin") and not self.impl:
            raise ValueError(f"node {self.name!r}: type={self.type} needs 'impl'")
        if self.type == "process" and not self.cmd:
            raise ValueError(f"node {self.name!r}: type=process needs 'cmd'")
        if self.type == "llm_agent" and not self.impl and not self.config.get("system"):
            raise ValueError(
                f"node {self.name!r}: type=llm_agent needs 'impl' or an inline "
                "'config.system' prompt"
            )
        if self.retry < 0:
            raise ValueError(f"node {self.name!r}: retry must be >= 0")
        if self.timeout_s <= 0:
            raise ValueError(f"node {self.name!r}: timeout_s must be > 0")
        return self


class WorkflowSpec(_Frozen):
    name: str
    version: int = 1
    nodes: tuple[NodeSpec, ...]
    edges: tuple[Edge, ...] = ()
    converge: ConvergeSpec = Field(default_factory=ConvergeSpec)
    verify: VerifySpec | None = None
    defaults: Defaults = Field(default_factory=Defaults)
    source_path: str | None = Field(default=None, exclude=True)

    # -- derived helpers -------------------------------------------------
    @property
    def node_map(self) -> dict[str, NodeSpec]:
        return {node.name: node for node in self.nodes}

    def node(self, name: str) -> NodeSpec | None:
        for node in self.nodes:
            if node.name == name:
                return node
        return None

    def out_edges(self, name: str) -> tuple[Edge, ...]:
        return tuple(edge for edge in self.edges if edge.src == name)

    def in_edges(self, name: str) -> tuple[Edge, ...]:
        return tuple(edge for edge in self.edges if edge.dst == name)

    @property
    def entry(self) -> str:
        """The unique node without incoming edges."""
        names = [node.name for node in self.nodes]
        with_in = {edge.dst for edge in self.edges if edge.dst in names}
        roots = [name for name in names if name not in with_in]
        return roots[0] if roots else names[0]

    @property
    def back_edges(self) -> tuple[tuple[str, str], ...]:
        """``(src, dst)`` pairs that close a loop, found by DFS from the entry."""
        nodes = self.node_map
        found: list[tuple[str, str]] = []
        visiting: list[str] = []
        visited: set[str] = set()

        def walk(name: str) -> None:
            if name in visiting or name in visited:
                return
            visiting.append(name)
            for edge in self.out_edges(name):
                dst = edge.dst
                if dst == "END" or dst not in nodes:
                    continue
                if dst in visiting:
                    if (name, dst) not in found:
                        found.append((name, dst))
                else:
                    walk(dst)
            visiting.pop()
            visited.add(name)

        if nodes:
            walk(self.entry)
        return tuple(found)

    @property
    def loop_heads(self) -> tuple[str, ...]:
        """Targets of back edges, discovered by a DFS from the entry node."""
        found: list[str] = []
        for _, dst in self.back_edges:
            if dst not in found:
                found.append(dst)
        return tuple(found)

    def is_back_edge(self, src: str, dst: str) -> bool:
        return (src, dst) in self.back_edges

    @property
    def loop_head(self) -> str:
        """First back-edge target, or ``""`` for an acyclic workflow."""
        heads = self.loop_heads
        return heads[0] if heads else ""

    def reachable(self) -> set[str]:
        nodes = self.node_map
        seen: set[str] = set()
        stack = [self.entry]
        while stack:
            name = stack.pop()
            if name in seen or name not in nodes:
                continue
            seen.add(name)
            for edge in self.out_edges(name):
                if edge.dst in nodes and edge.dst not in seen:
                    stack.append(edge.dst)
        return seen

    def to_hashable(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude={"source_path"})
        return data

    @property
    def spec_hash(self) -> str:
        return digest_of(self.to_hashable())

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "spec_hash": self.spec_hash,
            "nodes": [node.name for node in self.nodes],
            "edges": [f"{e.src}->{e.dst}" for e in self.edges],
            "entry": self.entry,
        }


def spec_hash_of(spec: WorkflowSpec) -> str:
    return digest_of(spec.to_hashable())


def canonical_spec(spec: WorkflowSpec) -> str:
    return canonical_json(spec.to_hashable())
