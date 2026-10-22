"""Tests for the interactive shell layer."""

from __future__ import annotations

import pytest

from awengine.shell import PRESETS, resolve_line, run_shell


def test_demo_presets_are_offline_and_complete() -> None:
    for name in ("hello", "bugfix", "docsync", "guard", "falsepass", "recover"):
        assert name in PRESETS
    assert "--fake" in PRESETS["bugfix"]
    assert "--workdir" in PRESETS["bugfix"]
    assert PRESETS["hello"][:4] == ["run", "hello", "--fake", "--workdir"]
    assert "-i" in PRESETS["hello"]


def test_resolve_presets_aliases_and_slash_commands() -> None:
    assert resolve_line("bugfix") == PRESETS["bugfix"]
    assert resolve_line("run bugfix") == PRESETS["bugfix"]
    assert resolve_line("/demo") == ["demo"]
    assert resolve_line("list") == ["ls"]
    assert resolve_line("?") == ["__help__"]
    assert resolve_line("quit") == ["__quit__"]
    assert resolve_line("  # a comment  ") is None


def test_run_commands_default_to_the_latest_run() -> None:
    assert resolve_line("show") == ["show", "last"]
    assert resolve_line("report") == ["report", "last"]
    assert resolve_line("last") == ["show", "last"]
    # A normal run command is not rewritten.
    assert resolve_line("show 20260101-000000-demo")[1] != "last"


def test_explicit_live_mode_replaces_preset_fake() -> None:
    argv = resolve_line("bugfix --live")
    assert "--fake" not in argv
    assert "--live" in argv


def test_unbalanced_quotes_raise_value_error() -> None:
    with pytest.raises(ValueError, match="cannot parse command"):
        resolve_line("run 'unterminated")


def test_shell_help_and_quit(capsys: pytest.CaptureFixture[str]) -> None:
    lines = iter(["help", "version", "quit"])
    code = run_shell(input_func=lambda _prompt: next(lines), use_prompt_toolkit=False)
    captured = capsys.readouterr()
    assert code == 0
    assert "Interactive awf commands" in captured.out
    assert "awengine 1.1.0" in captured.out
    assert captured.out.rstrip().endswith("bye")


def test_shell_unknown_command_does_not_terminate(capsys: pytest.CaptureFixture[str]) -> None:
    lines = iter(["definitely-not-a-command", "quit"])
    code = run_shell(input_func=lambda _prompt: next(lines), use_prompt_toolkit=False)
    captured = capsys.readouterr()
    assert code == 0
    assert "bye" in captured.out
    assert "invalid choice" in captured.err
