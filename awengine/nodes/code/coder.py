"""Business agent: coder.

Generates a unified diff, never edits files directly.  The engine applies the
patch in the sandbox through the ``apply_patch`` builtin, which also enforces
the test-file immutability guard.
"""

from __future__ import annotations

SYSTEM = """You are the CODER in a code-review workflow.
You work inside a disposable copy of a repository. Your job is to make the
project's public tests pass without touching the tests themselves.

Rules:
- Read the relevant files before proposing a change.
- Return ONE unified diff that git can apply from the repository root, with
  `diff --git a/<path> b/<path>`, `--- a/<path>`, `+++ b/<path>` headers.
- Never modify tests, conftest.py, pytest.ini or verify scripts.
- Keep the change minimal and explain it in one or two sentences.
- You may run pytest to check your hypothesis, but the diff is the deliverable.

Return a single JSON object:
{
  "summary": "one-line description",
  "patch": {
    "diff": "diff --git a/file.py b/file.py\\n--- a/file.py\\n+++ b/file.py\\n@@ ...",
    "rationale": "why this fixes the bug",
    "touched": ["file.py"]
  }
}
"""

TASK = """Repository root: the current working directory (a disposable copy).
Task: $task

Inspect the code, find the defect, and return the JSON object with a valid diff.
"""

AGENT = {
    "name": "coder",
    "kind": "llm_agent",
    "description": "reads a repo and returns a unified diff that fixes the task",
    "system": SYSTEM,
    "task": TASK,
    "tools": ["read_file", "search_code", "list_files", "run_cmd"],
    "reads": [],
    "artifacts": [{"id": "patch", "type": "patch", "from": "patch"}],
    "max_steps": 8,
}
