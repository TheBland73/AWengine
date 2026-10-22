"""Business agent: reviewer.

Discovery only.  The reviewer reports issues with evidence; it does not decide
whether the run passed.  Issues without evidence are dropped,
and the remaining ones are sorted by severity so the fixer sees blockers first.
"""

from __future__ import annotations

from typing import Any

SYSTEM = """You are the REVIEWER in a code-review workflow.
You only inspect code and report defects. You never edit files and you never
declare success; a deterministic verify command decides that.

Every issue MUST carry concrete evidence (path, line, exact quote). If you
cannot quote the code that motivates an issue, do not report it.

Severity:
- blocker: the code is wrong or a public test must fail because of it
- major: a real defect that may not be covered by tests
- minor: style or maintainability

Return a single JSON object:
{
  "summary": "one paragraph",
  "issues": [
    {
      "severity": "blocker",
      "file": "calc.py",
      "line": 4,
      "message": "add() subtracts instead of adding",
      "evidence": [{"path": "calc.py", "line": 4, "quote": "return a - b"}],
      "suggestion": "return a + b"
    }
  ]
}
An empty issues list means you found no defects.
"""

TASK = """Review the working copy in the current directory for the task: $task

Read the changed files (start with the patch digest in the context, then open
the files). Report only evidenced defects. Return the JSON object.
"""


def normalize(data: dict[str, Any], ctx) -> dict[str, Any]:
    raw_issues = data.get("issues")
    issues: list[dict[str, Any]] = []
    dropped = 0
    if isinstance(raw_issues, list):
        for raw in raw_issues:
            if not isinstance(raw, dict):
                dropped += 1
                continue
            evidence = raw.get("evidence")
            clean_evidence: list[dict[str, Any]] = []
            if isinstance(evidence, list):
                for item in evidence:
                    if not isinstance(item, dict):
                        continue
                    quote = str(item.get("quote") or "").strip()
                    path = str(item.get("path") or raw.get("file") or "").strip()
                    if not quote or not path:
                        continue
                    line = item.get("line", raw.get("line"))
                    clean_evidence.append(
                        {
                            "path": path,
                            "line": int(line) if isinstance(line, (int, float)) else None,
                            "quote": quote[:500],
                        }
                    )
            if not clean_evidence:
                dropped += 1
                continue
            severity = str(raw.get("severity") or "minor").lower()
            if severity not in {"blocker", "major", "minor"}:
                severity = "major"
            issues.append(
                {
                    "severity": severity,
                    "file": str(raw.get("file") or clean_evidence[0]["path"]),
                    "line": raw.get("line") if isinstance(raw.get("line"), int) else clean_evidence[0].get("line"),
                    "message": str(raw.get("message") or "").strip()[:1000],
                    "evidence": clean_evidence,
                    "suggestion": str(raw.get("suggestion") or "").strip()[:1000],
                }
            )
    order = {"blocker": 0, "major": 1, "minor": 2}
    issues.sort(key=lambda item: order.get(str(item["severity"]), 3))
    for index, issue in enumerate(issues, 1):
        issue["id"] = f"R{ctx.state.runtime.round}-{index}"
    blockers = sum(1 for issue in issues if issue["severity"] == "blocker")
    majors = sum(1 for issue in issues if issue["severity"] == "major")
    if dropped:
        ctx.warn(f"reviewer reported {dropped} issue(s) without evidence; dropped")
    return {
        "issues": issues,
        "blockers": blockers,
        "majors": majors,
        "dropped": dropped,
        "summary": str(data.get("summary") or "").strip(),
        "verdict": "changes_requested" if blockers else "clean",
    }


AGENT = {
    "name": "reviewer",
    "kind": "llm_agent",
    "description": "evidence-only code reviewer; never writes",
    "system": SYSTEM,
    "task": TASK,
    "tools": ["read_file", "search_code", "list_files", "run_cmd"],
    "reads": ["patch"],
    "artifacts": [{"id": "review", "type": "review", "from": "__self__"}],
    "normalize": normalize,
    "max_steps": 8,
}
