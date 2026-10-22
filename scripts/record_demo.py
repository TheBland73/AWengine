"""Record the checked-in cassettes for ``hello`` and ``bugfix``.

This script never touches the network.  It runs the real engine with a
``ScriptedLLM`` wrapped in a ``RecordingLLM``, so the cassette keys are produced
by the same code path a live provider would use.  Re-run it after changing an
agent prompt or a demo workflow:

    python scripts/record_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from awengine.budget import Budget, load_pricing  # noqa: E402
from awengine.checkpoint import RunStore  # noqa: E402
from awengine.engine import RunOptions, WorkflowEngine  # noqa: E402
from awengine.events import EventBus  # noqa: E402
from awengine.llm.fake import CassetteStore, RecordingLLM, ScriptedLLM, response  # noqa: E402
from awengine.loader import load_spec  # noqa: E402


def responder(model, messages, tools, turn):
    system = str(messages[0].get("content") or "")
    transcript = "\n".join(str(message.get("content") or "") for message in messages)
    if "CODER" in system:
        diff = (
            "diff --git a/calc.py b/calc.py\n"
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -6,2 +6,2 @@\n"
            " def add(a, b):\n"
            "-    return a - b\n"
            "+    return a + b\n"
        )
        payload = {
            "summary": "add() returned a - b; return the sum instead.",
            "patch": {
                "diff": diff,
                "rationale": "The public test expects 2 + 3 == 5.",
                "touched": ["calc.py"],
            },
        }
        return response(json.dumps(payload), prompt_tokens=420, completion_tokens=120)
    if "FIXER" in system:
        diff = (
            "diff --git a/calc.py b/calc.py\n"
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -10,2 +10,2 @@\n"
            " def mul(a, b):\n"
            "-    return a + b\n"
            "+    return a * b\n"
        )
        payload = {
            "summary": "Fixed R1-1: mul() added its arguments instead of multiplying.",
            "patch": {
                "diff": diff,
                "rationale": "test_mul expects 3 * 4 == 12 and the holdout expects -2 * 5 == -10.",
                "touched": ["calc.py"],
            },
        }
        return response(json.dumps(payload), prompt_tokens=640, completion_tokens=140)
    if "REVIEWER" in system and "apply_fix" not in transcript:
        payload = {
            "summary": (
                "add() is fixed, but mul() still returns a + b, so the public "
                "test_mul must fail. The coder stopped after the first defect."
            ),
            "issues": [
                {
                    "severity": "blocker",
                    "file": "calc.py",
                    "line": 11,
                    "message": "mul() adds instead of multiplying; test_mul must fail",
                    "evidence": [{"path": "calc.py", "line": 11, "quote": "return a + b"}],
                    "suggestion": "return a * b",
                }
            ],
        }
        return response(json.dumps(payload), prompt_tokens=560, completion_tokens=140)
    if "REVIEWER" in system:
        payload = {
            "summary": "Both add() and mul() now match their contracts; no issues remain.",
            "issues": [],
        }
        return response(json.dumps(payload), prompt_tokens=620, completion_tokens=70)
    if "ANALYST" in system:
        payload = {
            "brief": (
                "AWEngine is a YAML-declared workflow engine for collaborative "
                "agents. A workflow lists nodes and edges; the engine passes typed "
                "artifacts and control signals between agents. Collaboration "
                "matters because discovery, implementation and review are "
                "separate responsibilities."
            ),
            "facts": [
                "workflows are declared in YAML",
                "agents exchange typed artifacts",
                "the engine owns verification and convergence",
            ],
        }
        return response(json.dumps(payload), prompt_tokens=360, completion_tokens=150)
    if "WRITER" in system:
        payload = {
            "title": "AWEngine at a glance",
            "doc": (
                "# AWEngine at a glance\n\n"
                "AWEngine is a small workflow engine for collaborative agents. "
                "A workflow is declared in YAML and the engine moves typed "
                "artifacts between its nodes.\n\n"
                "## Collaboration\n\n"
                "The analyst, writer and editor are separate agents. Each one "
                "owns a different responsibility, so a later agent can inspect "
                "the earlier agent's work instead of silently trusting it.\n\n"
                "## Verification\n\n"
                "The engine, not an agent, runs the verify command and decides "
                "whether the workflow passed its goal.\n"
            ),
        }
        return response(json.dumps(payload), prompt_tokens=410, completion_tokens=180)
    if "EDITOR" in system:
        payload = {
            "summary": "The note contains all required terms and has no unsupported claims.",
            "issues": [],
            "verdict": "ready",
        }
        return response(json.dumps(payload), prompt_tokens=300, completion_tokens=60)
    payload = {"greeting": "Hello, world!", "words": 2}
    return response(json.dumps(payload), prompt_tokens=90, completion_tokens=20)


def record(workflow: str, *, workdir: Path, runs_root: Path, store: CassetteStore) -> None:
    spec = load_spec(ROOT / "workflows" / f"{workflow}.yaml")
    run_store = RunStore(runs_root)
    run_id = run_store.new_run_id(workflow)
    bus = EventBus(run_id, run_dir=run_store.run_dir(run_id))
    engine = WorkflowEngine(
        spec,
        options=RunOptions(
            workdir=workdir,
            runs_root=runs_root,
            project_dir=ROOT,
            fake=False,
        ),
        bus=bus,
        provider=RecordingLLM(ScriptedLLM(responder=responder), store),
        budget=Budget(load_pricing(ROOT / "pricing.toml"), None),
    )
    state = engine.start({}, run_id=run_id)
    bus.close()
    print(f"{workflow}: {state.terminal} ({state.stop_reason})  cassettes={store.count()}")


def main() -> int:
    store = CassetteStore(ROOT / "tests" / "cassettes", read_only=False)
    runs_root = ROOT / "runs" / "_record"
    record("hello", workdir=ROOT / "fixtures" / "calcfix", runs_root=runs_root, store=store)
    record("bugfix", workdir=ROOT / "fixtures" / "calcfix", runs_root=runs_root, store=store)
    record("docsync", workdir=ROOT / "fixtures" / "docsync", runs_root=runs_root, store=store)
    print(f"wrote cassettes under {store.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
