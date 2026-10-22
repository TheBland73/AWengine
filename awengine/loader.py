"""Load, validate and normalise workflow YAML.

The loader is deliberately strict about structure and permissive about unknown
keys (with a warning), so this engine can read files written for older
releases.  It is also the only place that implements ``--disable``: disabled
nodes are spliced out of the graph *before* hashing, so the run manifest, the
Mermaid graph and the report all describe the same effective workflow.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml
from pydantic import ValidationError

from .contracts import canonical_json
from .errors import SpecError
from .expr import ExprError, compile_expr
from .spec import (
    ConvergeSpec,
    Defaults,
    Edge,
    NodeSpec,
    VerifySpec,
    WorkflowSpec,
)

WarnFn = Callable[[str], None]

_TOP_KEYS = {"name", "version", "nodes", "edges", "converge", "verify", "defaults", "description"}
_NODE_KEYS = {
    "name",
    "type",
    "impl",
    "cmd",
    "config",
    "contract",
    "retry",
    "timeout_s",
    "enabled",
}
_DEFAULT_KEYS = {"model", "temperature", "max_tokens", "timeout_s", "max_steps"}
_MAX_SPEC_BYTES = 512 * 1024


def _warn(warn: WarnFn | None, message: str) -> None:
    if warn is not None:
        warn(message)


def _read_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SpecError("SPEC_NOT_FOUND", f"cannot read workflow: {exc}", path=str(path)) from exc
    if size > _MAX_SPEC_BYTES:
        raise SpecError(
            "SPEC_TOO_LARGE",
            f"workflow file is {size} bytes (limit {_MAX_SPEC_BYTES})",
            path=str(path),
        )
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SpecError("SPEC_NOT_FOUND", f"cannot read workflow: {exc}", path=str(path)) from exc


def _node_lines(text: str) -> list[tuple[str, int]]:
    """Extract (name, line) for each entry of the top-level ``nodes`` list."""
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return []
    if not isinstance(root, yaml.MappingNode):
        return []
    result: list[tuple[str, int]] = []
    for key, value in root.value:
        if getattr(key, "value", None) != "nodes" or not isinstance(value, yaml.SequenceNode):
            continue
        for item in value.value:
            if not isinstance(item, yaml.MappingNode):
                continue
            name = ""
            for ik, iv in item.value:
                if getattr(ik, "value", None) == "name" and isinstance(iv, yaml.ScalarNode):
                    name = str(iv.value)
            result.append((name, item.start_mark.line + 1))
    return result


def _edge_lines(text: str) -> list[int]:
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return []
    if not isinstance(root, yaml.MappingNode):
        return []
    for key, value in root.value:
        if getattr(key, "value", None) == "edges" and isinstance(value, yaml.SequenceNode):
            return [item.start_mark.line + 1 for item in value.value]
    return []


def _combine_when(outer: str | None, inner: str | None) -> str | None:
    if outer and inner:
        return f"({outer}) and ({inner})"
    return outer or inner or None


def _apply_disable(
    nodes: list[NodeSpec],
    edges: list[Edge],
    disabled: Iterable[str],
) -> tuple[list[NodeSpec], list[Edge]]:
    names = {node.name for node in nodes}
    requested = list(dict.fromkeys(disabled))
    unknown = [name for name in requested if name not in names]
    if unknown:
        raise SpecError(
            "SPEC_UNKNOWN_DISABLE",
            f"--disable refers to unknown node(s): {', '.join(unknown)}",
            f"known nodes: {', '.join(sorted(names))}",
        )
    if not requested:
        return nodes, edges

    active = [node for node in nodes if node.name not in set(requested)]
    current = list(edges)
    for name in requested:
        ins = [edge for edge in current if edge.dst == name]
        outs = [edge for edge in current if edge.src == name]
        if len(ins) > 1 or len(outs) > 1:
            raise SpecError(
                "SPEC_DISABLED_AMBIGUOUS",
                f"cannot disable {name!r}: it has {len(ins)} incoming and {len(outs)} outgoing edges",
                "a disabled node is only spliced when it has at most one in-edge and one out-edge",
            )
        current = [edge for edge in current if edge.src != name and edge.dst != name]
        if len(ins) == 1 and len(outs) == 1 and outs[0].dst != name:
            current.append(
                Edge(
                    src=ins[0].src,
                    dst=outs[0].dst,
                    when=_combine_when(ins[0].when, outs[0].when),
                )
            )
    if not active:
        raise SpecError("SPEC_NO_ACTIVE_NODE", "all nodes are disabled; nothing to run")
    return active, current


def _validate_entry(spec: WorkflowSpec, warn: WarnFn | None) -> None:
    names = [node.name for node in spec.nodes]
    roots = [name for name in names if not any(edge.dst == name for edge in spec.edges)]
    if not roots:
        raise SpecError(
            "SPEC_NO_ENTRY",
            "every node has an incoming edge; the workflow has no entry node",
            "add at least one node without incoming edges",
        )
    if len(roots) > 1:
        raise SpecError(
            "SPEC_MULTI_ENTRY",
            f"multiple entry nodes: {', '.join(roots)}",
            "only one node may have no incoming edges; connect the others",
        )
    reachable = spec.reachable()
    unreachable = [name for name in names if name not in reachable]
    if unreachable:
        _warn(warn, f"unreachable node(s) ignored by traversal: {', '.join(unreachable)}")


def _build_node(raw: dict[str, Any], defaults: Defaults, line: int | None) -> NodeSpec:
    data = dict(raw)
    data.setdefault("timeout_s", defaults.timeout_s)
    if "enabled" in data and not isinstance(data["enabled"], bool):
        raise SpecError("SPEC_NODE_INVALID", "enabled must be a boolean", line=line)
    try:
        return NodeSpec(**data, line=line)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        message = f"{where}: {first['msg']}" if where else first["msg"]
        raise SpecError("SPEC_NODE_INVALID", message, line=line) from exc
    except ValueError as exc:
        raise SpecError("SPEC_NODE_INVALID", str(exc), line=line) from exc


def _build_converge(raw: dict[str, Any] | None, spec_has_verify: bool, has_loop: bool) -> ConvergeSpec:
    data = dict(raw or {})
    raw_goal = data.get("goal")
    if raw_goal is None and spec_has_verify:
        # Generic default: evidence decides.  Workflows that also involve a
        # reviewer should spell out "verify.passed and reviewer.blockers == 0".
        data["goal"] = "verify.passed"
    try:
        converge = ConvergeSpec(**data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise SpecError("SPEC_CONVERGE_INVALID", f"{where}: {first['msg']}") from exc
    except ValueError as exc:
        raise SpecError("SPEC_CONVERGE_INVALID", str(exc)) from exc

    if converge.goal is not None:
        try:
            compile_expr(converge.goal)
        except ExprError as exc:
            raise SpecError(
                "SPEC_CONVERGE_INVALID",
                f"goal expression: {exc.message}",
                "allowed: names, attributes, comparisons, and/or/not, literals",
            ) from exc
    try:
        compile_expr(converge.progress.key)
    except ExprError as exc:
        raise SpecError(
            "SPEC_CONVERGE_INVALID", f"progress.key is not a path: {exc.message}"
        ) from exc
    if converge.rounds.min > 1 and not has_loop:
        raise SpecError(
            "SPEC_CONVERGE_UNREACHABLE",
            f"converge.rounds.min={converge.rounds.min} requires a loop, but the graph has no back edge",
            "add an edge back to an earlier node, or set rounds.min to 1",
        )
    if converge.escalate.after_round > 0 and not has_loop:
        raise SpecError(
            "SPEC_CONVERGE_UNREACHABLE",
            "converge.escalate requires a loop but the graph has no back edge",
        )
    if converge.escalate.max_times > 0 and converge.escalate.after_round <= 0:
        raise SpecError(
            "SPEC_CONVERGE_INVALID",
            "converge.escalate.max_times requires converge.escalate.after_round",
        )
    return converge


def _build_verify(raw: dict[str, Any] | None, base_dir: Path | None, warn: WarnFn | None) -> VerifySpec | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = {"cmd": raw, "level": 1}
    if not isinstance(raw, dict):
        raise SpecError("SPEC_VERIFY_INVALID", "verify must be a mapping or a command string")
    try:
        verify = VerifySpec(**raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise SpecError("SPEC_VERIFY_INVALID", f"{where}: {first['msg']}") from exc
    if base_dir is not None and verify.holdout:
        holdout = Path(verify.holdout)
        resolved = holdout if holdout.is_absolute() else (base_dir / holdout)
        if not resolved.exists():
            raise SpecError(
                "SPEC_VERIFY_HOLDOUT_MISSING",
                f"holdout not found: {verify.holdout} (resolved to {resolved})",
                "holdouts are resolved against the workflow file's directory",
            )
    if verify.level >= 2 and not verify.holdout:
        raise SpecError(
            "SPEC_VERIFY_HOLDOUT_MISSING",
            f"verify.level={verify.level} requires verify.holdout",
        )
    if verify.level >= 3 and not verify.red_before:
        _warn(warn, "verify.level >= 3 should set red_before: true to catch a verify that cannot fail")
    return verify


def load_spec_text(
    text: str,
    *,
    path: str | None = None,
    disable: Iterable[str] = (),
    warn: WarnFn | None = None,
) -> WorkflowSpec:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        raise SpecError("SPEC_PARSE", f"invalid YAML: {getattr(exc, 'problem', exc)}", line=line, path=path) from exc
    if not isinstance(data, dict):
        raise SpecError("SPEC_ROOT", "workflow root must be a mapping", path=path)

    for key in data:
        if key not in _TOP_KEYS:
            _warn(warn, f"unknown top-level key {key!r} ignored")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SpecError("SPEC_NAME", "workflow needs a non-empty string 'name'", path=path)
    version = data.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise SpecError("SPEC_VERSION", "workflow 'version' must be a positive integer", path=path)

    try:
        defaults = Defaults(**(data.get("defaults") or {}))
    except (ValidationError, TypeError) as exc:
        raise SpecError("SPEC_DEFAULT_INVALID", f"defaults: {exc}", path=path) from exc
    for key in (data.get("defaults") or {}):
        if key not in _DEFAULT_KEYS:
            _warn(warn, f"unknown defaults key {key!r} ignored")

    raw_nodes = data.get("nodes")
    if isinstance(raw_nodes, dict):
        # Mapping form is the documented shorthand: the key is the node name.
        normalised: list[dict[str, Any]] = []
        for node_name, raw_node in raw_nodes.items():
            if not isinstance(raw_node, dict):
                raise SpecError(
                    "SPEC_NODE_INVALID",
                    f"node {node_name!r} must be a mapping",
                    path=path,
                )
            if "name" in raw_node and raw_node["name"] != node_name:
                raise SpecError(
                    "SPEC_NODE_INVALID",
                    f"node key {node_name!r} disagrees with name {raw_node['name']!r}",
                    path=path,
                )
            merged = dict(raw_node)
            merged["name"] = node_name
            normalised.append(merged)
        raw_nodes = normalised
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise SpecError("SPEC_NO_NODES", "workflow needs a non-empty 'nodes' list or mapping", path=path)

    line_pairs = _node_lines(text)
    node_lines: dict[int, int] = {}
    for index, (_name, line) in enumerate(line_pairs):
        node_lines[index] = line

    nodes: list[NodeSpec] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(raw_nodes):
        line = node_lines.get(index)
        if not isinstance(raw, dict):
            raise SpecError("SPEC_NODE_INVALID", "each node must be a mapping", line=line, path=path)
        node_name = raw.get("name")
        if not isinstance(node_name, str) or not node_name.strip():
            raise SpecError("SPEC_NODE_INVALID", "node needs a non-empty string 'name'", line=line, path=path)
        if node_name in seen:
            raise SpecError(
                "SPEC_DUP_NODE",
                f"duplicate node name {node_name!r} (first defined at line {seen[node_name]})",
                line=line,
                path=path,
            )
        seen[node_name] = line or 0
        for key in raw:
            if key not in _NODE_KEYS:
                _warn(warn, f"node {node_name!r}: unknown key {key!r} ignored")
        nodes.append(_build_node(raw, defaults, line))

    node_names = {node.name for node in nodes}
    raw_edges = data.get("edges") or []
    if not isinstance(raw_edges, list):
        raise SpecError("SPEC_EDGE_INVALID", "'edges' must be a list", path=path)
    edge_lines = _edge_lines(text)
    edges: list[Edge] = []
    seen_edges: set[tuple[str, str, str | None]] = set()
    for index, raw in enumerate(raw_edges):
        line = edge_lines[index] if index < len(edge_lines) else None
        if not isinstance(raw, dict):
            raise SpecError("SPEC_EDGE_INVALID", "each edge must be a mapping", line=line, path=path)
        src = raw.get("from", raw.get("src"))
        dst = raw.get("to", raw.get("dst"))
        if not isinstance(src, str) or not isinstance(dst, str):
            raise SpecError(
                "SPEC_EDGE_INVALID",
                "edge needs 'from' and 'to' strings",
                line=line,
                path=path,
            )
        if src not in node_names:
            raise SpecError("SPEC_UNKNOWN_EDGE", f"edge from unknown node {src!r}", line=line, path=path)
        if dst != "END" and dst not in node_names:
            raise SpecError("SPEC_UNKNOWN_EDGE", f"edge to unknown node {dst!r}", line=line, path=path)
        when = raw.get("when")
        try:
            edge = Edge(src=src, dst=dst, when=when)
        except (ValidationError, ValueError) as exc:
            raise SpecError("SPEC_EDGE_INVALID", f"invalid edge: {exc}", line=line, path=path) from exc
        if edge.when is not None:
            try:
                compile_expr(edge.when)
            except ExprError as exc:
                raise SpecError(
                    "SPEC_EDGE_INVALID",
                    f"edge {src}->{dst} condition: {exc.message}",
                    line=line,
                    path=path,
                ) from exc
        key = (edge.src, edge.dst, edge.when)
        if key in seen_edges:
            _warn(warn, f"duplicate edge {src}->{dst} ignored")
        else:
            seen_edges.add(key)
            edges.append(edge)

    # A node may only be referenced by an edge if it is reachable from the
    # entry; the entry itself is validated after --disable has been applied.
    provisional = WorkflowSpec(
        name=name.strip(),
        version=version,
        nodes=tuple(nodes),
        edges=tuple(edges),
        defaults=defaults,
        source_path=path,
    )
    active_nodes, active_edges = _apply_disable(nodes, edges, disable)
    has_loop = bool(
        WorkflowSpec(
            name=name,
            nodes=tuple(active_nodes),
            edges=tuple(active_edges),
        ).loop_heads
    )
    verify = _build_verify(data.get("verify"), Path(path).resolve().parent if path else None, warn)
    converge = _build_converge(data.get("converge"), verify is not None, has_loop)

    spec = WorkflowSpec(
        name=name.strip(),
        version=version,
        nodes=tuple(active_nodes),
        edges=tuple(active_edges),
        converge=converge,
        verify=verify,
        defaults=defaults,
        source_path=path,
    )
    _validate_entry(spec, warn)
    for node in spec.nodes:
        if node.type == "builtin" and node.impl not in {"noop", "log", "set", "assert"}:
            # Imported lazily to avoid a loader -> nodes -> engine cycle.
            from .nodes.registry import BUILTIN_NAMES

            if node.impl not in BUILTIN_NAMES:
                raise SpecError(
                    "SPEC_UNKNOWN_BUILTIN",
                    f"node {node.name!r}: unknown builtin impl {node.impl!r}",
                    f"known builtins: {', '.join(sorted(BUILTIN_NAMES))}",
                    line=node.line,
                    path=path,
                )
    return spec


def load_spec(
    path: str | os.PathLike[str],
    *,
    disable: Iterable[str] = (),
    warn: WarnFn | None = None,
) -> WorkflowSpec:
    file_path = Path(path)
    text = _read_text(file_path)
    return load_spec_text(text, path=str(file_path), disable=disable, warn=warn)


def find_workflow(reference: str, *, extra_dirs: Iterable[Path] = ()) -> Path:
    """Resolve ``hello`` / ``workflows/hello.yaml`` / an absolute path."""
    candidate = Path(reference)
    if candidate.is_file():
        return candidate
    names = [reference]
    if not reference.endswith((".yaml", ".yml")):
        names += [f"{reference}.yaml", f"{reference}.yml"]
    search: list[Path] = [Path.cwd(), Path.cwd() / "workflows"]
    search.extend(Path(item) for item in extra_dirs)
    package_root = Path(__file__).resolve().parent.parent
    search.append(package_root / "workflows")
    for name in names:
        probe = Path(name)
        if probe.is_file():
            return probe
        for base in search:
            target = base / name
            if target.is_file():
                return target
    raise SpecError(
        "SPEC_NOT_FOUND",
        f"cannot find workflow {reference!r}",
        "pass a path or create workflows/<name>.yaml; try `awf presets`",
    )


def spec_to_yaml(spec: WorkflowSpec) -> str:
    """Debug helper - never used for scaffolding (comments must survive)."""
    payload = spec.model_dump(mode="json", exclude={"source_path"})
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def spec_fingerprint(spec: WorkflowSpec) -> str:
    return canonical_json(spec.to_hashable())
