"""Node protocol and the context handed to every handler.

Handlers are plain callables ``run(ctx) -> NodeResult``.  A handler never sees
the engine's control flow: it cannot route, stop the run, or touch another
node's files.  Everything it needs (and everything it is allowed to do) is on
the context object.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, Callable, Protocol

from ..budget import Budget
from ..contracts import Artifact, NodeResult, RunState, canonical_json
from ..events import EventBus
from ..executor import LocalExecutor
from ..spec import NodeSpec, WorkflowSpec

_DOTTED_REF = re.compile(
    r"\$\{([A-Za-z_]\w*\.[A-Za-z_]\w*)\}|\$([A-Za-z_]\w*\.[A-Za-z_]\w*)"
)


@dataclass
class NodeContext:
    node: NodeSpec
    spec: WorkflowSpec
    state: RunState
    executor: LocalExecutor
    bus: EventBus
    inputs: dict[str, str]
    round: int = 1
    attempt: int = 1
    llm: Any = None
    budget: Budget | None = None
    run_dir: Path = Path(".")
    project_dir: Path = Path(".")
    options: dict[str, Any] = field(default_factory=dict)
    agent: dict[str, Any] = field(default_factory=dict)

    # -- convenience ---------------------------------------------------
    @property
    def config(self) -> dict[str, Any]:
        return self.node.config

    def artifact(self, artifact_id: str) -> Artifact | None:
        return self.state.artifacts.get(artifact_id)

    def artifact_content(self, artifact_id: str) -> dict[str, Any]:
        artifact = self.artifact(artifact_id)
        return dict(artifact.content) if artifact else {}

    def output(self, node: str) -> dict[str, Any]:
        return self.state.outputs.get(node, {})

    def env(self) -> dict[str, Any]:
        return build_env(self.state, self.spec)

    def log(self, message: str, **data: Any) -> None:
        self.bus.emit("node_log", node=self.node.name, round=self.round, data={"message": message, **data})

    def warn(self, message: str, **data: Any) -> None:
        self.bus.emit("warn", node=self.node.name, round=self.round, data={"message": message, **data})

    def render(self, template: str) -> str:
        variables = {key: str(value) for key, value in self.inputs.items()}
        variables.setdefault("workdir", str(self.executor.sandbox))
        variables.setdefault("round", str(self.round))
        variables.setdefault("node", self.node.name)
        for name, output in self.state.outputs.items():
            if not isinstance(output, dict):
                continue
            for key, value in output.items():
                if isinstance(value, (str, int, float, bool)):
                    variables.setdefault(f"{name}.{key}", str(value))
        for artifact_id, artifact in self.state.artifacts.items():
            if isinstance(artifact.content, dict):
                variables.setdefault(
                    artifact_id,
                    json.dumps(artifact.content, ensure_ascii=False, indent=2, default=str),
                )
        for key, value in self.config.items():
            if isinstance(value, (str, int, float, bool)):
                variables.setdefault(key, str(value))
        try:
            # ``string.Template`` only understands bare ``$name``; promote
            # dotted references such as ``$writer.doc`` before substituting.
            template = _DOTTED_REF.sub(
                lambda match: variables.get(match.group(1) or match.group(2), match.group(0)),
                template,
            )
            return Template(template).safe_substitute(variables)
        except (ValueError, KeyError):
            return template

    def task_text(self) -> str:
        template = str(
            self.config.get("task")
            or self.agent.get("task")
            or "Inputs: $inputs"
        )
        text = self.render(template)
        if "$inputs" in text:
            rendered = ", ".join(f"{key}={value}" for key, value in self.inputs.items())
            text = text.replace("$inputs", rendered or "(none)")
        return text


class NodeHandler(Protocol):
    def __call__(self, ctx: NodeContext) -> NodeResult: ...


def build_env(state: RunState, spec: WorkflowSpec) -> dict[str, Any]:
    """The expression environment shared by guards, goals and node scripts."""
    env: dict[str, Any] = {}
    for name, output in state.outputs.items():
        env[name] = output
    artifacts: dict[str, Any] = {}
    for artifact_id, artifact in state.artifacts.items():
        rendered = dict(artifact.content)
        rendered.setdefault("digest", artifact.digest)
        rendered["digest"] = artifact.digest
        rendered.setdefault("type", artifact.type)
        rendered.setdefault("producer", artifact.producer)
        artifacts[artifact_id] = rendered
    env["artifacts"] = artifacts
    env["inputs"] = dict(state.inputs)
    env["counters"] = {
        "rounds": state.runtime.counters.rounds,
        "steps": state.runtime.counters.steps,
        "attempts": state.runtime.counters.attempts,
        "cost_used": state.runtime.counters.cost_used,
    }
    env["verify"] = state.verify.model_dump(mode="json") if state.verify else None
    env["blockers"] = sum(
        int(output.get("blockers") or 0)
        for output in state.outputs.values()
        if isinstance(output, dict) and isinstance(output.get("blockers"), (int, float))
    )
    env["runtime"] = {
        "round": state.runtime.round,
        "streak": state.runtime.streak,
        "visits": dict(state.runtime.visit_count),
    }
    env["messages"] = [message.model_dump(mode="json") for message in state.messages]
    return env


def resolve_impl(impl: str, *, project_dir: Path | None = None) -> Any:
    """Resolve ``package.module:attr`` (or ``package.module.attr``)."""
    if not impl:
        raise ValueError("empty impl")
    if ":" in impl:
        module_name, _, attribute = impl.partition(":")
    else:
        module_name, _, attribute = impl.rpartition(".")
        if not module_name:
            raise ValueError(f"impl {impl!r} must look like 'package.module:attribute'")
    for path in _import_paths(project_dir):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
    module = importlib.import_module(module_name)
    if not attribute:
        return module
    return getattr(module, attribute)


def _import_paths(project_dir: Path | None) -> list[Path]:
    paths = [Path.cwd(), Path.cwd() / "nodes"]
    if project_dir is not None:
        paths.extend([project_dir, project_dir / "nodes"])
    package_root = Path(__file__).resolve().parent.parent.parent
    paths.append(package_root)
    return paths


def import_node_module(name: str, *, project_dir: Path | None = None) -> Any:
    """Import a discovered node module by short name (``nodes.tester``)."""
    candidates = [name]
    if "." not in name:
        candidates = [name, f"nodes.{name}", f"awengine.nodes.{name}", f"awengine.nodes.code.{name}"]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return resolve_impl(candidate, project_dir=project_dir)
        except (ImportError, AttributeError, ValueError) as exc:
            last_error = exc
    raise ImportError(f"cannot import node module {name!r}: {last_error}")


def as_result(value: Any) -> NodeResult:
    if isinstance(value, NodeResult):
        return value
    if value is None:
        return NodeResult(status="ok", output={})
    if isinstance(value, dict):
        return NodeResult(status="ok", output=dict(value))
    return NodeResult(status="ok", output={"value": value})


def sha_of(value: Any) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def node_invocation_input(ctx: NodeContext) -> dict[str, Any]:
    """Stable description of everything that can change a node's output."""
    return {
        "node": ctx.node.model_dump(mode="json", exclude={"line"}),
        "round": ctx.round,
        "inputs": ctx.inputs,
        "artifacts": {key: value.digest for key, value in sorted(ctx.state.artifacts.items())},
        "outputs": {key: sha_of(value) for key, value in sorted(ctx.state.outputs.items())},
    }
