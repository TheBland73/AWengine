"""``awf new``: scaffold a project that runs offline."""

from __future__ import annotations

import json
from pathlib import Path

from .errors import UsageError

HELLO_YAML = """\
# A minimal two-agent workflow: deterministic planning, one LLM agent,
# deterministic summarising.  Run it with:
#   python -m pytest -q          (offline smoke test, ScriptedLLM)
#   awf run hello --live         (needs DEEPSEEK_API_KEY)
name: hello
version: 1
defaults:
  model: deepseek-chat
  temperature: 0.0
  max_tokens: 512

nodes:
  plan:
    type: builtin
    impl: echo
    config:
      value: "Write one friendly greeting for the world."
  greet:
    type: llm_agent
    impl: awengine.nodes.code.greeter
    config:
      name: world
    contract:
      required: [greeting, words]
      types: {greeting: str, words: int}
    retry: 1
  wrap:
    type: builtin
    impl: set
    config:
      values:
        summary: "Greeting produced and recorded."
      artifacts:
        - id: greeting
          type: text
          from: summary

edges:
  - {src: plan, dst: greet}
  - {src: greet, dst: wrap}
  - {src: wrap, dst: END}

converge:
  goal: "greet.words >= 1"
  rounds: {min: 1, max: 1}
  steps: {min: 1, max: 6}
  attempts: {min: 0, max: 3}
  progress: {key: "greet.words", window: 2}
"""

NODE_PY = '''\
"""Project node: uppercase text.  Drop any module with run(ctx) into nodes/."""

from __future__ import annotations

from awengine.contracts import NodeResult


def run(ctx) -> NodeResult:
    text = str(ctx.config.get("text") or ctx.inputs.get("text") or "")
    return NodeResult(status="ok", output={"text": text.upper(), "length": len(text.upper())})
'''

NODE_YAML = """\
name: uppercase-demo
version: 1
nodes:
  start:
    type: python
    impl: nodes.uppercase:run
    config:
      text: "hello workflow"
  done:
    type: builtin
    impl: set
    config:
      values:
        ok: "true"
edges:
  - {src: start, dst: done}
  - {src: done, dst: END}
converge:
  goal: "start.length > 0"
  rounds: {min: 1, max: 1}
  steps: {min: 1, max: 4}
"""

TEST_PY = '''\
"""Offline smoke test for this scaffold.  No API key, no network."""

from __future__ import annotations

from pathlib import Path

from awengine.checkpoint import RunStore
from awengine.engine import RunOptions, WorkflowEngine
from awengine.events import EventBus
from awengine.llm.fake import ScriptedLLM, response
from awengine.loader import load_spec_text

WORKFLOW = """
name: scaffold-test
version: 1
nodes:
  ask:
    type: llm_agent
    impl: awengine.nodes.code.greeter
    config: {name: world}
    contract: {required: [greeting, words]}
  done:
    type: builtin
    impl: set
    config: {values: {ok: done}}
edges:
  - {src: ask, dst: done}
  - {src: done, dst: END}
converge:
  goal: "ask.words >= 1"
  rounds: {min: 1, max: 1}
  steps: {min: 1, max: 4}
"""


def test_scaffolded_workflow_passes(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "README.md").write_text("demo", encoding="utf-8")
    spec = load_spec_text(WORKFLOW)
    store = RunStore(tmp_path / "runs")
    run_id = store.new_run_id("test")
    bus = EventBus(run_id, run_dir=store.run_dir(run_id))
    provider = ScriptedLLM([response('{"greeting": "hi", "words": 1}')])
    engine = WorkflowEngine(
        spec,
        options=RunOptions(workdir=workdir, runs_root=tmp_path / "runs", fake=True),
        bus=bus,
        provider=provider,
        budget=__import__("awengine.budget", fromlist=["Budget"]).Budget(
            __import__("awengine.budget", fromlist=["Pricing"]).Pricing(models={}, version="test"),
            None,
        ),
    )
    state = engine.start({}, run_id=run_id)
    bus.close()
    assert state.terminal == "success", state.stop_detail
    assert state.outputs["ask"]["words"] == 1
'''

README = """# {name}

Scaffolded by AWEngine.  Install the engine first (from its checkout:
`python -m pip install -e <path-to-awengine>`), then:

```text
python -m pytest -q          # offline smoke test with ScriptedLLM
awf validate workflows/hello.yaml
awf run hello --live         # needs DEEPSEEK_API_KEY
```

`--fake` replay needs a cassette recorded with `--record`; the generated
`tests/cassettes/` starts empty on purpose.

## Layout

- `workflows/` - declarative YAML workflows (the only source of control flow)
- `nodes/` - project node handlers (`run(ctx) -> NodeResult`)
- `tests/` - offline tests; `tests/cassettes` holds recorded LLM replies
- `runs/` - one directory per run: `state.json`, `events.jsonl`, `report.md`, `replay.html`

## Add an agent

1. Write a module with `run(ctx)` (see `nodes/uppercase.py`) or an `AGENT` dict
   (see `awengine/nodes/code/greeter.py`).
2. Reference it from a workflow node via `impl:`.
3. Run `awf nodes` to confirm it is discovered; no engine change is needed.
"""

GITIGNORE = """runs/
__pycache__/
*.pyc
.venv/
.pytest_cache/
"""


def create_project(name: str, destination: Path, *, template: str = "hello", force: bool = False) -> Path:
    if not name or name in (".", ".."):
        raise UsageError("E-SCAFFOLD-NAME", "project name must be a non-empty directory name")
    root = Path(destination)
    if root.exists() and any(root.iterdir()) and not force:
        raise UsageError(
            "E-SCAFFOLD-EXISTS",
            f"destination is not empty: {root}",
            "choose another directory or pass --force",
        )
    root.mkdir(parents=True, exist_ok=True)
    for directory in ("workflows", "nodes", "tests", "runs", "tests/cassettes"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "workflows" / "hello.yaml").write_text(HELLO_YAML, encoding="utf-8", newline="\n")
    (root / "workflows" / "uppercase.yaml").write_text(NODE_YAML, encoding="utf-8", newline="\n")
    (root / "nodes" / "uppercase.py").write_text(NODE_PY, encoding="utf-8", newline="\n")
    (root / "tests" / "test_workflow.py").write_text(TEST_PY, encoding="utf-8", newline="\n")
    (root / "README.md").write_text(README.format(name=name), encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8", newline="\n")
    pyproject = {
        "project": {"name": name, "version": "0.1.0", "requires-python": ">=3.11"},
        "tool": {"pytest": {"testpaths": ["tests"]}},
    }
    (root / "pyproject.toml").write_text(
        "# scaffolded by awf new\n" + _toml_dump(pyproject), encoding="utf-8", newline="\n"
    )
    (root / "tests" / "cassettes" / ".gitkeep").write_text("", encoding="utf-8")
    return root


def _toml_dump(data: dict) -> str:
    lines: list[str] = []

    def emit(prefix: str, mapping: dict) -> None:
        scalars = {key: value for key, value in mapping.items() if not isinstance(value, dict)}
        tables = {key: value for key, value in mapping.items() if isinstance(value, dict)}
        if scalars:
            lines.append(f"[{prefix}]" if prefix else "")
            for key, value in scalars.items():
                lines.append(f"{key} = {json.dumps(value)}")
        for key, value in tables.items():
            emit(f"{prefix}.{key}" if prefix else key, value)

    emit("", data)
    return "\n".join(lines) + "\n"
