"""The execution engine.

Control flow lives here and nowhere else: load a spec, walk the graph, run one
node at a time, checkpoint, evaluate convergence, stop.  This module contains no
business node names - it only knows the five node types and the fixed terminal
priority implemented in :mod:`awengine.guard`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .budget import Budget, load_pricing
from .checkpoint import RunLock, RunStore, atomic_write_text
from .contracts import (
    Artifact,
    EscalationRecord,
    NodeResult,
    RunState,
    Terminal,
    VerifyResult,
    digest_of,
    iso,
    utc_now,
)
from .errors import AwfError, ContractError, StateCorrupt, classify_stop
from .events import EventBus
from .executor import LocalExecutor
from .expr import ExprError, compile_expr
from .guard import (
    ConvergeVerdict,
    evaluate_round,
    record_progress,
    should_escalate,
    stop_decision_event,
)
from .nodes.base import NodeContext, build_env, node_invocation_input
from .nodes.builtin.nodes import BUILTIN_HANDLERS
from .nodes.registry import handler_for
from .spec import NodeSpec, WorkflowSpec


@dataclass
class RunOptions:
    workdir: Path
    runs_root: Path = Path("runs")
    max_rounds: int | None = None
    max_cost: Decimal | None = None
    max_steps: int | None = None
    fake: bool = False
    record: bool = False
    live: bool = False
    no_verify: bool = False
    project_dir: Path = Path(".")
    cassette_dir: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def validate_contract(node: NodeSpec, result: NodeResult) -> str | None:
    """Very small declarative contract: required keys, types and list sizes."""
    contract = node.contract
    if not contract:
        return None
    if isinstance(contract, list):
        contract = {"required": contract}
    if not isinstance(contract, dict):
        return "contract must be a mapping or a list of required keys"
    output = result.output or {}
    required = contract.get("required") or []
    if isinstance(required, list):
        missing = [str(key) for key in required if key not in output]
        if missing:
            return f"missing required output key(s): {', '.join(missing)}"
    types = contract.get("types") or {}
    if isinstance(types, dict):
        mapping = {"str": str, "int": int, "float": (int, float), "bool": bool, "list": list, "dict": dict}
        for key, type_name in types.items():
            if key not in output:
                continue
            expected = mapping.get(str(type_name))
            if expected is None:
                continue
            value = output[key]
            if not isinstance(value, expected) or (
                isinstance(value, bool) and str(type_name) in ("int", "float")
            ):
                return f"output {key!r} must be {type_name}, got {type(value).__name__}"
    minimum = contract.get("min_items") or {}
    if isinstance(minimum, dict):
        for key, size in minimum.items():
            value = output.get(key)
            if isinstance(value, list) and len(value) < int(size):
                return f"output {key!r} needs at least {size} item(s)"
    return None


def outcome_exit_code(terminal: str, on_exhausted: str = "report") -> int:
    if terminal in ("success", "no_op"):
        return 0
    if terminal == "exhausted" and on_exhausted == "fail":
        return 2
    return 1


class WorkflowEngine:
    def __init__(
        self,
        spec: WorkflowSpec,
        *,
        options: RunOptions,
        bus: EventBus,
        provider: Any,
        budget: Budget,
    ) -> None:
        self.spec = spec
        self.options = options
        self.bus = bus
        self.provider = provider
        self.budget = budget
        self.store = RunStore(options.runs_root)
        self.executor = LocalExecutor(Path(options.workdir), Path(options.runs_root) / bus.run_id / "workdir", run_id=bus.run_id, bus=bus)
        self._lock: RunLock | None = None
        self._escalation: dict[str, Any] = {}
        self._warned_missing: set[str] = set()
        self._goal_expr = None
        if spec.converge.goal:
            self._goal_expr = compile_expr(spec.converge.goal)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self, inputs: dict[str, str] | None = None, *, run_id: str | None = None) -> RunState:
        state = self.store.create(
            workflow=self.spec.name,
            spec_hash=self.spec.spec_hash,
            inputs=inputs or {},
            run_id=run_id or self.bus.run_id,
            workdir=str(Path(self.options.workdir).resolve()),
            spec_path=str(self.spec.source_path) if self.spec.source_path else None,
            options={
                "fake": self.options.fake,
                "record": self.options.record,
                "live": self.options.live,
                "max_cost": str(self.options.max_cost) if self.options.max_cost is not None else None,
                "max_steps": self.options.max_steps,
                "disable": list(self.options.extra.get("disable") or []),
                "project_dir": str(self.options.project_dir),
                "cassettes": str(self.options.cassette_dir) if self.options.cassette_dir else None,
            },
        )
        state.budget.cost_limit = self.options.max_cost
        state.budget.pricing_version = self.budget.pricing.version
        self.store.save(state)
        self.bus.emit(
            "run_start",
            data={
                "workflow": self.spec.name,
                "version": self.spec.version,
                "spec_hash": self.spec.spec_hash,
                "inputs": state.inputs,
                "workdir": str(Path(self.options.workdir).resolve()),
                "sandbox": str(self.executor.sandbox),
                "nodes": [node.name for node in self.spec.nodes],
                "entry": self.spec.entry,
                "loop_head": self.spec.loop_head,
                "fake": bool(self.options.fake),
                "live": bool(self.options.live),
                "record": bool(self.options.record),
                "verify": self.spec.verify.model_dump(mode="json") if self.spec.verify else None,
                "pricing_version": self.budget.pricing.version,
            },
        )
        self._acquire_lock()
        try:
            self.executor.prepare()
            if self._red_before_gate(state) is not None:
                return state
            self._begin_round(state)
            self._run_loop(state, self.spec.entry, resume=False)
        finally:
            self._release_lock()
        return state

    def resume(self, state: RunState) -> RunState:
        if state.terminal:
            raise StateCorrupt(
                f"run {state.run_id} already finished with terminal={state.terminal}",
                "start a new run instead of resuming a finished one",
            )
        if state.spec_hash != self.spec.spec_hash:
            raise StateCorrupt(
                "workflow changed since this run started; resume would mix two specs",
                "restore the original workflow or start a new run",
            )
        self.bus.emit(
            "resume",
            data={
                "cursor": state.cursor,
                "round": state.runtime.round,
                "steps": state.runtime.counters.steps,
                "cost_used": str(state.runtime.counters.cost_used),
            },
        )
        self._acquire_lock()
        try:
            if not self.executor.sandbox.exists():
                self.executor.prepare()
            start = state.cursor or self.spec.entry
            if state.runtime.counters.rounds == 0:
                self._begin_round(state)
            self._run_loop(state, start, resume=True)
        finally:
            self._release_lock()
        return state

    def _acquire_lock(self) -> None:
        self._lock = RunLock(
            self.store.run_dir(self.bus.run_id),
            self.bus.run_id,
            on_steal=self._on_steal,
        )
        self._lock.acquire()

    def _on_steal(self, previous: dict[str, Any], age: float | None) -> None:
        self.bus.emit(
            "lease_stolen",
            data={"previous": previous, "age_seconds": round(age or 0, 1)},
        )

    def _release_lock(self) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    def _run_loop(self, state: RunState, start: str, *, resume: bool) -> None:
        next_name: str | None = start
        first = True
        while True:
            if next_name is None:
                # A checkpoint may point at END after a crash.
                if not self._end_round(state, at_end=True):
                    return
                next_name = self._force_next_round(state)
                first = False
                continue
            node = self.spec.node(next_name)
            if node is None:
                self._finish(state, ConvergeVerdict(Terminal.BLOCKED, "unknown_node", {"node": next_name}, goal=False))
                return
            capacity = self._check_capacity(state)
            if capacity is not None:
                self._finish(state, capacity)
                return
            result, input_digest = self._execute_with_retry(state, node)
            self._apply(state, node, result, input_digest)
            if result.status == "fail" and self._is_cheat(result):
                state.runtime.flags["cheat"] = True
                self.bus.emit(
                    "guard_trip",
                    node=node.name,
                    round=state.runtime.round,
                    data={"rule": "test_file_immutable", "error": result.error, "detail": result.output},
                )
                self._finish(state, ConvergeVerdict(Terminal.BLOCKED, "cheat", {"node": node.name}, goal=False))
                return
            next_name = self._route(state, node, result)
            state.cursor = next_name
            self.store.save(state)
            self.bus.emit(
                "cursor",
                node=node.name,
                round=state.runtime.round,
                data={"next": next_name},
            )
            if next_name is None:
                if not self._end_round(state, at_end=True):
                    return
                forced = self._force_next_round(state)
                if forced is None:
                    return
                next_name = forced
                first = False
                continue
            if next_name in self.spec.loop_heads and self.spec.is_back_edge(node.name, next_name):
                if not self._end_round(state, at_end=False):
                    return
                if state.runtime.counters.rounds >= self.spec.converge.rounds.max:
                    verdict = ConvergeVerdict(
                        Terminal.EXHAUSTED,
                        "rounds",
                        {"rounds": state.runtime.counters.rounds, "max": self.spec.converge.rounds.max},
                        goal=False,
                    )
                    self._finish(state, verdict)
                    return
                self._begin_round(state)
                first = False
                continue
            first = False

    def _force_next_round(self, state: RunState) -> str | None:
        """Used when rounds.min/streak asks for another round after END."""
        if not self.spec.loop_heads:
            return None
        if state.runtime.counters.rounds >= self.spec.converge.rounds.max:
            return None
        self._begin_round(state)
        return self.spec.loop_head

    def _check_capacity(self, state: RunState) -> ConvergeVerdict | None:
        counters = state.runtime.counters
        converge = self.spec.converge
        max_steps = converge.steps.max
        if self.options.max_steps is not None:
            max_steps = min(max_steps, self.options.max_steps)
        if counters.steps >= max_steps:
            return ConvergeVerdict(
                Terminal.EXHAUSTED,
                "steps",
                {"steps": counters.steps, "max": max_steps},
                goal=False,
            )
        if counters.rounds > converge.rounds.max:
            return ConvergeVerdict(Terminal.EXHAUSTED, "rounds", {"rounds": counters.rounds}, goal=False)
        if counters.attempts > converge.attempts.max:
            return ConvergeVerdict(Terminal.EXHAUSTED, "attempts", {"attempts": counters.attempts}, goal=False)
        cost_limit = self.options.max_cost if self.options.max_cost is not None else converge.cost.max_cny
        if cost_limit is not None and counters.cost_used > cost_limit:
            return ConvergeVerdict(
                Terminal.EXHAUSTED,
                "cost",
                {"cost_used": str(counters.cost_used), "max": str(cost_limit)},
                goal=False,
            )
        return None

    def _begin_round(self, state: RunState) -> None:
        state.runtime.round += 1
        state.runtime.counters.rounds += 1
        state.runtime.round_open = True
        self.bus.emit(
            "round_start",
            round=state.runtime.round,
            data={"round": state.runtime.round, "max": self.spec.converge.rounds.max},
        )
        self.store.save(state)

    def _end_round(self, state: RunState, *, at_end: bool) -> bool:
        """Run verify, record progress, evaluate convergence.

        Returns True when the engine should continue, False when the run has
        been finalised.
        """
        state.runtime.round_open = False
        verify_result = self._run_verify(state)
        env = build_env(state, self.spec)
        record_progress(state, self.spec, env)
        goal: bool | None = None
        if self._goal_expr is not None:
            goal, missing = self._goal_expr.evaluate_with_missing(env)
            for name in missing:
                if name not in self._warned_missing:
                    self._warned_missing.add(name)
                    self.bus.emit(
                        "warn",
                        round=state.runtime.round,
                        data={"message": f"goal references unknown name {name!r}; treated as None"},
                    )
        verify_failed = bool(verify_result is not None and not verify_result.passed)
        blockers = sum(
            int(output.get("blockers") or 0)
            for output in state.outputs.values()
            if isinstance(output, dict) and isinstance(output.get("blockers"), (int, float))
        )
        verdict = evaluate_round(
            state,
            self.spec,
            goal=goal,
            at_end=at_end,
            verify_failed=verify_failed,
            blockers=blockers,
        )
        self.bus.emit(
            "converge_eval",
            round=state.runtime.round,
            data={
                "round": state.runtime.round,
                "goal": goal,
                "goal_expression": self.spec.converge.goal,
                "streak": state.runtime.streak,
                "required_streak": self.spec.converge.streak,
                "blockers": blockers,
                "verify_passed": None if verify_result is None else verify_result.passed,
                "progress": [point.model_dump(mode="json") for point in state.runtime.progress_trace[-4:]],
                "terminal": verdict.terminal.value if verdict else None,
                "reason": verdict.reason if verdict else None,
            },
        )
        if verdict is not None:
            self._finish(state, verdict)
            return False
        if at_end:
            failed = bool(state.history and state.history[-1].status == "fail")
            if failed:
                self._finish(
                    state,
                    ConvergeVerdict(
                        Terminal.BLOCKED,
                        "node_failed",
                        {"node": state.history[-1].node, "error": state.history[-1].error},
                        goal=goal,
                    ),
                )
                return False
            # rounds.min or streak asks for another pass; only possible with a loop.
            if state.runtime.counters.rounds >= self.spec.converge.rounds.max or not self.spec.loop_heads:
                self._finish(
                    state,
                    ConvergeVerdict(
                        Terminal.BLOCKED,
                        "streak_unmet",
                        {
                            "goal": goal,
                            "streak": state.runtime.streak,
                            "required": self.spec.converge.streak,
                            "rounds": state.runtime.counters.rounds,
                            "rounds_min": self.spec.converge.rounds.min,
                        },
                        goal=goal,
                    ),
                )
                return False
            self.store.save(state)
            return True
        self._maybe_escalate(state, goal)
        self.store.save(state)
        return True

    def _maybe_escalate(self, state: RunState, goal: bool | None) -> None:
        if not should_escalate(state, self.spec, goal=goal):
            return
        rule = self.spec.converge.escalate
        previous = str(self._escalation.get("model") or self.spec.defaults.model)
        model = rule.model or previous
        record = EscalationRecord(
            round=state.runtime.round,
            from_model=previous,
            to_model=model,
            temperature=rule.temperature,
            rubric=rule.rubric,
        )
        state.runtime.escalations = tuple(state.runtime.escalations) + (record,)
        self._escalation = {
            "model": model,
            "temperature": rule.temperature,
            "rubric": rule.rubric,
        }
        self.bus.emit(
            "escalate",
            round=state.runtime.round,
            data={
                "round": state.runtime.round,
                "from_model": previous,
                "to_model": model,
                "temperature": rule.temperature,
                "rubric": rule.rubric,
                "times": len(state.runtime.escalations),
            },
        )

    # ------------------------------------------------------------------
    # node execution
    # ------------------------------------------------------------------
    def _execute_with_retry(self, state: RunState, node: NodeSpec) -> tuple[NodeResult, str]:
        attempt = 0
        last: NodeResult | None = None
        digest = ""
        while True:
            attempt += 1
            ctx = self._context(state, node, attempt)
            digest = digest_of(node_invocation_input(ctx))
            result = self._invoke(ctx, node)
            contract_error = validate_contract(node, result) if result.ok else None
            if contract_error is not None:
                result = NodeResult(
                    status="fail",
                    output=dict(result.output),
                    error=f"F-COORD-HANDOFF: {node.name}: {contract_error}",
                    usage=result.usage,
                    artifacts=result.artifacts,
                    started_at=result.started_at,
                    ended_at=utc_now(),
                )
            last = result
            if result.ok or self._is_cheat(result):
                return result, digest
            if attempt > node.retry:
                return result, digest
            state.runtime.counters.attempts += 1
            self.bus.emit(
                "node_retry",
                node=node.name,
                round=state.runtime.round,
                data={"attempt": attempt, "max_attempts": node.retry + 1, "error": result.error},
            )
            if state.runtime.counters.attempts > self.spec.converge.attempts.max:
                return result, digest
        return last or NodeResult.failure("E-NODE: no result"), digest

    def _context(self, state: RunState, node: NodeSpec, attempt: int) -> NodeContext:
        options: dict[str, Any] = {}
        if self._escalation:
            options["escalate_model"] = self._escalation.get("model")
            options["escalate_temperature"] = self._escalation.get("temperature")
            options["escalate_rubric"] = self._escalation.get("rubric")
        return NodeContext(
            node=node,
            spec=self.spec,
            state=state,
            executor=self.executor,
            bus=self.bus,
            inputs=dict(state.inputs),
            round=state.runtime.round,
            attempt=attempt,
            llm=self.provider,
            budget=self.budget,
            run_dir=self.store.run_dir(state.run_id),
            project_dir=Path(self.options.project_dir),
            options=options,
        )

    def _invoke(self, ctx: NodeContext, node: NodeSpec) -> NodeResult:
        started = time.perf_counter()
        self.bus.emit(
            "node_start",
            node=node.name,
            round=ctx.round,
            data={"type": node.type, "impl": node.impl or node.cmd, "attempt": ctx.attempt, "round": ctx.round},
        )
        try:
            handler: Callable[[NodeContext], NodeResult]
            if node.type == "builtin":
                handler = BUILTIN_HANDLERS[node.impl]
            elif node.type == "python":
                handler = handler_for("python", node.impl, project_dir=ctx.project_dir)
            elif node.type == "process":
                from .nodes.process_node import run as process_run

                handler = process_run
            elif node.type == "llm_agent":
                from .nodes.llm_node import run as llm_run

                handler = llm_run
            else:  # pragma: no cover - spec validation prevents this
                return NodeResult.failure(f"E-NODE: unknown type {node.type!r}")
            result = handler(ctx)
            if not isinstance(result, NodeResult):
                result = NodeResult(status="ok", output={"value": result})
        except (ContractError, ExprError) as exc:
            result = NodeResult.failure(f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - node boundary
            result = NodeResult.failure(f"E-NODE: {type(exc).__name__}: {exc}")
        result.ended_at = utc_now()
        latency = int((time.perf_counter() - started) * 1000)
        if result.usage is not None and result.usage.latency_ms == 0:
            result.usage.latency_ms = latency
        self.bus.emit(
            "node_end",
            node=node.name,
            round=ctx.round,
            data={
                "status": result.status,
                "duration_ms": latency,
                "error": result.error,
                "tokens": (result.usage.prompt_tokens + result.usage.completion_tokens) if result.usage else 0,
                "cost": str(result.usage.cost) if result.usage else "0",
                "artifacts": [artifact.id for artifact in result.artifacts],
                "output_keys": sorted(result.output)[:20],
            },
        )
        return result

    def _apply(self, state: RunState, node: NodeSpec, result: NodeResult, input_digest: str) -> None:
        artifacts = tuple(
            artifact.model_copy(
                update={
                    "producer": artifact.producer or node.name,
                    "round": artifact.round or state.runtime.round,
                }
            )
            for artifact in result.artifacts
        )
        result.artifacts = artifacts
        reads = list(node.config.get("reads") or [])
        if not reads and node.type == "llm_agent":
            try:
                from .nodes.llm_node import load_agent

                agent = load_agent(node.impl, project_dir=Path(self.options.project_dir))
                reads = list(agent.get("reads") or [])
            except Exception:  # noqa: BLE001 - handoff metadata is best effort
                reads = []
        self.state_manager().record_result(
            state,
            node.name,
            result,
            round_no=state.runtime.round,
            input_digest=input_digest,
            reads=reads,
        )

    def state_manager(self):
        from .state import StateManager

        return StateManager(self.bus)

    def _route(self, state: RunState, node: NodeSpec, result: NodeResult) -> str | None:
        env = build_env(state, self.spec)
        env["failed"] = result.status == "fail"
        env["status"] = result.status
        env["error"] = result.error or ""
        for edge in self.spec.out_edges(node.name):
            if edge.when is None:
                return None if edge.dst == "END" else edge.dst
            try:
                if compile_expr(edge.when).evaluate(env):
                    return None if edge.dst == "END" else edge.dst
            except ExprError as exc:
                self.bus.emit(
                    "warn",
                    node=node.name,
                    round=state.runtime.round,
                    data={"message": f"edge condition failed: {exc.message}"},
                )
        return None

    @staticmethod
    def _is_cheat(result: NodeResult) -> bool:
        if result.output.get("cheat"):
            return True
        return (result.error or "").startswith("E-GUARD")

    # ------------------------------------------------------------------
    # verify
    # ------------------------------------------------------------------
    def _run_verify(self, state: RunState) -> VerifyResult | None:
        spec = self.spec.verify
        if spec is None or self.options.no_verify:
            return None
        red_before_failed: bool | None = None
        if spec.red_before:
            red_before_failed = not state.runtime.flags.get("red_before_passed", True)
        result = self.executor.verify(
            cmd=spec.cmd,
            level=spec.level,
            holdout=self._resolve_holdout(spec.holdout),
            red_before_failed=red_before_failed,
            timeout=spec.timeout_s,
        )
        verify = VerifyResult(**result)
        state.verify = verify
        self.bus.emit(
            "verify",
            round=state.runtime.round,
            data={
                "command": verify.command,
                "level": verify.level,
                "passed": verify.passed,
                "exit_code": verify.exit_code,
                "holdout_passed": verify.holdout_passed,
                "red_before_failed": verify.red_before_failed,
                "duration_ms": verify.duration_ms,
                "failure_class": verify.failure_class,
                "stdout_tail": verify.stdout_tail,
                "stderr_tail": verify.stderr_tail,
            },
        )
        return verify

    def _resolve_holdout(self, holdout: str | None) -> str | None:
        """Resolve a workflow-relative holdout against the workflow file.

        Holdouts normally live outside the working directory on purpose: they are
        copied into ``tests_holdout/`` only at verification time, so no agent can
        read them while the run is still in progress.
        """
        if not holdout:
            return None
        path = Path(holdout)
        if path.is_absolute():
            return str(path)
        source = self.spec.source_path
        if source is not None:
            candidate = Path(source).parent / path
            if candidate.exists():
                return str(candidate.resolve())
        candidate = Path(self.options.workdir).resolve() / path
        if candidate.exists():
            return str(candidate.resolve())
        return str(path)

    def _red_before_gate(self, state: RunState) -> ConvergeVerdict | None:
        spec = self.spec.verify
        if spec is None or self.options.no_verify or not spec.red_before:
            return None
        if state.runtime.flags.get("red_before_checked"):
            return None
        passed_before = not self.executor.red_before_check(spec.cmd, spec.timeout_s)
        state.runtime.flags["red_before_checked"] = True
        state.runtime.flags["red_before_passed"] = passed_before
        self.bus.emit(
            "verify_red_before",
            data={"command": spec.cmd, "verify_passed_on_clean_copy": passed_before, "level": spec.level},
        )
        if passed_before:
            verdict = ConvergeVerdict(
                Terminal.BLOCKED,
                "verify_weak",
                {"reason": "verify passed before any change", "command": spec.cmd},
                goal=False,
            )
            self._finish(state, verdict)
            return verdict
        return None

    # ------------------------------------------------------------------
    # finish
    # ------------------------------------------------------------------
    def _finish(self, state: RunState, verdict: ConvergeVerdict) -> None:
        detail = stop_decision_event(verdict, state=state, spec=self.spec)
        failure_class = verdict.failure_class
        if verdict.terminal == Terminal.BLOCKED and verdict.reason == "verify_failed":
            failure_class = (state.verify.failure_class if state.verify is not None else "") or ""
        self.state_manager().finalize(
            state,
            verdict.terminal,
            reason=verdict.reason,
            detail=detail,
            failure_class=failure_class,
        )
        self.store.save(state)
        self.bus.emit("stop_decision", round=state.runtime.round, data=detail)
        self.bus.emit(
            "run_end",
            data={
                "terminal": state.terminal,
                "reason": state.stop_reason,
                "failure_class": state.failure_class,
                "exit_code": outcome_exit_code(state.terminal or "", self.spec.converge.on_exhausted),
                "counters": {
                    "rounds": state.runtime.counters.rounds,
                    "steps": state.runtime.counters.steps,
                    "attempts": state.runtime.counters.attempts,
                    "cost_used": str(state.runtime.counters.cost_used),
                },
                "usage": state.usage.model_dump(mode="json"),
            },
        )
        try:
            from .trace.report import generate_report

            generate_report(self.store.run_dir(state.run_id), state, self.spec)
        except Exception as exc:  # noqa: BLE001 - reporting must not break a run
            self.bus.emit("warn", data={"message": f"report generation failed: {exc}"})


# ----------------------------------------------------------------------
def run_workflow(
    spec: WorkflowSpec,
    *,
    inputs: dict[str, str],
    options: RunOptions,
    bus: EventBus,
    provider: Any,
    budget: Budget | None = None,
) -> RunState:
    engine = WorkflowEngine(spec, options=options, bus=bus, provider=provider, budget=budget or Budget(load_pricing(), options.max_cost))
    return engine.start(inputs)


def resume_workflow(
    spec: WorkflowSpec,
    state: RunState,
    *,
    options: RunOptions,
    bus: EventBus,
    provider: Any,
    budget: Budget | None = None,
) -> RunState:
    engine = WorkflowEngine(spec, options=options, bus=bus, provider=provider, budget=budget or Budget(load_pricing(), options.max_cost))
    return engine.resume(state)
