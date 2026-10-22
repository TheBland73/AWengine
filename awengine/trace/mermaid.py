"""Mermaid views: flow from the spec, sequence from the real event stream."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from ..spec import WorkflowSpec

_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _node_id(name: str) -> str:
    safe = _SAFE.sub("_", name)
    return f"n_{safe}" if not safe[:1].isdigit() else f"n_{safe}"


def _label(text: str, limit: int = 60) -> str:
    text = str(text).replace('"', "'").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def flow_mermaid(spec: WorkflowSpec) -> str:
    lines = ["flowchart TD"]
    for node in spec.nodes:
        label = f"{node.name}<br/><small>{node.type}</small>"
        if node.name == spec.entry:
            shape_open, shape_close = "([", "])"
        else:
            shape_open, shape_close = "[", "]"
        if node.type == "llm_agent":
            shape_open, shape_close = "{{", "}}"
        elif node.type == "process":
            shape_open, shape_close = "[[", "]]"
        lines.append(f'    {_node_id(node.name)}{shape_open}"{_label(label, 80)}"{shape_close}')
    has_end = any(edge.dst == "END" for edge in spec.edges)
    if has_end:
        lines.append('    __END__(["END"])')
    for edge in spec.edges:
        dst = "__END__" if edge.dst == "END" else _node_id(edge.dst)
        if edge.when:
            lines.append(f'    {_node_id(edge.src)} -->|"{_label(edge.when, 40)}"| {dst}')
        else:
            lines.append(f"    {_node_id(edge.src)} --> {dst}")
    if spec.verify:
        lines.append(f'    __VERIFY__["verify L{spec.verify.level}: {_label(spec.verify.cmd, 34)}"]')
    lines.append("    classDef agent fill:#e8f0fe,stroke:#4a6fa5,color:#10243f;")
    lines.append("    classDef gate fill:#fdecea,stroke:#b3261e,color:#5f1712;")
    lines.append("    classDef process fill:#eef7ee,stroke:#2e7d32,color:#123d16;")
    lines.append("    classDef terminal fill:#f3e8fd,stroke:#6a1b9a,color:#2a0845;")
    for node in spec.nodes:
        cls = {"llm_agent": "agent", "process": "process"}.get(node.type, "gate")
        lines.append(f"    class {_node_id(node.name)} {cls};")
    if spec.verify:
        lines.append("    class __VERIFY__ gate;")
    if any(edge.dst == "END" for edge in spec.edges):
        lines.append("    class __END__ terminal;")
    return "\n".join(lines) + "\n"


def sequence_mermaid(events: Iterable[dict[str, Any]], *, limit: int = 400) -> str:
    events = list(events)
    participants: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> str:
        name = name or "engine"
        if name not in seen:
            seen.add(name)
            participants.append(name)
        return "P" + _SAFE.sub("_", name) or f"P{len(participants)}"

    # deterministic ids
    ids: dict[str, str] = {}
    for event in events:
        for candidate in (event.get("node"), (event.get("data") or {}).get("src"), (event.get("data") or {}).get("dst")):
            if isinstance(candidate, str) and candidate and candidate not in ids:
                ids[candidate] = add(candidate)
    ids.setdefault("engine", "PE")
    ids.setdefault("LLM", "PLLM")
    ids.setdefault("Tool", "PTool")

    lines = ["sequenceDiagram", "    autonumber"]
    for name in ["engine", *participants, "LLM", "Tool"]:
        if name in ids:
            label = name if name in ("engine", "LLM", "Tool") else name
            lines.append(f"    participant {ids[name]} as {_label(label, 30)}")
    count = 0
    for event in events:
        if count >= limit:
            lines.append("    Note over " + ids["engine"] + ": ... (truncated)")
            break
        etype = event.get("type")
        node = event.get("node")
        data = event.get("data") or {}
        pid = ids.get(node or "", ids["engine"]) if node else ids["engine"]
        if etype == "node_start":
            lines.append(f'    {ids["engine"]}->>{pid}: run {node} (r{event.get("round")})')
            count += 1
        elif etype == "llm_call":
            lines.append(f'    {pid}->>{ids["LLM"]}: {_label(data.get("model", "model"), 24)} +{data.get("completion_tokens", 0)} tok')
            lines.append(f'    {ids["LLM"]}-->>{pid}: reply')
            count += 2
        elif etype == "tool_call":
            tool = _label(data.get("tool", "tool"), 20)
            lines.append(f'    {pid}->>{ids["Tool"]}: {tool}')
            count += 1
        elif etype == "message":
            src = ids.get(str(data.get("src")), ids["engine"])
            dst = ids.get(str(data.get("dst")), ids["engine"])
            artifacts = ", ".join(str(item) for item in data.get("artifacts") or [])
            lines.append(f"    {src}->>{dst}: {_label(str(data.get('intent', 'handoff')), 20)} {artifacts}")
            count += 1
        elif etype == "verify":
            lines.append(f'    {ids["engine"]}->>{ids["engine"]}: verify {"PASS" if data.get("passed") else "FAIL"}')
            count += 1
        elif etype == "node_end":
            lines.append(f'    {pid}-->>{ids["engine"]}: {data.get("status", "ok")}')
            count += 1
        elif etype == "stop_decision":
            lines.append(f'    Note over {ids["engine"]}: terminal={data.get("terminal")} ({data.get("failure_class") or "ok"})')
            count += 1
    return "\n".join(lines) + "\n"


def write_mermaid(run_dir: Path, spec: WorkflowSpec, events: list[dict[str, Any]]) -> dict[str, str]:
    flow = flow_mermaid(spec)
    sequence = sequence_mermaid(events)
    (run_dir / "flow.mmd").write_text(flow, encoding="utf-8", newline="\n")
    (run_dir / "sequence.mmd").write_text(sequence, encoding="utf-8", newline="\n")
    return {"flow": flow, "sequence": sequence}
