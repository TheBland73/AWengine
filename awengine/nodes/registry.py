"""Node discovery and resolution.

Built-ins are a dict; project nodes are discovered from ``<cwd>/nodes/*.py``
and from the ``awengine.nodes.code`` package.  Adding a node never requires
editing the engine or this registry: drop a module with ``run(ctx)`` (or an
``AGENT`` dict for LLM agents) and it appears in ``awf nodes``.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .builtin.nodes import BUILTIN_DESCRIPTIONS, BUILTIN_HANDLERS

BUILTIN_NAMES = frozenset(BUILTIN_HANDLERS)


@dataclass(frozen=True)
class NodeInfo:
    name: str
    kind: str
    source: str
    description: str = ""
    impl: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "kind": self.kind,
            "source": self.source,
            "description": self.description,
            "impl": self.impl,
        }


def discover_nodes(project_dir: Path | None = None) -> list[NodeInfo]:
    found: dict[str, NodeInfo] = {}
    for name, handler in BUILTIN_HANDLERS.items():
        found[f"builtin:{name}"] = NodeInfo(
            name=name,
            kind="builtin",
            source="awengine.nodes.builtin",
            description=BUILTIN_DESCRIPTIONS.get(name, ""),
            impl=name,
        )
    for module in _package_modules("awengine.nodes.code"):
        info = _info_from_module(module, source="awengine.nodes.code")
        if info:
            found[f"code:{info.name}"] = info
    if project_dir is not None:
        nodes_dir = Path(project_dir) / "nodes"
        if nodes_dir.is_dir():
            for path in sorted(nodes_dir.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                try:
                    module = _import_file_module(path)
                except Exception:  # noqa: BLE001 - a broken node must not break listing
                    continue
                info = _info_from_module(module, source="project")
                if info:
                    found[f"project:{info.name}"] = info
    return sorted(found.values(), key=lambda item: (item.kind, item.name))


def _package_modules(package_name: str):
    try:
        package = importlib.import_module(package_name)
    except ImportError:
        return
    for info in pkgutil.iter_modules(package.__path__, prefix=f"{package_name}."):
        if info.name.rsplit(".", 1)[-1].startswith("_"):
            continue
        try:
            yield importlib.import_module(info.name)
        except Exception:  # noqa: BLE001
            continue


def _import_file_module(path: Path):
    import importlib.util
    import sys

    module_name = f"awf_project_nodes.{path.stem}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _info_from_module(module: Any, *, source: str) -> NodeInfo | None:
    if module is None:
        return None
    short = module.__name__.rsplit(".", 1)[-1]
    agent = getattr(module, "AGENT", None)
    if isinstance(agent, dict):
        return NodeInfo(
            name=str(agent.get("name") or short),
            kind=str(agent.get("kind") or "llm_agent"),
            source=source,
            description=str(agent.get("description") or ""),
            impl=module.__name__,
        )
    if callable(getattr(module, "run", None)):
        return NodeInfo(
            name=str(getattr(module, "NODE_NAME", short)),
            kind=str(getattr(module, "KIND", "python")),
            source=source,
            description=str(getattr(module, "DESCRIPTION", "")),
            impl=f"{module.__name__}:run",
        )
    return None


def handler_for(node_type: str, impl: str, *, project_dir: Path | None = None) -> Callable[..., Any]:
    if node_type == "builtin":
        if impl not in BUILTIN_HANDLERS:
            raise KeyError(f"unknown builtin {impl!r}")
        return BUILTIN_HANDLERS[impl]
    module_name, _, attribute = impl.partition(":")
    if not module_name:
        module_name, _, attribute = impl.rpartition(".")
    module = importlib.import_module(module_name)
    if attribute:
        return getattr(module, attribute)
    if callable(getattr(module, "run", None)):
        return module.run
    raise AttributeError(f"{impl!r} has no callable 'run' or attribute")


def find_node_module(name: str) -> Any:
    for candidate in (name, f"nodes.{name}", f"awengine.nodes.{name}", f"awengine.nodes.code.{name}"):
        try:
            return importlib.import_module(candidate)
        except ImportError:
            continue
    return None
