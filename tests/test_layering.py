"""Layering guard-rails for dependency direction."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "awengine"

LOWER_LAYERS = {
    "contracts",
    "spec",
    "expr",
    "events",
    "checkpoint",
    "state",
    "budget",
    "guard",
    "executor",
    "context",
    "loader",
}
BUSINESS = {"coder", "reviewer", "fixer"}


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            if node.level:
                modules.add("." * node.level + node.module)
    return modules


def test_lower_layers_do_not_import_engine_or_cli() -> None:
    offenders: list[str] = []
    for name in LOWER_LAYERS:
        path = PACKAGE / f"{name}.py"
        for module in imports_of(path):
            if module in {"awengine.engine", "awengine.cli"} or module.endswith(".engine"):
                offenders.append(f"{name}.py -> {module}")
    assert not offenders, offenders


def test_engine_has_no_business_node_names() -> None:
    source = (PACKAGE / "engine.py").read_text(encoding="utf-8")
    hits = [word for word in BUSINESS if word in source]
    assert not hits, f"engine.py mentions business nodes: {hits}"


def test_business_nodes_do_not_import_engine() -> None:
    offenders: list[str] = []
    for path in (PACKAGE / "nodes" / "code").glob("*.py"):
        for module in imports_of(path):
            if module.endswith("engine") or module.endswith("state"):
                offenders.append(f"{path.name} -> {module}")
    assert not offenders, offenders
