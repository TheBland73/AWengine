"""Checkpoint store, atomic writes and run locks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from awengine.checkpoint import RunLock, RunStore
from awengine.errors import RunLocked, StateCorrupt


def _create(store: RunStore, tmp_path: Path, run_id: str = "run-1"):
    return store.create(
        workflow="demo",
        spec_hash="abc123",
        inputs={"name": "world"},
        workdir=tmp_path / "work",
        run_id=run_id,
        spec_path=str(tmp_path / "demo.yaml"),
        options={"fake": True},
    )


def test_create_save_load_roundtrip(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    state = _create(store, tmp_path)
    assert state.run_id == "run-1"
    assert store.exists("run-1")
    manifest = store.manifest("run-1")
    assert manifest["workflow"] == "demo"
    assert manifest["spec_path"].endswith("demo.yaml")
    assert Path(manifest["workdir"]).is_absolute()

    state.outputs["plan"] = {"message": "hello"}
    store.save(state)
    loaded = store.load("run-1")
    assert loaded.outputs["plan"] == {"message": "hello"}
    assert loaded.inputs == {"name": "world"}


def test_corrupt_state_falls_back_to_previous_checkpoint(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    state = _create(store, tmp_path)
    state.terminal = "running"
    store.save(state)
    state.terminal = "success"
    store.save(state)  # previous copy now holds the running state
    (store.run_dir("run-1") / "state.json").write_text("{not json", encoding="utf-8")
    loaded = store.load("run-1")
    assert loaded.terminal == "running"


def test_invalid_run_id_is_rejected(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with pytest.raises(StateCorrupt):
        store.run_dir("../escape")
    with pytest.raises(StateCorrupt):
        store.load("missing-run")


def test_list_runs_summarises(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    state = _create(store, tmp_path, "run-a")
    state.terminal = "success"
    state.runtime.counters.rounds = 2
    state.runtime.counters.steps = 5
    store.save(state)
    rows = store.list_runs()
    assert [row.run_id for row in rows] == ["run-a"]
    assert rows[0].terminal == "success"
    assert rows[0].rounds == 2
    assert rows[0].steps == 5


def test_run_lock_is_exclusive_and_reusable(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    store = RunStore(root)
    _create(store, tmp_path)
    first = RunLock(root / "run-1", "run-1")
    first.acquire()
    try:
        with pytest.raises(RunLocked):
            RunLock(root / "run-1", "run-1").acquire()
    finally:
        first.release()

    second = RunLock(root / "run-1", "run-1")
    with second:
        lease = json.loads((root / "run-1" / ".lease").read_text(encoding="utf-8"))
        assert lease["run_id"] == "run-1"
    assert not (root / "run-1" / ".lease").exists()

