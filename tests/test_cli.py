"""End-to-end CLI smoke test: every documented subcommand on a real run."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from awengine.loader import load_spec

ROOT = Path(__file__).resolve().parent.parent


def awf(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        [sys.executable, "-m", "awengine", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    return process


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    runs = tmp_path_factory.mktemp("cli-runs")
    result = awf(
        "run",
        "hello",
        "--fake",
        "--cassettes",
        str(ROOT / "tests" / "cassettes"),
        "--workdir",
        str(ROOT / "fixtures" / "calcfix"),
        "-i",
        "name=world",
        "--runs",
        str(runs),
        "--no-color",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    run_ids = [path.name for path in runs.iterdir() if path.is_dir()]
    assert len(run_ids) == 1
    return runs, run_ids[0]


def test_run_creates_report_and_replay(demo_run: tuple[Path, str]) -> None:
    runs, run_id = demo_run
    run_dir = runs / run_id
    for name in ("state.json", "manifest.json", "events.jsonl", "report.md", "summary.json", "flow.mmd", "replay.html"):
        assert (run_dir / name).is_file(), name
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["terminal"] == "success"
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "```mermaid" in report
    assert "Why it stopped" in report


def test_show_explain_trace_and_ls(demo_run: tuple[Path, str]) -> None:
    runs, run_id = demo_run
    shown = awf("show", run_id, "--json", "--runs", str(runs))
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["terminal"] == "success"

    explained = awf("explain", run_id, "--json", "--runs", str(runs))
    assert explained.returncode == 0, explained.stderr
    assert json.loads(explained.stdout)["terminal"] == "success"

    traced = awf("trace", run_id, "--runs", str(runs), "--no-color")
    assert traced.returncode == 0, traced.stderr
    assert "run_start" in traced.stdout

    listed = awf("ls", "--json", "--runs", str(runs))
    assert listed.returncode == 0, listed.stderr
    rows = json.loads(listed.stdout)
    assert rows[0]["run_id"] == run_id


def test_replay_and_report_regenerate(demo_run: tuple[Path, str]) -> None:
    runs, run_id = demo_run
    replay = awf("replay", run_id, "--runs", str(runs))
    assert replay.returncode == 0, replay.stderr
    report = awf("report", run_id, "--runs", str(runs))
    assert report.returncode == 0, report.stderr
    assert (runs / run_id / "replay.html").is_file()


def test_resume_a_terminal_run_is_rejected(demo_run: tuple[Path, str]) -> None:
    runs, run_id = demo_run
    result = awf("resume", run_id, "--fake", "--runs", str(runs))
    assert result.returncode != 0
    assert "terminal" in (result.stdout + result.stderr).lower()


def test_demo_subcommand_summary(tmp_path: Path) -> None:
    result = awf("demo", "--only", "hello", "--json", "--runs", str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["name"] == "hello"
    assert rows[0]["ok"] is True
    assert (tmp_path / rows[0]["run_id"] / "report.md").is_file()


def test_relative_workdir_resolves_next_to_workflow(tmp_path: Path) -> None:
    """Running from outside the checkout must still find demo fixtures."""
    runs = tmp_path / "runs"
    result = awf(
        "run",
        "bugfix",
        "--fake",
        "--cassettes",
        str(ROOT / "tests" / "cassettes"),
        "--workdir",
        "fixtures/calcfix",
        "--runs",
        str(runs),
        "--no-color",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "success" in result.stdout


def test_validate_nodes_and_new(tmp_path: Path) -> None:
    spec = ROOT / "workflows" / "hello.yaml"
    validated = awf("validate", str(spec), "--graph")
    assert validated.returncode == 0, validated.stderr
    assert "flowchart" in validated.stdout

    nodes = awf("nodes", "--json", "--project", str(ROOT))
    assert nodes.returncode == 0, nodes.stderr
    names = {item["name"] for item in json.loads(nodes.stdout)}
    assert {"coder", "reviewer", "echo", "apply_patch"} <= names

    created = awf("new", "demo-project", "--dir", str(tmp_path / "demo-project"))
    assert created.returncode == 0, created.stderr
    generated = load_spec(tmp_path / "demo-project" / "workflows" / "hello.yaml")
    assert generated.name == "hello"
    assert (tmp_path / "demo-project" / "tests" / "test_workflow.py").is_file()

