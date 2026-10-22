"""Built-in tools available to LLM agents.

Tools are deliberately few and read-mostly.  The reviewer may read, search and
run whitelisted commands; it may never write.  Denials are returned as tool
observations (and an event), not exceptions, so the model can recover.
"""

from __future__ import annotations

import json
from typing import Any

from ...executor import LocalExecutor, is_protected

READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the sandboxed workdir.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "path relative to the workdir"}},
            "required": ["path"],
        },
    },
}

SEARCH_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_code",
        "description": "Search the workdir with a Python regular expression.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob": {"type": "string", "default": "**/*.py"},
            },
            "required": ["pattern"],
        },
    },
}

RUN_CMD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_cmd",
        "description": "Run a whitelisted command (pytest/python/node) inside the workdir.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 60},
            },
            "required": ["command"],
        },
    },
}

LIST_FILES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": "List files under the workdir (excluding caches and .git).",
        "parameters": {
            "type": "object",
            "properties": {"glob": {"type": "string", "default": "**/*"}},
            "required": [],
        },
    },
}

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_file": READ_FILE_SCHEMA,
    "search_code": SEARCH_CODE_SCHEMA,
    "run_cmd": RUN_CMD_SCHEMA,
    "list_files": LIST_FILES_SCHEMA,
}

DENIED_TOOLS = {"write_file", "apply_patch", "delete_file", "shell", "edit_file"}


def schemas_for(names: list[str]) -> list[dict[str, Any]]:
    return [TOOL_SCHEMAS[name] for name in names if name in TOOL_SCHEMAS]


def execute_tool(name: str, arguments: dict[str, Any], executor: LocalExecutor, *, node: str = "") -> str:
    """Execute one tool call and return an observation string."""
    if name in DENIED_TOOLS:
        return f"DENIED: tool {name!r} is not available to this agent"
    try:
        if name == "read_file":
            path = str(arguments.get("path") or "")
            text = executor.read_file(path)
            return text[:60_000]
        if name == "search_code":
            hits = executor.search_code(
                str(arguments.get("pattern") or ""),
                glob=str(arguments.get("glob") or "**/*.py"),
            )
            return json.dumps(hits, ensure_ascii=False, indent=2)[:30_000] or "[]"
        if name == "run_cmd":
            result = executor.run(
                str(arguments.get("command") or ""),
                timeout=int(arguments.get("timeout") or 60),
                node=node,
            )
            return json.dumps(result.to_dict(), ensure_ascii=False)
        if name == "list_files":
            pattern = str(arguments.get("glob") or "**/*")
            files: list[str] = []
            for path in executor.sandbox.glob(pattern):
                if path.is_file() and ".aw" not in path.parts and ".git" not in path.parts:
                    files.append(path.relative_to(executor.sandbox).as_posix())
                if len(files) >= 200:
                    break
            return json.dumps(sorted(files), ensure_ascii=False)
        if name in DENIED_TOOLS or name in {"write_file", "apply_patch"}:
            return f"DENIED: tool {name!r} is not allowed"
        return f"ERROR: unknown tool {name!r}"
    except Exception as exc:  # noqa: BLE001 - tool errors are observations
        return f"ERROR: {type(exc).__name__}: {exc}"


__all__ = ["TOOL_SCHEMAS", "schemas_for", "execute_tool", "is_protected"]
