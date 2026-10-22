"""Atomic checkpoints, run listing and lease-based locking.

One run is one directory::

    runs/<run_id>/
        manifest.json      immutable description of what ran
        state.json         checkpoint, replaced atomically after every node
        state.json.prev    previous checkpoint, used when state.json is corrupt
        events.jsonl       append-only event stream
        report.md          generated at stop time
        flow.mmd ...       generated views
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .contracts import Counters, RunState, Usage, utc_now, iso
from .errors import RunLocked, StateCorrupt

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LEASE_STALE_SECONDS = 120.0
HEARTBEAT_SECONDS = 30.0


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, keeping the previous version."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if path.exists():
        try:
            shutil.copy2(path, path.with_name(path.name + ".prev"))
        except OSError:
            pass
    os.replace(tmp, path)


def state_to_json(state: RunState) -> str:
    payload = state.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@dataclass
class RunSummary:
    run_id: str
    workflow: str
    terminal: str
    created_at: str
    spec_hash: str
    rounds: int
    steps: int
    cost: str


class RunStore:
    """Filesystem layout for runs.  ``root`` is usually ``<cwd>/runs``."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------
    def run_dir(self, run_id: str) -> Path:
        if not RUN_ID_RE.match(run_id):
            raise StateCorrupt(f"invalid run id {run_id!r}")
        return self.root / run_id

    def exists(self, run_id: str) -> bool:
        return self.run_dir(run_id).is_dir()

    def new_run_id(self, prefix: str = "") -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        stem = re.sub(r"[^a-z0-9]+", "-", prefix.lower()).strip("-")[:24]
        candidate = f"{stamp}-{stem}" if stem else stamp
        run_id = candidate
        index = 1
        while (self.root / run_id).exists():
            index += 1
            run_id = f"{candidate}-{index}"
        return run_id

    def create(
        self,
        *,
        workflow: str,
        spec_hash: str,
        inputs: dict[str, str],
        workdir: str,
        options: dict[str, Any] | None = None,
        run_id: str | None = None,
        spec_path: str | None = None,
    ) -> RunState:
        run_id = run_id or self.new_run_id(workflow)
        if not RUN_ID_RE.match(run_id):
            raise StateCorrupt(f"invalid run id {run_id!r}")
        directory = self.root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        state = RunState(
            run_id=run_id,
            workflow=workflow,
            spec_hash=spec_hash,
            inputs=dict(inputs),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        manifest = {
            "run_id": run_id,
            "engine_version": __version__,
            "workflow": workflow,
            "spec_hash": spec_hash,
            "spec_path": spec_path or "",
            "inputs": dict(inputs),
            "workdir": str(Path(workdir).resolve()),
            "created_at": iso(state.created_at),
            "python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
            "platform": os.name,
            "options": options or {},
        }
        atomic_write_text(directory / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        self.save(state)
        return state

    def save(self, state: RunState) -> None:
        state.updated_at = utc_now()
        atomic_write_text(self.run_dir(state.run_id) / "state.json", state_to_json(state))

    def load(self, run_id: str) -> RunState:
        directory = self.run_dir(run_id)
        if not directory.is_dir():
            raise StateCorrupt(f"run {run_id!r} does not exist", "use `awf ls` to list runs")
        for name in ("state.json", "state.json.prev"):
            path = directory / name
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return migrate_state(data)
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                if name == "state.json.prev":
                    raise StateCorrupt(f"both state.json and state.json.prev are corrupt: {exc}") from exc
                continue
        raise StateCorrupt(f"run {run_id!r} has no readable checkpoint")

    def manifest(self, run_id: str) -> dict[str, Any]:
        path = self.run_dir(run_id) / "manifest.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def list_runs(self) -> list[RunSummary]:
        if not self.root.is_dir():
            return []
        summaries: list[RunSummary] = []
        for directory in sorted(self.root.iterdir(), key=lambda item: item.name, reverse=True):
            if not directory.is_dir() or not RUN_ID_RE.match(directory.name):
                continue
            try:
                state = self.load(directory.name)
            except StateCorrupt:
                continue
            summaries.append(
                RunSummary(
                    run_id=state.run_id,
                    workflow=state.workflow,
                    terminal=state.terminal or "running",
                    created_at=iso(state.created_at),
                    spec_hash=state.spec_hash,
                    rounds=state.runtime.counters.rounds,
                    steps=state.runtime.counters.steps,
                    cost=str(state.runtime.counters.cost_used),
                )
            )
        return summaries


def migrate_state(data: dict[str, Any]) -> RunState:
    """Migrate a state.json payload to the current schema (1 -> 2)."""
    version = int(data.get("schema_version", 1))
    if version > 2:
        raise StateCorrupt(
            f"state schema {version} is newer than this engine (2)",
            "upgrade awengine or use a matching release",
        )
    if version < 2:
        history = data.get("history") or []
        counters = {
            "rounds": max((entry.get("round", 0) for entry in history), default=0),
            "steps": len(history),
            "attempts": sum(1 for entry in history if entry.get("status") == "fail"),
            "cost_used": "0",
        }
        total = Usage()
        for entry in history:
            usage = entry.get("usage") or {}
            try:
                total = total.plus(Usage(**usage))
            except (ValueError, TypeError):
                continue
        runtime = data.setdefault("runtime", {})
        runtime["counters"] = counters
        runtime.setdefault("visit_count", {})
        for entry in history:
            node = entry.get("node")
            if node:
                runtime["visit_count"][node] = runtime["visit_count"].get(node, 0) + 1
        data["usage"] = total.model_dump(mode="json")
        data["schema_version"] = 2
        data.setdefault("outputs", {})
        data.setdefault("cursor", None)
        data.setdefault("stop_reason", "")
        data.setdefault("failure_class", "")
        data.setdefault("stop_detail", {})
    try:
        return RunState(**data)
    except (ValueError, TypeError) as exc:
        raise StateCorrupt(f"state does not match schema: {exc}") from exc


def state_counters(state: RunState) -> Counters:
    return state.runtime.counters


# ----------------------------------------------------------------------
class RunLock:
    """A lease file with a heartbeat thread.

    A stale lease (> 120s without heartbeat) can be stolen; the steal is
    reported through ``on_steal`` so the engine can emit ``lease_stolen``.
    """

    def __init__(
        self,
        run_dir: Path,
        run_id: str,
        *,
        on_steal: Any = None,
        stale_seconds: float = LEASE_STALE_SECONDS,
    ) -> None:
        self.path = run_dir / ".lease"
        self.run_id = run_id
        self.on_steal = on_steal
        self.stale_seconds = stale_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.owned = False

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        info = {
            "run_id": self.run_id,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": iso(utc_now()),
            "heartbeat_at": iso(utc_now()),
        }
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            previous = self._read()
            age = _lease_age(self.path)
            if age is not None and age < self.stale_seconds:
                owner = f"pid {previous.get('pid', '?')} on {previous.get('host', '?')}"
                raise RunLocked(self.run_id, owner)
            if self.on_steal is not None:
                self.on_steal(previous, age)
            try:
                self.path.unlink()
            except OSError:
                pass
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(info, handle)
        self.owned = True
        self._thread = threading.Thread(target=self._heartbeat, name=f"lease-{self.run_id}", daemon=True)
        self._thread.start()

    def _heartbeat(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            if not self.owned:
                return
            payload = {
                "run_id": self.run_id,
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "heartbeat_at": iso(utc_now()),
            }
            try:
                self.path.write_text(json.dumps(payload), encoding="utf-8")
                os.utime(self.path, None)
            except OSError:
                return

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self.owned:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.owned = False

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


def _lease_age(path: Path) -> float | None:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None
