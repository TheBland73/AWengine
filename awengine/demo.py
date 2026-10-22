"""One-command, fully offline demo suite for the engine behaviours.

``awf demo`` runs every checked-in demo workflow against its checked-in
cassettes, then compares the outcome with the expectation declared below.  It
never calls a network provider: even ``--live`` is deliberately ignored here so
the result is reproducible on a clean machine.

Expected failures are part of the suite: ``guard`` must stop on a cheat and
``falsepass`` must expose a visible-pass / hidden-fail gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .budget import Budget, load_pricing
from .checkpoint import RunStore
from .engine import RunOptions, WorkflowEngine
from .events import EventBus
from .llm.fake import CassetteStore, FakeLLM
from .loader import load_spec


@dataclass(frozen=True)
class Demo:
    name: str
    workdir: str
    terminal: str
    reason: str
    failure_class: str = ""
    inputs: dict[str, str] | None = None
    headline: str = ""


@dataclass(frozen=True)
class DemoResult:
    demo: Demo
    run_id: str
    terminal: str
    reason: str
    failure_class: str
    ok: bool
    report: Path | None
    error: str = ""


DEMOS: tuple[Demo, ...] = (
    Demo(
        "hello",
        "fixtures/calcfix",
        "success",
        "goal_met",
        inputs={"name": "world"},
        headline="three-node YAML pipeline",
    ),
    Demo(
        "bugfix",
        "fixtures/calcfix",
        "success",
        "goal_met",
        headline="coder -> reviewer -> fixer loop (three agents)",
    ),
    Demo(
        "docsync",
        "fixtures/docsync",
        "success",
        "goal_met",
        headline="inline YAML agents, no Python module",
    ),
    Demo(
        "guard",
        "fixtures/calcfix",
        "blocked",
        "cheat",
        "F-STOP-EARLY",
        headline="test-file immutability guard (expected hard stop)",
    ),
    Demo(
        "falsepass",
        "fixtures/falsepass",
        "blocked",
        "verify_failed",
        "F-VERIFY-FALSEPASS",
        headline="visible tests pass, holdout fails (expected block)",
    ),
    Demo(
        "recover",
        "fixtures/calcfix",
        "success",
        "goal_met",
        headline="failed patch routed to a recovery edge",
    ),
)


def run_demos(
    project_root: Path,
    runs_root: Path,
    *,
    only: tuple[str, ...] = (),
    printer: Callable[[str], None] = print,
) -> list[DemoResult]:
    root = project_root.resolve()
    selected = [demo for demo in DEMOS if not only or demo.name in only]
    unknown = sorted(set(only) - {demo.name for demo in DEMOS})
    if unknown:
        raise ValueError(f"unknown demo(s): {', '.join(unknown)}")
    store = RunStore(runs_root)
    cassettes = CassetteStore(root / "tests" / "cassettes")
    pricing = load_pricing(root / "pricing.toml")
    results: list[DemoResult] = []
    for demo in selected:
        spec = load_spec(root / "workflows" / f"{demo.name}.yaml")
        run_id = store.new_run_id(demo.name)
        bus = EventBus(run_id, run_dir=store.run_dir(run_id))
        engine = WorkflowEngine(
            spec,
            options=RunOptions(
                workdir=root / demo.workdir,
                runs_root=runs_root,
                fake=True,
                project_dir=root,
                cassette_dir=cassettes.root,
            ),
            bus=bus,
            provider=FakeLLM(cassettes),
            budget=Budget(pricing, None),
        )
        error = ""
        try:
            state = engine.start(dict(demo.inputs or {}), run_id=run_id)
        except Exception as exc:  # noqa: BLE001 - demo runner reports every failure
            error = f"{type(exc).__name__}: {exc}"
            results.append(DemoResult(demo, run_id, "error", error, "", False, None, error))
            printer(f"[error] {demo.name}: {error}")
            bus.close()
            continue
        finally:
            if not error:
                bus.close()
        report = store.run_dir(run_id) / "report.md"
        ok = (
            state.terminal == demo.terminal
            and state.stop_reason == demo.reason
            and (not demo.failure_class or state.failure_class == demo.failure_class)
        )
        result = DemoResult(
            demo=demo,
            run_id=run_id,
            terminal=state.terminal or "",
            reason=state.stop_reason or "",
            failure_class=state.failure_class or "",
            ok=ok,
            report=report if report.is_file() else None,
        )
        results.append(result)
        mark = "ok" if ok else "MISMATCH"
        printer(
            f"[{mark}] {demo.name}: {result.terminal}/{result.reason} "
            f"({result.failure_class or 'no failure class'}) - {demo.headline}"
        )
        printer(f"        report: {result.report}")
    printer(f"summary: {sum(result.ok for result in results)}/{len(results)} demos matched expectations")
    return results
