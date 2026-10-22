"""``awf`` command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence, TextIO

from . import __version__
from .budget import Budget, format_cny, load_pricing
from .checkpoint import RunStore
from .demo import run_demos
from .engine import RunOptions, WorkflowEngine, outcome_exit_code
from .errors import AwfError, ExitCode, UsageError
from .events import EventBus, read_events
from .llm.client import OpenAICompatibleLLM, resolve_api_key
from .llm.fake import CassetteStore, FakeLLM, RecordingLLM, default_cassette_dir
from .loader import find_workflow, load_spec, spec_fingerprint
from .nodes.registry import discover_nodes
from .scaffold import create_project
from .spec import WorkflowSpec
from .trace.explain import explain_run
from .trace.mermaid import flow_mermaid
from .trace.replay import write_replay
from .trace.report import generate_report


# ----------------------------------------------------------------------
# small presentation helpers
# ----------------------------------------------------------------------
class Colors:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def blue(self, text: str) -> str:
        return self._wrap("34", text)


def _color_enabled(no_color: bool) -> bool:
    if no_color or os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(sys.stderr, "isatty", lambda: False)())


class Printer:
    """Human event renderer; JSON mode bypasses it entirely."""

    def __init__(self, *, quiet: bool = False, verbose: bool = False, color: bool = True) -> None:
        self.quiet = quiet
        self.verbose = verbose
        self.c = Colors(color)

    def line(self, text: str) -> None:
        print(text, file=sys.stderr, flush=True)

    def __call__(self, event: Any) -> None:
        etype = event.type
        data = event.data or {}
        node = event.node or ""
        round_no = event.round
        if etype == "run_start":
            if not self.quiet:
                self.line(
                    f"{self.c.bold('run')} {event.run}  workflow={data.get('workflow')}  "
                    f"entry={data.get('entry')}  mode={'fake' if data.get('fake') else 'live'}"
                )
            return
        if etype == "node_start":
            if not self.quiet:
                self.line(f"[r{round_no}] {self.c.blue('->')} {node} {self.c.dim('(' + str(data.get('type')) + ')')}")
            return
        if etype == "node_end":
            status = data.get("status")
            badge = self.c.green("ok") if status == "ok" else self.c.red(str(status))
            if not self.quiet:
                tokens = data.get("tokens") or 0
                duration = data.get("duration_ms", 0)
                self.line(
                    f"[r{round_no}] {self.c.blue('<-')} {node} {badge} "
                    f"{self.c.dim(f'{duration}ms {tokens} tok')}"
                )
            if data.get("error"):
                self.line(f"      {self.c.red(str(data['error']))}")
            return
        if etype == "llm_call":
            if self.verbose and not self.quiet:
                self.line(
                    f"      llm {data.get('model')} step={data.get('step')} "
                    f"+{data.get('completion_tokens', 0)} tok {self.c.dim(format_cny(Decimal(str(data.get('cost', '0')))))}"
                )
            return
        if etype == "tool_call":
            if self.verbose and not self.quiet:
                denied = self.c.red(" DENIED") if data.get("denied") else ""
                self.line(f"      tool {data.get('tool')}{denied}")
            return
        if etype == "message":
            if not self.quiet:
                self.line(
                    f"      {self.c.dim('handoff')} {data.get('src')} -> {data.get('dst')} "
                    f"[{', '.join(data.get('artifacts') or [])}]"
                )
            return
        if etype == "verify":
            passed = data.get("passed")
            badge = self.c.green("PASS") if passed else self.c.red("FAIL")
            self.line(
                f"[r{round_no}] verify {badge} L{data.get('level')} exit={data.get('exit_code')} "
                f"{self.c.dim(str(data.get('duration_ms', 0)) + 'ms')}"
            )
            return
        if etype == "verify_red_before":
            weak = data.get("verify_passed_on_clean_copy")
            self.line(
                f"      red-before: verify {'PASSED on the untouched copy (weak gate)' if weak else 'failed as expected'}"
            )
            return
        if etype == "converge_eval":
            if self.quiet:
                return
            verdict = data.get("terminal")
            tail = self.c.green(str(verdict)) if verdict == "success" else (self.c.red(str(verdict)) if verdict else "continue")
            self.line(
                f"[r{round_no}] goal={data.get('goal')} streak={data.get('streak')}/{data.get('required_streak')} "
                f"-> {tail}"
            )
            return
        if etype == "escalate":
            if not self.quiet:
                self.line(
                    f"[r{round_no}] {self.c.yellow('escalate')} {data.get('from_model')} -> {data.get('to_model')} "
                    f"temperature={data.get('temperature')}"
                )
            return
        if etype == "node_retry":
            self.line(
                f"[r{round_no}] {self.c.yellow('retry')} {node} attempt {data.get('attempt')} "
                f"{self.c.dim(str(data.get('error') or '')[:160])}"
            )
            return
        if etype == "guard_trip":
            self.line(self.c.red(f"[r{round_no}] GUARD {data.get('rule')} at {node}: {data.get('error')}"))
            return
        if etype == "stop_decision":
            terminal = data.get("terminal")
            code = data.get("failure_class") or ""
            color = self.c.green if terminal == "success" else self.c.red
            self.line(
                f"{self.c.bold('stop')} {color(str(terminal))}/{data.get('reason')}"
                + (f" ({code})" if code else "")
            )
            return
        if etype == "warn":
            self.line(self.c.yellow(f"warn: {data.get('message')}"))
            return
        if etype == "resume":
            if not self.quiet:
                self.line(f"{self.c.bold('resume')} at {data.get('cursor')} (round {data.get('round')})")
            return
        if etype in ("context_action", "cursor", "round_start", "lease_stolen") and self.verbose:
            self.line(self.c.dim(f"{etype}: {json.dumps(data, ensure_ascii=False)[:300]}"))


# ----------------------------------------------------------------------
# argument parsing
# ----------------------------------------------------------------------
def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a decimal number: {value}") from exc
    if result < 0:
        raise argparse.ArgumentTypeError("cost must be >= 0")
    return result


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an integer: {value}") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runs", default=os.environ.get("AW_RUNS", "runs"), help="runs directory (default: runs)")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    parser.add_argument("--json-events", action="store_true", help="write JSONL events to stdout")
    parser.add_argument("--quiet", action="store_true", help="only warnings and the final stop")
    parser.add_argument("-v", "--verbose", action="store_true", help="show LLM, tool and context events")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="awf", description="Declarative, verifiable workflows for collaborative AI agents")
    parser.add_argument("--version", action="version", version=f"awengine {__version__}")
    sub = parser.add_subparsers(dest="command")

    shell = sub.add_parser("shell", help="start the interactive shell")
    shell.add_argument("--plain", action="store_true", help="use plain input instead of prompt_toolkit")
    shell.set_defaults(func=cmd_shell)

    run = sub.add_parser("run", help="run a workflow")
    run.add_argument("workflow", help="workflow name or path")
    run.add_argument("-i", "--input", action="append", default=[], metavar="KEY=VALUE", help="workflow input")
    run.add_argument("--input-file", help="JSON file with input keys")
    run.add_argument("--workdir", default=".", help="directory copied into a run sandbox (default: .)")
    run.add_argument("--spec", help="explicit workflow path")
    run.add_argument("--disable", action="append", default=[], metavar="NODE", help="splice out a node (A/B)")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--fake", action="store_true", help="replay recorded LLM responses")
    mode.add_argument("--live", action="store_true", help="call the real provider")
    mode.add_argument("--record", action="store_true", help="call the provider and write cassettes")
    run.add_argument("--cassettes", help="cassette directory")
    run.add_argument("--max-rounds", type=_positive, help="override converge.rounds.max")
    run.add_argument("--max-steps", type=_positive, help="override converge.steps.max")
    run.add_argument("--max-cost", type=_decimal, help="hard cost budget in CNY")
    run.add_argument("--no-verify", action="store_true", help="skip the deterministic verify command (debug only)")
    run.add_argument("--dry-run", action="store_true", help="print the plan and exit without side effects")
    _add_common(run)
    run.set_defaults(func=cmd_run)

    resume = sub.add_parser("resume", help="resume an interrupted run from its cursor")
    resume.add_argument("run_id")
    resume.add_argument("--spec", help="explicit workflow path")
    mode = resume.add_mutually_exclusive_group()
    mode.add_argument("--fake", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--record", action="store_true")
    resume.add_argument("--cassettes")
    resume.add_argument("--max-cost", type=_decimal)
    _add_common(resume)
    resume.set_defaults(func=cmd_resume)

    replay = sub.add_parser("replay", help="(re)generate replay.html for a run")
    replay.add_argument("run_id")
    replay.add_argument("--spec", help="explicit workflow path")
    replay.add_argument("--open", action="store_true", help="open the file in the default browser")
    _add_common(replay)
    replay.set_defaults(func=cmd_replay)

    show = sub.add_parser("show", help="show run state, history and artifacts")
    show.add_argument("run_id")
    show.add_argument("--events", type=int, default=0, help="also show the last N events")
    show.add_argument("--json", action="store_true", help="dump raw state JSON")
    _add_common(show)
    show.set_defaults(func=cmd_show)

    explain = sub.add_parser("explain", help="explain why a run stopped")
    explain.add_argument("run_id")
    explain.add_argument("--spec")
    explain.add_argument("--json", action="store_true")
    _add_common(explain)
    explain.set_defaults(func=cmd_explain)

    trace = sub.add_parser("trace", help="stream a run's event log")
    trace.add_argument("run_id")
    trace.add_argument("-n", "--events", type=int, default=0, help="number of events to show (default: all)")
    trace.add_argument("--follow", action="store_true", help="poll for new events until interrupted")
    _add_common(trace)
    trace.set_defaults(func=cmd_trace)

    report = sub.add_parser("report", help="regenerate report.md and summary.json")
    report.add_argument("run_id")
    report.add_argument("--spec")
    _add_common(report)
    report.set_defaults(func=cmd_report)

    nodes = sub.add_parser("nodes", help="list discovered node implementations")
    nodes.add_argument("--project", default=".", help="project directory to scan for nodes/")
    nodes.add_argument("--json", action="store_true")
    nodes.set_defaults(func=cmd_nodes)

    demo = sub.add_parser("demo", help="run the offline demo suite (all demos)")
    demo.add_argument("--only", action="append", default=[], metavar="NAME", help="run only this demo")
    demo.add_argument("--json", action="store_true", help="print a machine-readable summary")
    _add_common(demo)
    demo.set_defaults(func=cmd_demo)

    new = sub.add_parser("new", help="scaffold a runnable project")
    new.add_argument("name")
    new.add_argument("--dir", help="destination directory (default: ./<name>)")
    new.add_argument("--template", default="hello", choices=["hello"])
    new.add_argument("--force", action="store_true")
    new.set_defaults(func=cmd_new)

    validate = sub.add_parser("validate", help="validate a workflow without running it")
    validate.add_argument("workflow")
    validate.add_argument("--spec")
    validate.add_argument("--graph", action="store_true", help="print the Mermaid flow")
    validate.set_defaults(func=cmd_validate)

    ls = sub.add_parser("ls", help="list runs")
    ls.add_argument("--json", action="store_true")
    _add_common(ls)
    ls.set_defaults(func=cmd_ls)
    return parser


# ----------------------------------------------------------------------
# shared helpers
# ----------------------------------------------------------------------
def _load_spec(path: str | Path, *, disable: Sequence[str] = (), warn: Any = None) -> WorkflowSpec:
    return load_spec(Path(path), disable=tuple(disable), warn=warn)


def _warn(message: str) -> None:
    print(f"warn: {message}", file=sys.stderr)


def _workflow_path(reference: str, explicit: str | None) -> Path:
    if explicit:
        candidate = Path(explicit)
        if not candidate.is_file():
            raise UsageError("E-SPEC-NOTFOUND", f"workflow file does not exist: {explicit}")
        return candidate
    return find_workflow(reference, extra_dirs=(Path.cwd() / "workflows",))


def _apply_overrides(spec: WorkflowSpec, args: argparse.Namespace) -> WorkflowSpec:
    converge = spec.converge
    updates: dict[str, Any] = {}
    if getattr(args, "max_rounds", None):
        rounds = converge.rounds.model_copy(update={"max": int(args.max_rounds), "min": min(converge.rounds.min, int(args.max_rounds))})
        updates["rounds"] = rounds
    if getattr(args, "max_steps", None):
        steps = converge.steps.model_copy(update={"max": int(args.max_steps), "min": min(converge.steps.min, int(args.max_steps))})
        updates["steps"] = steps
    if not updates:
        return spec
    return spec.model_copy(update={"converge": converge.model_copy(update=updates)})


def _parse_inputs(args: argparse.Namespace) -> dict[str, str]:
    inputs: dict[str, str] = {}
    if getattr(args, "input_file", None):
        path = Path(args.input_file)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UsageError("E-INPUT-FILE", f"cannot read input file: {exc}") from exc
        if not isinstance(data, dict):
            raise UsageError("E-INPUT-FILE", "input file must contain a JSON object")
        inputs.update({str(key): str(value) for key, value in data.items()})
    for item in getattr(args, "input", []) or []:
        if "=" not in item:
            raise UsageError("E-INPUT", f"input must be KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        if not key:
            raise UsageError("E-INPUT", f"empty input key in {item!r}")
        inputs[key] = value
    return inputs


def _runs_root(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "runs", "runs")).resolve()


def _budget(spec: WorkflowSpec, args: argparse.Namespace, *, limit: Decimal | None = None) -> Budget:
    pricing = load_pricing()
    cap = limit if limit is not None else getattr(args, "max_cost", None)
    if cap is None:
        cap = spec.converge.cost.max_cny
    return Budget(pricing, cap)


def _provider(args: argparse.Namespace, spec: WorkflowSpec, printer: Printer | None) -> tuple[Any, str]:
    model = spec.defaults.model
    cassette_dir = Path(getattr(args, "cassettes", None) or default_cassette_dir()).resolve()
    fake = bool(getattr(args, "fake", False))
    live = bool(getattr(args, "live", False))
    record = bool(getattr(args, "record", False))
    if not (fake or live or record):
        key, _ = resolve_api_key(model)
        if key:
            live = True
        else:
            fake = True
            if printer is not None:
                printer.line(
                    printer.c.yellow(
                        f"warn: no API key for {model}; using --fake cassettes from {cassette_dir}"
                    )
                )
    if fake:
        store = CassetteStore(cassette_dir, read_only=True)
        if store.count() == 0 and printer is not None:
            printer.line(
                printer.c.yellow(
                    f"warn: no cassettes in {cassette_dir}; LLM nodes will fail with CassetteMiss. "
                    "Record with `awf run ... --record`."
                )
            )
        return FakeLLM(store, model=model), "fake"
    if record:
        key, base = resolve_api_key(model)
        if not key:
            raise UsageError("E-LLM-NOKEY", f"no API key for {model}", "set DEEPSEEK_API_KEY or run with --fake")
        store = CassetteStore(cassette_dir, read_only=False)
        return RecordingLLM(OpenAICompatibleLLM(api_key=key, base_url=base, model=model), store), "record"
    return OpenAICompatibleLLM(model=model), "live"


def _load_state_spec(args: argparse.Namespace, store: RunStore, state: Any) -> tuple[WorkflowSpec, dict[str, Any]]:
    manifest = store.manifest(state.run_id)
    reference = manifest.get("spec_path") or state.workflow
    path: Path | None = None
    explicit = getattr(args, "spec", None)
    if explicit:
        path = Path(explicit)
    elif manifest.get("spec_path"):
        candidate = Path(str(manifest["spec_path"]))
        if candidate.is_file():
            path = candidate
    if path is None:
        path = find_workflow(str(reference), extra_dirs=(Path(manifest.get("workdir", ".")) / "workflows", Path.cwd() / "workflows"))
    return load_spec(path), manifest


def _summary(state: Any, report: Path | None) -> str:
    counters = state.runtime.counters
    lines = [
        f"run {state.run_id}: {state.terminal} ({state.stop_reason})",
        f"rounds {counters.rounds}, steps {counters.steps}, attempts {counters.attempts}, cost {format_cny(counters.cost_used)}",
    ]
    if state.failure_class:
        lines.append(f"failure class: {state.failure_class}")
    if state.stop_detail and state.stop_detail.get("reason"):
        lines.append(f"reason: {state.stop_detail['reason']}")
    if report is not None:
        lines.append(f"report: {report}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------
def _resolve_workdir(value: str, spec_path: Path) -> Path:
    """Resolve ``--workdir`` without assuming the caller's current directory.

    Shipped demos use paths such as ``fixtures/calcfix`` that live next to the
    workflow tree.  Running ``awf`` from another directory (for example from the
    user's home) should still work: if the path is not present relative to the
    current directory, try the workflow directory and its parent.
    """
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    direct = candidate.resolve()
    if direct.is_dir():
        return direct
    for base in (spec_path.parent, spec_path.parent.parent, Path(__file__).resolve().parents[1]):
        resolved = (base / candidate).resolve()
        if resolved.is_dir():
            return resolved
    return direct


def cmd_run(args: argparse.Namespace) -> int:
    path = _workflow_path(args.workflow, args.spec)
    spec = _apply_overrides(_load_spec(path, disable=args.disable, warn=_warn), args)
    inputs = _parse_inputs(args)
    workdir = _resolve_workdir(args.workdir, path)
    if args.dry_run:
        print(f"workflow: {spec.name} v{spec.version}  ({path})")
        print(f"spec hash: {spec.spec_hash}")
        print(f"entry: {spec.entry}  loop head: {spec.loop_head or '(none)'}")
        print("nodes:")
        for node in spec.nodes:
            print(f"  - {node.name:<16} {node.type:<10} {node.impl or node.cmd or ''}")
        print("edges:")
        for edge in spec.edges:
            print(f"  - {edge.src} -> {edge.dst}" + (f" [when {edge.when}]" if edge.when else ""))
        print(f"verify: {spec.verify.cmd if spec.verify else '(none)'}")
        print(f"goal: {spec.converge.goal or '(none)'}")
        print("dry-run: no run directory was created")
        return int(ExitCode.OK)

    printer = Printer(quiet=args.quiet, verbose=args.verbose, color=_color_enabled(args.no_color))
    provider, mode = _provider(args, spec, None if args.json_events else printer)
    budget = _budget(spec, args)
    store = RunStore(_runs_root(args))
    run_id = store.new_run_id(spec.name)
    run_dir = store.run_dir(run_id)
    bus = EventBus(
        run_id,
        run_dir=run_dir,
        human_sink=None if args.json_events else printer,
        json_stream=sys.stdout if args.json_events else None,
    )
    options = RunOptions(
        workdir=workdir,
        runs_root=_runs_root(args),
        max_rounds=args.max_rounds,
        max_cost=args.max_cost,
        max_steps=args.max_steps,
        fake=(mode == "fake"),
        record=(mode == "record"),
        live=(mode == "live"),
        no_verify=args.no_verify,
        project_dir=Path.cwd(),
        cassette_dir=Path(args.cassettes) if args.cassettes else None,
        extra={"mode": mode, "disable": list(args.disable), "spec_path": str(path.resolve())},
    )
    engine = WorkflowEngine(spec, options=options, bus=bus, provider=provider, budget=budget)
    try:
        state = engine.start(inputs, run_id=run_id)
    finally:
        bus.close()
    report = run_dir / "report.md"
    if args.json_events:
        print(json.dumps({"run_id": state.run_id, "terminal": state.terminal, "report": str(report)}), file=sys.stderr)
    else:
        print(_summary(state, report if report.is_file() else None))
    return outcome_exit_code(state.terminal or "", spec.converge.on_exhausted)


def cmd_resume(args: argparse.Namespace) -> int:
    store = RunStore(_runs_root(args))
    state = store.load(args.run_id)
    if state.terminal:
        print(f"run {state.run_id} already finished with terminal={state.terminal}; nothing to resume", file=sys.stderr)
        return int(ExitCode.NOT_PASSED)
    spec, manifest = _load_state_spec(args, store, state)
    if spec.spec_hash != state.spec_hash:
        raise UsageError(
            "E-STATE-SPEC-MISMATCH",
            "workflow changed since this run started",
            "restore the original workflow file or start a new run",
        )
    recorded = dict(manifest.get("options") or {})
    if not (args.fake or args.live or args.record):
        if recorded.get("fake"):
            args.fake = True
        elif recorded.get("record"):
            args.record = True
        elif recorded.get("live"):
            args.live = True
    if not args.cassettes and recorded.get("cassettes"):
        args.cassettes = recorded["cassettes"]
    printer = Printer(quiet=args.quiet, verbose=args.verbose, color=_color_enabled(args.no_color))
    provider, mode = _provider(args, spec, None if args.json_events else printer)
    budget = _budget(spec, args, limit=state.budget.cost_limit)
    bus = EventBus(
        state.run_id,
        run_dir=store.run_dir(state.run_id),
        human_sink=None if args.json_events else printer,
        json_stream=sys.stdout if args.json_events else None,
    )
    options = RunOptions(
        workdir=Path(manifest.get("workdir", ".")),
        runs_root=_runs_root(args),
        max_cost=state.budget.cost_limit,
        max_steps=recorded.get("max_steps"),
        fake=(mode == "fake"),
        record=(mode == "record"),
        live=(mode == "live"),
        project_dir=Path(recorded.get("project_dir") or Path.cwd()),
        cassette_dir=Path(recorded["cassettes"]) if recorded.get("cassettes") else None,
    )
    engine = WorkflowEngine(spec, options=options, bus=bus, provider=provider, budget=budget)
    try:
        state = engine.resume(state)
    finally:
        bus.close()
    report = store.run_dir(state.run_id) / "report.md"
    if args.json_events:
        print(json.dumps({"run_id": state.run_id, "terminal": state.terminal, "report": str(report)}), file=sys.stderr)
    else:
        print(_summary(state, report if report.is_file() else None))
    return outcome_exit_code(state.terminal or "", spec.converge.on_exhausted)


def cmd_replay(args: argparse.Namespace) -> int:
    store = RunStore(_runs_root(args))
    state = store.load(args.run_id)
    spec, _ = _load_state_spec(args, store, state)
    run_dir = store.run_dir(state.run_id)
    events = read_events(run_dir / "events.jsonl")
    path = Path(write_replay(run_dir, state, spec, events))
    print(path)
    if args.open:
        webbrowser.open(path.as_uri())
    return int(ExitCode.OK)


def cmd_show(args: argparse.Namespace) -> int:
    store = RunStore(_runs_root(args))
    state = store.load(args.run_id)
    if args.json:
        print(state.model_dump_json(indent=2))
        return int(ExitCode.OK)
    manifest = store.manifest(state.run_id)
    print(f"run:        {state.run_id}")
    print(f"workflow:   {state.workflow} ({state.spec_hash[:16]})")
    print(f"workdir:    {manifest.get('workdir')}")
    print(f"terminal:   {state.terminal or 'interrupted'}")
    print(f"reason:     {state.stop_reason or '-'}  ({state.failure_class or '-'})")
    print(f"created:    {state.created_at.isoformat()}")
    print(f"counters:   rounds={state.runtime.counters.rounds} steps={state.runtime.counters.steps} "
          f"attempts={state.runtime.counters.attempts} cost={format_cny(state.runtime.counters.cost_used)}")
    if state.verify:
        print(f"verify:     {'PASS' if state.verify.passed else 'FAIL'} (exit {state.verify.exit_code})")
    print("\nhistory:")
    for entry in state.history:
        print(f"  r{entry.round} {entry.node:<16} {entry.status:<5} {entry.error or ''}")
    if state.artifacts:
        print("\nartifacts:")
        for artifact in state.artifacts.values():
            print(f"  {artifact.id:<16} {artifact.type:<10} {artifact.producer:<12} {artifact.digest[:12]}")
    if state.messages:
        print("\nmessages:")
        for message in state.messages:
            print(f"  {message.src} -> {message.dst} [{message.intent}] {', '.join(message.artifacts)}")
    if state.stop_detail:
        print("\nstop detail:")
        print(json.dumps(state.stop_detail, ensure_ascii=False, indent=2, default=str))
    if args.events:
        print("\nevents:")
        for event in read_events(store.run_dir(state.run_id) / "events.jsonl")[-args.events:]:
            print(f"  {event.get('seq'):>4} {event.get('type'):<16} {event.get('node') or '':<16} "
                  f"{json.dumps(event.get('data') or {}, ensure_ascii=False)[:160]}")
    return int(ExitCode.OK)


def cmd_explain(args: argparse.Namespace) -> int:
    store = RunStore(_runs_root(args))
    state = store.load(args.run_id)
    spec, _ = _load_state_spec(args, store, state)
    info, text = explain_run(store.run_dir(state.run_id), state, spec)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2, default=str))
    else:
        print(text, end="")
    return int(ExitCode.OK)


def cmd_trace(args: argparse.Namespace) -> int:
    store = RunStore(_runs_root(args))
    store.load(args.run_id)
    path = store.run_dir(args.run_id) / "events.jsonl"
    seen = 0
    while True:
        events = read_events(path)
        if args.events and not args.follow:
            events = events[-args.events:]
        for event in events[seen:]:
            _render_trace_line(event)
        seen = len(events)
        if not args.follow:
            break
        time.sleep(0.5)
    return int(ExitCode.OK)


def _render_trace_line(event: dict[str, Any]) -> None:
    data = event.get("data") or {}
    etype = event.get("type")
    node = event.get("node") or ""
    payload = {key: value for key, value in data.items() if key not in ("stdout_tail", "stderr_tail", "counters")}
    print(
        f"{event.get('seq'):>5} {str(event.get('ts'))[11:23]} {str(etype):<18} {node:<16} "
        f"{json.dumps(payload, ensure_ascii=False, default=str)[:400]}"
    )


def cmd_report(args: argparse.Namespace) -> int:
    store = RunStore(_runs_root(args))
    state = store.load(args.run_id)
    spec, _ = _load_state_spec(args, store, state)
    run_dir = store.run_dir(state.run_id)
    generate_report(run_dir, state, spec)
    print(run_dir / "report.md")
    return int(ExitCode.OK)


def cmd_nodes(args: argparse.Namespace) -> int:
    infos = discover_nodes(Path(args.project))
    if args.json:
        print(json.dumps([info.__dict__ for info in infos], ensure_ascii=False, indent=2))
        return int(ExitCode.OK)
    print(f"{'name':<28} {'kind':<12} {'source':<10} description")
    for info in infos:
        print(f"{info.name:<28} {info.kind:<12} {info.source:<10} {info.description}")
    return int(ExitCode.OK)


def cmd_new(args: argparse.Namespace) -> int:
    destination = Path(args.dir) if args.dir else Path(args.name)
    root = create_project(args.name, destination, template=args.template, force=args.force)
    print(f"created {root}")
    print("next:")
    print(f"  cd {root}")
    print("  python -m pytest -q")
    print("  awf run hello --live      # needs DEEPSEEK_API_KEY")
    return int(ExitCode.OK)


def cmd_demo(args: argparse.Namespace) -> int:
    printer = (lambda _message: None) if args.json else print
    project = Path(args.project).resolve() if getattr(args, "project", None) else Path.cwd()
    try:
        results = run_demos(
            project,
            _runs_root(args),
            only=tuple(args.only),
            printer=printer,
        )
    except ValueError as exc:
        raise UsageError("E-DEMO", str(exc)) from exc
    if args.json:
        payload = [
            {
                "name": result.demo.name,
                "run_id": result.run_id,
                "terminal": result.terminal,
                "reason": result.reason,
                "failure_class": result.failure_class,
                "ok": result.ok,
                "report": str(result.report) if result.report else None,
                "headline": result.demo.headline,
            }
            for result in results
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return int(ExitCode.OK) if all(result.ok for result in results) else int(ExitCode.NOT_PASSED)


def cmd_validate(args: argparse.Namespace) -> int:
    path = _workflow_path(args.workflow, args.spec)
    spec = _load_spec(path, warn=_warn)
    print(f"workflow:   {spec.name} v{spec.version}")
    print(f"path:       {path}")
    print(f"spec hash:  {spec.spec_hash}")
    print(f"entry:      {spec.entry}")
    print(f"loop head:  {spec.loop_head or '(none)'}")
    print(f"nodes:      {len(spec.nodes)}  edges: {len(spec.edges)}")
    for node in spec.nodes:
        print(f"  {node.name:<18} {node.type:<10} {node.impl or node.cmd or ''}")
    if spec.verify:
        print(f"verify:     L{spec.verify.level} `{spec.verify.cmd}` holdout={spec.verify.holdout} red_before={spec.verify.red_before}")
    print(f"goal:       {spec.converge.goal or '(none)'}")
    print(f"fingerprint {spec_fingerprint(spec)}")
    if args.graph:
        print()
        print(flow_mermaid(spec))
    return int(ExitCode.OK)


def cmd_ls(args: argparse.Namespace) -> int:
    store = RunStore(_runs_root(args))
    runs = store.list_runs()
    if args.json:
        print(json.dumps([run.__dict__ for run in runs], ensure_ascii=False, indent=2))
        return int(ExitCode.OK)
    if not runs:
        print("no runs")
        return int(ExitCode.OK)
    print(f"{'run_id':<34} {'workflow':<20} {'terminal':<10} {'rounds':>6} {'steps':>6} {'cost':>10}")
    for run in runs:
        print(f"{run.run_id:<34} {run.workflow:<20} {run.terminal:<10} {run.rounds:>6} {run.steps:>6} {run.cost:>10}")
    return int(ExitCode.OK)


def cmd_shell(args: argparse.Namespace) -> int:
    from .shell import run_shell

    return run_shell(use_prompt_toolkit=False if getattr(args, "plain", False) else None)


# ----------------------------------------------------------------------
def _tune_console() -> None:
    """Keep legacy Windows code pages from turning a successful run into exit 3."""
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "¥".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        from . import budget

        budget.CURRENCY = "CNY "
    try:
        stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass


def main(argv: Sequence[str] | None = None) -> int:
    _tune_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        if interactive and not os.environ.get("AW_NO_SHELL"):
            from .shell import run_shell

            return run_shell()
        parser.print_help()
        return int(ExitCode.OK)
    try:
        return int(args.func(args))
    except AwfError as exc:
        print(str(exc), file=sys.stderr)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        print("\ninterrupted; the run is checkpointed - resume it with `awf resume <run>`", file=sys.stderr)
        return int(ExitCode.INTERRUPT)
    except Exception as exc:  # noqa: BLE001 - last-resort CLI boundary
        if os.environ.get("AW_DEBUG"):
            raise
        print(f"[E-INTERNAL] {type(exc).__name__}: {exc}", file=sys.stderr)
        print("  hint: set AW_DEBUG=1 for a traceback, or run `awf explain <run>`", file=sys.stderr)
        return int(ExitCode.INTERNAL)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
