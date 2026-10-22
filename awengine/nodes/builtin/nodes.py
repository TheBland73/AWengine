"""Generic built-in nodes.  Business-free on purpose."""

from __future__ import annotations

from typing import Any

from ...contracts import Artifact, NodeResult
from ...executor import patch_violations
from ..base import NodeContext
from .tools import execute_tool, schemas_for


def noop(ctx: NodeContext) -> NodeResult:
    return NodeResult(status="ok", output={"ok": True})


def echo(ctx: NodeContext) -> NodeResult:
    value = ctx.config.get("value", ctx.config.get("text", ""))
    if isinstance(value, str):
        value = ctx.render(value)
    output = {"text": value}
    if isinstance(ctx.config.get("output"), dict):
        output.update(ctx.config["output"])
    return NodeResult(status="ok", output=output)


def set_values(ctx: NodeContext) -> NodeResult:
    values = ctx.config.get("values") or {}
    output: dict[str, Any] = {}
    if isinstance(values, dict):
        for key, value in values.items():
            output[str(key)] = ctx.render(value) if isinstance(value, str) else value
    extra = ctx.config.get("output")
    if isinstance(extra, dict):
        output.update(extra)
    artifacts = tuple(_artifacts_from_config(ctx, output))
    return NodeResult(status="ok", output=output, artifacts=artifacts)


def assert_condition(ctx: NodeContext) -> NodeResult:
    from ...expr import ExprError, compile_expr

    source = str(ctx.config.get("condition") or "False")
    try:
        ok = compile_expr(source).evaluate(ctx.env())
    except ExprError as exc:
        return NodeResult.failure(f"E-EXPR: {exc.message}")
    if not ok:
        return NodeResult(status="fail", output={"condition": source, "passed": False}, error=f"assert failed: {source}")
    return NodeResult(status="ok", output={"condition": source, "passed": True})


def write_file(ctx: NodeContext) -> NodeResult:
    path = str(ctx.config.get("path") or "")
    if not path:
        return NodeResult.failure("E-NODE: write_file needs config.path")
    content = ctx.config.get("content", "")
    if isinstance(content, str):
        content = ctx.render(content)
    try:
        written = ctx.executor.write_file(path, str(content))
    except Exception as exc:  # noqa: BLE001
        return NodeResult.failure(f"E-NODE: {exc}")
    relative = written.relative_to(ctx.executor.sandbox).as_posix()
    return NodeResult(
        status="ok",
        output={"path": relative, "bytes": len(str(content).encode("utf-8"))},
        artifacts=(
            Artifact(
                id=str(ctx.config.get("artifact") or "file"),
                type="file",
                producer=ctx.node.name,
                round=ctx.round,
                content={"path": relative, "bytes": len(str(content).encode("utf-8"))},
            ),
        ),
    )


def apply_patch(ctx: NodeContext) -> NodeResult:
    artifact_id = str(ctx.config.get("artifact") or "patch")
    artifact = ctx.artifact(artifact_id)
    if artifact is None:
        return NodeResult.failure(f"E-NODE: artifact {artifact_id!r} not found")
    content = artifact.content
    diff = str(content.get("diff") or content.get("patch") or "")
    problems = patch_violations(diff)
    if problems:
        return NodeResult(
            status="fail",
            output={"cheat": True, "violations": problems, "artifact": artifact_id},
            error="E-GUARD: patch touches protected paths: " + "; ".join(problems),
        )
    ok, detail = ctx.executor.apply_patch(diff, node=ctx.node.name)
    if not ok:
        return NodeResult(status="fail", output={"applied": False, "detail": detail}, error=f"E-PATCH: {detail}")
    touched = content.get("touched") or []
    return NodeResult(
        status="ok",
        output={"applied": True, "artifact": artifact_id, "digest": artifact.digest, "touched": touched},
    )


def call_tool(ctx: NodeContext) -> NodeResult:
    name = str(ctx.config.get("tool") or "")
    arguments = ctx.config.get("arguments") or {}
    observation = execute_tool(name, dict(arguments), ctx.executor, node=ctx.node.name)
    return NodeResult(status="ok", output={"tool": name, "observation": observation[:20_000]})


def log_node(ctx: NodeContext) -> NodeResult:
    message = ctx.render(str(ctx.config.get("message") or ctx.node.name))
    ctx.log(message)
    return NodeResult(status="ok", output={"message": message})


def _artifacts_from_config(ctx: NodeContext, output: dict[str, Any]) -> list[Artifact]:
    artifacts: list[Artifact] = []
    specs = ctx.config.get("artifacts") or []
    if not isinstance(specs, list):
        return artifacts
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        artifact_id = str(spec.get("id") or "")
        if not artifact_id:
            continue
        source = spec.get("from", artifact_id)
        content = output.get(source) if isinstance(source, str) else None
        if content is None:
            content = {}
        if not isinstance(content, dict):
            content = {"value": content}
        artifacts.append(
            Artifact(
                id=artifact_id,
                type=str(spec.get("type") or "generic"),
                producer=ctx.node.name,
                round=ctx.round,
                refs=tuple(str(item) for item in spec.get("refs") or ()),
                content=content,
            )
        )
    return artifacts


BUILTIN_HANDLERS = {
    "noop": noop,
    "echo": echo,
    "log": log_node,
    "set": set_values,
    "assert": assert_condition,
    "write_file": write_file,
    "apply_patch": apply_patch,
    "call_tool": call_tool,
}

BUILTIN_DESCRIPTIONS = {
    "noop": "do nothing successfully",
    "echo": "emit config.value as output.text",
    "log": "emit a node_log event",
    "set": "emit static/templated output values and artifacts",
    "assert": "fail unless config.condition is true",
    "write_file": "write config.content to config.path in the sandbox",
    "apply_patch": "git-apply the latest patch artifact, refusing protected paths",
    "call_tool": "call one read-only tool and return its observation",
}

__all__ = ["BUILTIN_HANDLERS", "BUILTIN_DESCRIPTIONS", "schemas_for"]
