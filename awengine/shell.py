"""Interactive terminal shell for ``awf``.

The shell is deliberately a thin layer over the normal command line: every
command is translated to the same argv that ``awf`` already understands, so
there is exactly one implementation of runs, reports and validation.  It adds
a few conveniences for interactive use:

* bare demo names (``bugfix``, ``docsync`` ...) expand to curated commands;
* ``show``/``report``/``replay``/``explain`` default to the newest run;
* ``/demo`` and other slash-prefixed commands work like their bare versions;
* tab completion, history and a status toolbar when ``prompt_toolkit`` is
  installed, with a plain ``input()`` fallback otherwise.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Callable, Sequence, TextIO

from . import __version__

PROMPT = "awf> "

#: Curated one-word commands for the bundled demonstrations.
PRESETS: dict[str, list[str]] = {
    "hello": ["run", "hello", "--fake", "--workdir", "fixtures/calcfix", "-i", "name=world"],
    "bugfix": ["run", "bugfix", "--fake", "--workdir", "fixtures/calcfix"],
    "docsync": ["run", "docsync", "--fake", "--workdir", "fixtures/docsync"],
    "guard": ["run", "guard"],
    "falsepass": ["run", "falsepass", "--workdir", "fixtures/falsepass"],
    "recover": ["run", "recover", "--workdir", "fixtures/calcfix"],
}

ALIASES = {
    "list": "ls",
    "runs": "ls",
    "status": "ls",
    "h": "__help__",
    "?": "__help__",
    "q": "__quit__",
    "exit": "__quit__",
    "ver": "__version__",
}

#: Commands that accept ``last`` in place of a run id.
_RUN_COMMANDS = {"show", "report", "replay", "explain", "trace"}

HELP_TEXT = """\
Interactive awf commands
  <demo>                 run one demo offline: hello, bugfix, docsync,
                         guard, falsepass, recover
  demo [--only NAME]     run every demo and print the summary
  run <workflow> [...]   run any workflow with the normal awf flags
  show [last|<id>]       inspect a run (default: newest)
  report [last|<id>]     regenerate report.md and summary.json
  replay [last|<id>]     regenerate replay.html
  explain [last|<id>]    explain why a run stopped
  ls                     list runs
  validate <workflow>    validate a YAML workflow
  nodes                  list discovered node types
  new <name>             scaffold a new project
  last                   inspect the newest run
  help                   show this text
  quit / Ctrl-D          leave the shell

Examples
  bugfix                 three-agent review + fix demo, fully offline
  bugfix --live          same workflow against a real provider
  show last              inspect the last run
  demo --only guard      run a single demo

Every command is also available in non-interactive form:
  awf run bugfix --fake --workdir fixtures/calcfix
  awf demo
"""


def _project_root(start: Path | None = None) -> Path:
    """Find a project root, so the shell works from any subdirectory."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "workflows").is_dir():
            return candidate
    return current


def _runs_root() -> Path:
    return Path(os.environ.get("AW_RUNS", "runs"))


def _latest_run_id(runs_root: Path) -> str | None:
    if not runs_root.is_dir():
        return None
    candidates = [
        child
        for child in runs_root.iterdir()
        if child.is_dir() and (child / "state.json").is_file()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0].name


def _rewrite_preset(word: str, extra: Sequence[str]) -> list[str]:
    argv = list(PRESETS[word])
    # An explicit --live/--record must replace the preset's --fake default.
    if any(flag in {"--live", "--record"} for flag in extra) and "--fake" in argv:
        argv.remove("--fake")
    return argv + list(extra)


def resolve_line(line: str) -> list[str] | None:
    """Turn one shell line into argv, or ``None`` for an empty/comment line.

    Raises ``ValueError`` for unbalanced quotes so the shell can show a
    friendly error without terminating the session.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith("/"):
        text = text[1:].lstrip()
    try:
        parts = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValueError(f"cannot parse command: {exc}") from exc
    if not parts:
        return None

    word = parts[0].lower()
    if word in {"quit", "exit", "q"}:
        return ["__quit__"]
    if word in {"help", "h", "?"}:
        return ["__help__"]
    if word in {"clear", "cls"}:
        return ["__clear__"]
    if word in {"version", "ver", "--version"}:
        return ["__version__"]
    if word == "pwd":
        return ["__pwd__"]

    if word in PRESETS:
        return _rewrite_preset(word, parts[1:])

    # ``run bugfix`` should be as safe as typing ``bugfix``; explicit flags keep
    # the normal CLI semantics.
    if word == "run" and len(parts) == 2 and parts[1] in PRESETS:
        return list(PRESETS[parts[1]])

    if word in ALIASES:
        word = ALIASES[word]
    if word in {"show", "report", "replay", "explain", "trace"} and len(parts) == 1:
        return [word, "last"]
    if word == "last":
        return ["show", "last"]
    return [word, *parts[1:]]


def _expand_last(tokens: list[str], runs_root: Path) -> list[str] | None:
    if not tokens or tokens[0] not in _RUN_COMMANDS or "last" not in tokens[1:]:
        return tokens
    latest = _latest_run_id(runs_root)
    if latest is None:
        print("no runs yet; try `bugfix` or `demo`", file=sys.stderr)
        return None
    return [latest if token == "last" else token for token in tokens]


def _banner(out: TextIO, root: Path, runs_root: Path) -> None:
    count = 0
    if runs_root.is_dir():
        count = sum(1 for child in runs_root.iterdir() if (child / "state.json").is_file())
    print(f"awengine {__version__}  interactive shell", file=out)
    print(f"project  {root}", file=out)
    print(f"runs     {runs_root}  ({count} run{'s' if count != 1 else ''})", file=out)
    try:
        from .llm.client import resolve_api_key

        key, _ = resolve_api_key("deepseek-chat")
    except Exception:  # pragma: no cover - provider metadata is optional
        key = None
    print(f"model    {'live key detected' if key else 'offline: recorded cassettes'}", file=out)
    print("type `help` for commands, `quit` to exit", file=out)
    print(file=out)


def _clear_screen() -> None:
    print("\033[2J\033[H", end="")


def _make_session(runs_root: Path):
    """Create a prompt_toolkit session, or return ``None`` without it."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.styles import Style
    except Exception:  # pragma: no cover - optional dependency
        return None

    words = [
        "help",
        "quit",
        "demo",
        "run",
        "show",
        "report",
        "replay",
        "explain",
        "trace",
        "resume",
        "validate",
        "nodes",
        "new",
        "ls",
        "last",
        "version",
        *PRESETS,
    ]
    try:
        words += [f"/{word}" for word in words]
        workflows = Path.cwd() / "workflows"
        if workflows.is_dir():
            words += [path.stem for path in workflows.glob("*.yaml")]
        if runs_root.is_dir():
            words += [
                child.name
                for child in sorted(
                    (path for path in runs_root.iterdir() if path.is_dir()),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )[:20]
            ]
    except OSError:
        pass

    history = Path.home() / ".awf" / "history"
    try:
        history.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    style = Style.from_dict(
        {
            "prompt": "ansiblue bold",
            "bottom-toolbar": "bg:#20252b #9aa7b3",
        }
    )
    try:
        return PromptSession(
            history=FileHistory(str(history)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=WordCompleter(words, ignore_case=True, sentence=False),
            complete_while_typing=True,
            style=style,
            bottom_toolbar=" demo · bugfix · run <workflow> · show last · help · quit ",
        )
    except Exception:  # pragma: no cover - e.g. no terminal available
        return None


def run_shell(
    *,
    input_func: Callable[[str], str] | None = None,
    output: TextIO | None = None,
    use_prompt_toolkit: bool | None = None,
) -> int:
    """Run the interactive loop.

    ``input_func`` is injectable for tests and for embedding the shell; when
    omitted and stdin is a terminal, ``prompt_toolkit`` is used if available.
    """
    out = output or sys.stdout
    root = _project_root()
    if Path.cwd() != root:
        os.chdir(root)
    runs_root = _runs_root()
    session = None
    if input_func is None and use_prompt_toolkit is not False and sys.stdin.isatty():
        session = _make_session(runs_root)
    if input_func is None and session is None:
        input_func = input

    _banner(out, root, runs_root)
    while True:
        try:
            line = session.prompt(PROMPT) if session is not None else input_func(PROMPT)
        except EOFError:
            print(file=out)
            break
        except KeyboardInterrupt:
            print(file=out)
            print("  (use `quit` or Ctrl-D to leave the shell)", file=out)
            continue

        try:
            argv = resolve_line(line)
        except ValueError as exc:
            print(f"error: {exc}", file=out)
            continue
        if argv is None:
            continue
        if argv == ["__quit__"]:
            break
        if argv == ["__help__"]:
            print(HELP_TEXT, end="", file=out)
            continue
        if argv == ["__version__"]:
            print(f"awengine {__version__}", file=out)
            continue
        if argv == ["__pwd__"]:
            print(Path.cwd(), file=out)
            continue
        if argv == ["__clear__"]:
            _clear_screen()
            continue

        argv = _expand_last(argv, runs_root)
        if argv is None:
            continue
        from .cli import main

        try:
            code = main(argv)
        except SystemExit as exc:  # argparse errors stay inside the shell
            code = int(exc.code or 0)
        if code:
            print(f"[exit {code}]", file=out)

    print("bye", file=out)
    return 0


__all__ = ["HELP_TEXT", "PRESETS", "PROMPT", "resolve_line", "run_shell"]
