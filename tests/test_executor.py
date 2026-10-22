"""Sandbox executor: patching, hidden holdouts and the command splitter."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from awengine.executor import LocalExecutor, patch_violations, split_command

PATCH = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -6,2 +6,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


def make_executor(tmp_path: Path, source: Path) -> LocalExecutor:
    return LocalExecutor(source, tmp_path / "sandbox")


def test_prepare_copies_the_workdir_and_initialises_git(tmp_path: Path, fixture_workdir: Path) -> None:
    executor = make_executor(tmp_path, fixture_workdir)
    executor.prepare()
    assert (executor.sandbox / "calc.py").is_file()
    assert (executor.sandbox / ".git").is_dir()
    assert executor.snapshot_digest()


def test_apply_patch_changes_the_sandbox(tmp_path: Path, fixture_workdir: Path) -> None:
    executor = make_executor(tmp_path, fixture_workdir)
    executor.prepare()
    ok, detail = executor.apply_patch(PATCH)
    assert ok, detail
    text = (executor.sandbox / "calc.py").read_text(encoding="utf-8")
    assert "return a + b" in text


def test_patch_violations_reject_tests_and_traversal() -> None:
    bad = """diff --git a/tests/test_calc.py b/tests/test_calc.py
--- a/tests/test_calc.py
+++ b/tests/test_calc.py
@@ -1 +1 @@
-x
+y
diff --git a/../escape.py b/../escape.py
--- a/../escape.py
+++ b/../escape.py
@@ -1 +1 @@
-x
+y
"""
    problems = patch_violations(bad)
    assert any("protected" in problem for problem in problems)
    assert any("traversal" in problem for problem in problems)


def test_split_command_keeps_windows_quoted_paths(tmp_path: Path) -> None:
    spacey = tmp_path / "a b" / "x.py"
    parts = split_command(f'python -m pytest "{spacey}" -q --rootdir="{tmp_path}"')
    assert parts[:3] == ["python", "-m", "pytest"]
    assert parts[3] == str(spacey)
    assert parts[-1] == f"--rootdir={tmp_path}"


def test_hidden_holdout_runs_only_at_verify_time(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    (source / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    public = source / "tests"
    public.mkdir()
    (public / "test_public.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    holdout = tmp_path / "holdout" / "test_hidden.py"
    holdout.parent.mkdir()
    holdout.write_text(
        "from calc import add\n\ndef test_negative():\n    assert add(-2, -3) == -5\n",
        encoding="utf-8",
    )
    executor = make_executor(tmp_path, source)
    executor.prepare()
    assert not (executor.sandbox / "tests_holdout").exists()
    result = executor.verify(
        cmd="python -m pytest -q",
        level=2,
        holdout=str(holdout),
        red_before_failed=None,
        timeout=120,
    )
    assert result["passed"] is True
    assert result["holdout_passed"] is True
    assert (executor.sandbox / "tests_holdout" / "test_hidden.py").is_file()
    assert result["failure_class"] == ""


def test_verify_classifies_a_visible_pass_but_hidden_failure(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    (source / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    holdout = tmp_path / "holdout" / "test_hidden.py"
    holdout.parent.mkdir()
    holdout.write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    executor = make_executor(tmp_path, source)
    executor.prepare()
    result = executor.verify(
        cmd='python -c "pass"',
        level=2,
        holdout=str(holdout),
        red_before_failed=None,
        timeout=120,
    )
    assert result["passed"] is False
    assert result["exit_code"] == 0
    assert result["holdout_passed"] is False
    assert result["failure_class"] == "F-VERIFY-FALSEPASS"


def test_env_does_not_leak_api_keys(tmp_path: Path, fixture_workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak")
    executor = make_executor(tmp_path, fixture_workdir)
    executor.prepare()
    result = executor.run('python -c "import os; print(os.getenv(\'DEEPSEEK_API_KEY\'))"')
    assert result.exit_code == 0
    assert "sk-should-not-leak" not in result.stdout
