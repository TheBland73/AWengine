"""The offline demo suite must stay green."""

from __future__ import annotations

from pathlib import Path

import pytest

from awengine.demo import DEMOS, run_demos


def test_demo_summary_matches_expectations(project_root: Path, tmp_path: Path) -> None:
    results = run_demos(project_root, tmp_path / "runs", printer=lambda _message: None)
    assert len(results) == len(DEMOS)
    assert all(result.ok for result in results), [
        (result.demo.name, result.terminal, result.reason, result.failure_class)
        for result in results
        if not result.ok
    ]
    by_name = {result.demo.name: result for result in results}
    assert by_name["bugfix"].terminal == "success"
    assert by_name["docsync"].terminal == "success"
    assert by_name["guard"].terminal == "blocked"
    assert by_name["guard"].failure_class == "F-STOP-EARLY"
    assert by_name["falsepass"].failure_class == "F-VERIFY-FALSEPASS"
    assert by_name["recover"].terminal == "success"


def test_demo_rejects_unknown_names(project_root: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown demo"):
        run_demos(project_root, tmp_path / "runs", only=("does-not-exist",), printer=lambda _m: None)
