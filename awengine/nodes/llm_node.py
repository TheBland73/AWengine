"""``type: llm_agent`` runtime: context, tool loop, artifacts.

An agent module supplies an ``AGENT`` dict::

    AGENT = {
        "name": "coder",
        "system": "...",
        "task": "... $task ...",
        "tools": ["read_file", "search_code"],
        "artifacts": [{"id": "patch", "type": "patch", "from": "patch"}],
    }

The node calls the provider until it returns a final JSON object, executing
whitelisted read-only tools in between.  Usage is charged against the budget at
the same place the call is made, so cost cannot be forgotten by a handler.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..budget import Budget
from ..context import ContextOverflow, ContextPolicy, assemble
from ..contracts import Artifact, NodeResult, Usage, utc_now
from ..llm.client import LLMResponse, parse_json_response
from ..spec import NodeSpec
from .base import NodeContext, resolve_impl
from .builtin.tools import execute_tool, schemas_for


def load_agent(impl: str, *, project_dir) -> dict[str, Any]:
    if not impl:
        return {}
    module = None
    try:
        import importlib

        module = importlib.import_module(impl)
    except ImportError:
        obj = resolve_impl(impl, project_dir=project_dir)
        if isinstance(obj, dict):
            return dict(obj)
        module = obj
    agent = getattr(module, "AGENT", None)
    if isinstance(agent, dict):
        return dict(agent)
    return {}


def _policy(config: dict[str, Any]) -> ContextPolicy:
    raw = config.get("context") or {}
    if not isinstance(raw, dict):
        raw = {}
    defaults = {
        "sources": ("input", "history", "repo_map", "artifacts"),
        "max_tokens": int(config.get("max_tokens") or 12_000),
    }
    merged = {**defaults, **raw}
    return ContextPolicy(**merged)


def _repo_map(ctx: NodeContext) -> str:
    setting = ctx.config.get("repo_map", ctx.agent.get("repo_map", True))
    if setting is False:
        return ""
    roots = ctx.executor.sandbox
    lines: list[str] = []
    allowed = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".js", ".ts", ".cfg", ".ini"}
    ignored = {
        ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", "node_modules", "runs", ".aw", "tests_holdout",
    }
    for path in sorted(roots.rglob("*")):
        if not path.is_file() or ignored.intersection(path.relative_to(roots).parts):
            continue
        if path.suffix.lower() not in allowed:
            continue
        relative = path.relative_to(roots).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            continue
        lines.append(f"- {relative} ({size} bytes)")
        if len(lines) >= 80:
            break
    if not lines:
        return ""
    return "Files in the working copy:\n" + "\n".join(lines)


def _history(ctx: NodeContext) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in ctx.state.history:
        output = ctx.state.outputs.get(entry.node, {})
        summary = ""
        if isinstance(output, dict):
            for key in ("summary", "message", "text", "greeting"):
                if output.get(key):
                    summary = str(output[key])
                    break
            if not summary:
                summary = json.dumps(output, ensure_ascii=False, sort_keys=True)
        entries.append(
            {
                "round": entry.round,
                "node": entry.node,
                "status": entry.status,
                "summary": summary,
            }
        )
    return entries


def _artifact_specs(ctx: NodeContext) -> list[dict[str, Any]]:
    specs = ctx.config.get("artifacts", ctx.agent.get("artifacts", []))
    return [item for item in specs if isinstance(item, dict)] if isinstance(specs, list) else []


def _build_artifacts(ctx: NodeContext, output: dict[str, Any]) -> tuple[Artifact, ...]:
    artifacts: list[Artifact] = []
    for spec in _artifact_specs(ctx):
        artifact_id = str(spec.get("id") or "")
        if not artifact_id:
            continue
        source = str(spec.get("from") or artifact_id)
        if source == "__self__":
            value: Any = {key: item for key, item in output.items() if not key.startswith("_")}
        else:
            value = output.get(source)
        if value is None:
            continue
        if isinstance(value, dict):
            content = value
        elif isinstance(value, list):
            content = {"items": value}
        else:
            content = {"value": value}
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
    return tuple(artifacts)


def _model_settings(ctx: NodeContext) -> tuple[str, float, int]:
    model = str(ctx.config.get("model") or ctx.agent.get("model") or ctx.spec.defaults.model)
    temperature = float(ctx.config.get("temperature", ctx.agent.get("temperature", ctx.spec.defaults.temperature)))
    max_tokens = int(ctx.config.get("max_tokens") or ctx.spec.defaults.max_tokens)
    if ctx.options.get("escalate_model"):
        model = str(ctx.options["escalate_model"])
    if ctx.options.get("escalate_temperature") is not None:
        temperature = float(ctx.options["escalate_temperature"])
    return model, temperature, max_tokens


def _system_prompt(ctx: NodeContext) -> str:
    system = str(ctx.config.get("system") or ctx.agent.get("system") or "You are a helpful agent.")
    rubric = ctx.config.get("rubric") or ctx.options.get("escalate_rubric")
    if rubric:
        system = f"{system}\n\n## Rubric (this attempt)\n{rubric}"
    return ctx.render(system)


def _chat_tools(ctx: NodeContext) -> tuple[list[str], list[dict[str, Any]]]:
    names = ctx.config.get("tools", ctx.agent.get("tools", []))
    names = [str(item) for item in names] if isinstance(names, list) else []
    allowed = [name for name in names if name in {"read_file", "search_code", "run_cmd", "list_files"}]
    return allowed, schemas_for(allowed)


def _normalise(ctx: NodeContext, data: dict[str, Any]) -> dict[str, Any]:
    handler = ctx.agent.get("normalize")
    if callable(handler):
        try:
            result = handler(data, ctx)
            if isinstance(result, dict):
                return result
        except Exception as exc:  # noqa: BLE001 - agent code is untrusted
            ctx.warn(f"agent normalize() failed: {exc}")
    return data


def run(ctx: NodeContext) -> NodeResult:
    started = utc_now()
    agent = load_agent(ctx.node.impl, project_dir=ctx.project_dir)
    ctx.agent = agent
    if ctx.llm is None:
        return NodeResult.failure("E-LLM: no provider configured")
    try:
        policy = _policy(ctx.config)
    except Exception as exc:  # noqa: BLE001
        return NodeResult(status="fail", output={}, error=f"E-CONTEXT: {exc}", started_at=started, ended_at=utc_now())
    max_steps = int(ctx.config.get("max_steps") or ctx.agent.get("max_steps") or ctx.spec.defaults.max_steps)
    model, temperature, max_tokens = _model_settings(ctx)
    system = _system_prompt(ctx)
    task = ctx.task_text()
    repo_map = _repo_map(ctx)
    observations: list[dict[str, Any]] = []
    tool_names, tool_schemas = _chat_tools(ctx)
    artifact_contents: dict[str, Any] = {}
    for artifact_id in ctx.config.get("reads") or ctx.agent.get("reads") or []:
        if artifact_id in ctx.state.artifacts:
            artifact_contents[str(artifact_id)] = ctx.state.artifacts[str(artifact_id)].content
    try:
        bundle = assemble(
            system=system,
            task=task,
            policy=policy,
            node=ctx.node.name,
            repo_map=repo_map,
            history=_history(ctx),
            observations=observations,
            artifacts=artifact_contents,
        )
    except ContextOverflow as exc:
        return NodeResult(status="fail", output={"overflow": True, "tokens": exc.used},
                          error=f"F-CONTEXT-OVERFLOW: {exc.message}", started_at=started, ended_at=utc_now())
    if bundle.trimmed:
        ctx.bus.emit(
            "context_action",
            node=ctx.node.name,
            round=ctx.round,
            data={"actions": bundle.trimmed, "tokens": bundle.tokens, "prefix_sha": bundle.prefix_sha},
        )
    messages: list[dict[str, Any]] = list(bundle.messages)
    total_usage = Usage(model=model)
    text = ""
    finish_reason = "max_steps"
    for step in range(max_steps):
        call_started = time.perf_counter()
        try:
            response = ctx.llm.complete(
                model=model,
                messages=messages,
                tools=tool_schemas or None,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=ctx.node.timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return NodeResult(
                status="fail",
                output={"step": step, "model": model},
                error=f"E-LLM: {type(exc).__name__}: {exc}",
                usage=total_usage,
                started_at=started,
                ended_at=utc_now(),
            )
        latency = int((time.perf_counter() - call_started) * 1000)
        response.usage.latency_ms = latency
        response.usage.model = response.usage.model or model
        cost = ctx.budget.charge(model, response.usage) if ctx.budget is not None else 0
        response.usage.cost = cost
        total_usage = total_usage.plus(response.usage)
        ctx.bus.emit(
            "llm_call",
            node=ctx.node.name,
            round=ctx.round,
            data={
                "model": model,
                "step": step,
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "cached_tokens": response.usage.cached_tokens,
                "cost": str(cost),
                "latency_ms": latency,
                "tool_calls": [call.name for call in response.tool_calls],
                "cache_prefix_sha": bundle.prefix_sha,
                "context_tokens": bundle.tokens,
                "temperature": temperature,
            },
        )
        if response.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": response.text or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                        }
                        for call in response.tool_calls
                    ],
                }
            )
            for call in response.tool_calls:
                if call.name not in tool_names:
                    observation = f"DENIED: tool {call.name!r} is not enabled for this agent"
                    ctx.bus.emit(
                        "tool_call",
                        node=ctx.node.name,
                        round=ctx.round,
                        data={"tool": call.name, "denied": True, "arguments": call.arguments},
                    )
                else:
                    ctx.bus.emit(
                        "tool_call",
                        node=ctx.node.name,
                        round=ctx.round,
                        data={"tool": call.name, "denied": False, "arguments": call.arguments},
                    )
                    observation = execute_tool(call.name, call.arguments, ctx.executor, node=ctx.node.name)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": observation})
            continue
        text = response.text
        finish_reason = "final"
        break
    if finish_reason != "final":
        return NodeResult(
            status="fail",
            output={"steps": max_steps},
            error=f"E-LLM: no final answer after {max_steps} model turns",
            usage=total_usage,
            started_at=started,
            ended_at=utc_now(),
        )
    try:
        data = parse_json_response(text)
    except Exception as exc:  # noqa: BLE001
        return NodeResult(
            status="fail",
            output={"text": text[:4000]},
            error=f"E-LLM: {exc}",
            usage=total_usage,
            started_at=started,
            ended_at=utc_now(),
        )
    data = _normalise(ctx, data)
    artifacts = _build_artifacts(ctx, data)
    output = dict(data)
    output.setdefault("_agent", agent.get("name") or ctx.node.name)
    return NodeResult(
        status="ok",
        output=output,
        artifacts=artifacts,
        usage=total_usage,
        started_at=started,
        ended_at=utc_now(),
    )
