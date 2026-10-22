"""Sandboxed filesystem and command execution.

Hard rules enforced here:

* a run never writes to the user's source tree - it works on a copy under
  ``runs/<id>/workdir``;
* symlinks are rejected before copying;
* commands are tokenised and executed without a shell, and only a small
  whitelist of executables is allowed (pytest / python / node);
* a patch is checked with ``git apply --check`` before it is applied, and test
  files are immutable;
* the environment seen by a node is rebuilt from a whitelist, so API keys never
  leak into a tool subprocess.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .contracts import iso, utc_now
from .errors import ExecutorUnsafe

DEFAULT_TIMEOUT = 120
MAX_OUTPUT_CHARS = 12_000

IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "runs",
    ".aw",
}
IGNORE_SUFFIXES = {".pyc", ".pyo", ".bak", ".tmp"}
IGNORE_FILES = {"state.json.prev"}

EXECUTABLES = {"python", "python3", "pytest", "node", "py"}
PROTECTED_PATTERNS = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)tests_holdout/"),
    re.compile(r"(^|/)conftest\.py$"),
    re.compile(r"(^|/)pytest\.ini$"),
    re.compile(r"(^|/)tox\.ini$"),
    re.compile(r"(^|/)test_[^/]*\.py$"),
    re.compile(r"(^|/)[^/]*_test\.py$"),
)
ENV_ALLOW = {
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PYTHONIOENCODING",
    "PYTHONPATH",
    "PYTHONUTF8",
    "LANG",
    "LC_ALL",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
}


@dataclass
class CommandResult:
    cmd: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False

    @property
    def tail(self) -> str:
        return (self.stderr or self.stdout or "").strip()[-4000:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cmd": self.cmd,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "stdout_tail": self.stdout[-4000:],
            "stderr_tail": self.stderr[-4000:],
        }


def _split_windows(command: str) -> list[str]:
    """Split a command line with the same rules as CreateProcess.

    ``shlex`` cannot reproduce Windows quoting (``--rootdir="C:\\a b"``), so use
    the Win32 parser when it is available.
    """
    try:
        import ctypes

        argc = ctypes.c_int(0)
        argv = ctypes.POINTER(ctypes.c_wchar_p)()
        shell32 = ctypes.windll.shell32
        shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        argv = shell32.CommandLineToArgvW(command, ctypes.byref(argc))
        if not argv:
            return []
        try:
            return [argv[index] for index in range(argc.value)]
        finally:
            ctypes.windll.kernel32.LocalFree(argv)
    except (AttributeError, OSError):
        parts = shlex.split(command, posix=False)
        cleaned: list[str] = []
        for part in parts:
            if len(part) >= 2 and part[0] == part[-1] == '"':
                part = part[1:-1]
            cleaned.append(part)
        return cleaned


def split_command(command: str) -> list[str]:
    command = command.strip()
    if not command:
        raise ExecutorUnsafe("E-EXEC-EMPTY", "empty command")
    if "\x00" in command or "\n" in command:
        raise ExecutorUnsafe("E-EXEC-UNSAFE", "command contains control characters")
    if os.name == "nt":
        return [part for part in _split_windows(command) if part != ""]
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        raise ExecutorUnsafe("E-EXEC-UNSAFE", f"cannot parse command: {exc}") from exc
    return [part for part in parts if part != ""]


def check_executable(parts: list[str]) -> None:
    name = Path(parts[0]).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name not in EXECUTABLES:
        raise ExecutorUnsafe(
            "E-EXEC-DENIED",
            f"executable {parts[0]!r} is not in the allow-list",
            f"allowed: {', '.join(sorted(EXECUTABLES))}",
        )


def is_protected(path: str) -> bool:
    normalised = path.replace("\\", "/").lstrip("./")
    return any(pattern.search(normalised) for pattern in PROTECTED_PATTERNS)


def patch_touches(diff: str) -> list[str]:
    touched: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            raw = line[4:].strip()
            if raw.startswith("a/") or raw.startswith("b/"):
                raw = raw[2:]
            if raw and raw != "/dev/null" and raw not in touched:
                touched.append(raw)
        elif line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                for raw in parts[2:4]:
                    if raw.startswith(("a/", "b/")):
                        raw = raw[2:]
                    if raw not in touched:
                        touched.append(raw)
    return [item for item in touched if item]


def patch_violations(diff: str) -> list[str]:
    problems: list[str] = []
    for path in patch_touches(diff):
        normalised = path.replace("\\", "/")
        if normalised.startswith("/") or re.match(r"^[A-Za-z]:", normalised):
            problems.append(f"absolute path: {path}")
        if ".." in normalised.split("/"):
            problems.append(f"path traversal: {path}")
        if is_protected(normalised):
            problems.append(f"protected test file: {path}")
    if not diff.strip():
        problems.append("empty diff")
    return problems


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in IGNORE_DIRS or name in IGNORE_FILES:
            ignored.add(name)
        elif Path(name).suffix in IGNORE_SUFFIXES:
            ignored.add(name)
    return ignored


class LocalExecutor:
    """Owns the sandbox copy and every side effect a node may cause."""

    def __init__(self, source: Path, sandbox: Path, *, run_id: str = "", bus: Any = None) -> None:
        self.source = Path(source).resolve()
        self.sandbox = Path(sandbox).resolve()
        self.run_id = run_id
        self.bus = bus
        self.effects_path = self.sandbox.parent / "effects.jsonl"
        self._effects: dict[str, dict[str, Any]] = {}
        self._prepared = False
        self._load_effects()

    # ------------------------------------------------------------------
    @property
    def root(self) -> Path:
        return self.sandbox

    def prepare(self) -> Path:
        if self._prepared:
            return self.sandbox
        if self.sandbox.is_dir() and any(self.sandbox.iterdir()):
            self._prepared = True
            return self.sandbox
        if not self.source.is_dir():
            raise ExecutorUnsafe(
                "E-EXEC-WORKDIR",
                f"workdir does not exist or is not a directory: {self.source}",
            )
        self._reject_symlinks(self.source)
        self.sandbox.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(
                self.source,
                self.sandbox,
                ignore=_copy_ignore,
                symlinks=False,
                dirs_exist_ok=True,
            )
        except shutil.Error as exc:
            raise ExecutorUnsafe("E-EXEC-COPY", f"failed to copy workdir: {exc}") from exc
        (self.sandbox / ".aw").mkdir(parents=True, exist_ok=True)
        ignore_file = self.sandbox / ".gitignore"
        try:
            existing = ignore_file.read_text(encoding="utf-8") if ignore_file.is_file() else ""
            if ".aw/" not in existing:
                ignore_file.write_text(existing + "\n.aw/\n", encoding="utf-8")
        except OSError:
            pass
        self._init_git_baseline()
        self._prepared = True
        return self.sandbox

    def _init_git_baseline(self) -> None:
        """Turn the sandbox into a git repository so unified diffs can be applied.

        The repository is disposable and lives only inside ``runs/<id>/workdir``;
        a failure (git missing, no git identity) is not fatal because nodes may
        not need patches at all.
        """
        try:
            init = self.run("git init -q", timeout=30, node="prepare", internal=True)
            if init.exit_code != 0:
                return
            # The host may have core.autocrlf=true; patches are byte-oriented, so
            # normalise the disposable repo instead of silently rewriting blobs.
            self.run("git config core.autocrlf false", timeout=30, node="prepare", internal=True)
            self.run("git config core.safecrlf false", timeout=30, node="prepare", internal=True)
            self.run("git -c user.email=aw@local -c user.name=awengine add -A", timeout=60, node="prepare", internal=True)
            self.run(
                "git -c user.email=aw@local -c user.name=awengine commit -qm baseline",
                timeout=60,
                node="prepare",
                internal=True,
            )
        except (OSError, ExecutorUnsafe):
            return

    def _reject_symlinks(self, root: Path) -> None:
        for current, dirs, files in os.walk(root, followlinks=False):
            for name in list(dirs) + files:
                path = Path(current) / name
                if path.is_symlink():
                    raise ExecutorUnsafe(
                        "E-EXEC-SYMLINK",
                        f"workdir contains a symlink: {path}",
                        "symlinks are rejected so a run cannot escape its sandbox",
                    )

    # ------------------------------------------------------------------
    def env(self, extra: dict[str, str] | None = None, *, node: str = "") -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in os.environ.items():
            if key in ENV_ALLOW or key.startswith("AW_"):
                result[key] = value
        result.setdefault("PYTHONIOENCODING", "utf-8")
        result.setdefault("PYTHONUTF8", "1")
        result["AW_RUN_ID"] = self.run_id
        result["AW_WORKDIR"] = str(self.sandbox)
        result["AW_NODE"] = node
        if extra:
            for key in extra:
                if key in ENV_ALLOW or key.startswith("AW_"):
                    result[key] = extra[key]
        return result

    def run(
        self,
        command: str,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        node: str = "",
        stdin_text: str | None = None,
        cwd: Path | None = None,
        extra_env: dict[str, str] | None = None,
        internal: bool = False,
    ) -> CommandResult:
        parts = split_command(command)
        if not internal:
            check_executable(parts)
        workdir = Path(cwd) if cwd is not None else self.sandbox
        if not self._within(workdir):
            raise ExecutorUnsafe("E-EXEC-PATH", f"cwd escapes sandbox: {workdir}")
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                parts,
                cwd=str(workdir),
                env=self.env(extra_env, node=node),
                input=stdin_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
            )
            duration = int((time.perf_counter() - started) * 1000)
            return CommandResult(
                cmd=command,
                exit_code=completed.returncode,
                stdout=(completed.stdout or "")[-MAX_OUTPUT_CHARS:],
                stderr=(completed.stderr or "")[-MAX_OUTPUT_CHARS:],
                duration_ms=duration,
            )
        except subprocess.TimeoutExpired as exc:
            duration = int((time.perf_counter() - started) * 1000)
            stdout = exc.stdout or ""
            stderr = (exc.stderr or "") + f"\n[timeout after {timeout}s]"
            return CommandResult(
                cmd=command,
                exit_code=124,
                stdout=str(stdout)[-MAX_OUTPUT_CHARS:],
                stderr=str(stderr)[-MAX_OUTPUT_CHARS:],
                duration_ms=duration,
                timed_out=True,
            )
        except FileNotFoundError as exc:
            raise ExecutorUnsafe("E-EXEC-NOTFOUND", f"executable not found: {parts[0]}") from exc

    def _within(self, path: Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.sandbox.resolve())
            return True
        except ValueError:
            return False

    def resolve(self, relative: str) -> Path:
        candidate = (self.sandbox / relative).resolve()
        if not self._within(candidate):
            raise ExecutorUnsafe("E-EXEC-PATH", f"path escapes sandbox: {relative}")
        return candidate

    # ------------------------------------------------------------------
    def apply_patch(self, diff: str, *, node: str = "") -> tuple[bool, str]:
        problems = patch_violations(diff)
        if problems:
            return False, "; ".join(problems)
        self.prepare()
        patches = self.sandbox.parent / "patches"
        patches.mkdir(exist_ok=True)
        digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()[:16]
        patch_path = patches / f"{digest}.diff"
        patch_path.write_text(diff, encoding="utf-8", newline="\n")
        check = self.run(
            f'git apply --check --unidiff-zero --recount --whitespace=nowarn "{patch_path}"',
            timeout=60,
            node=node,
            internal=True,
        )
        if check.exit_code != 0:
            return False, (check.stderr or check.stdout).strip()[:2000]
        applied = self.run(
            f'git apply --unidiff-zero --recount --whitespace=nowarn "{patch_path}"',
            timeout=60,
            node=node,
            internal=True,
        )
        if applied.exit_code != 0:
            return False, (applied.stderr or applied.stdout).strip()[:2000]
        return True, ""

    def read_file(self, relative: str, *, max_bytes: int = 200_000) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise ExecutorUnsafe("E-EXEC-READ", f"file not found: {relative}")
        data = path.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")

    def write_file(self, relative: str, content: str) -> Path:
        path = self.resolve(relative)
        if is_protected(relative):
            raise ExecutorUnsafe("E-EXEC-PROTECTED", f"refusing to write protected file: {relative}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    def search_code(self, pattern: str, *, glob: str = "**/*.py", limit: int = 50) -> list[dict[str, Any]]:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ExecutorUnsafe("E-EXEC-REGEX", f"invalid search pattern: {exc}") from exc
        hits: list[dict[str, Any]] = []
        for path in self.sandbox.glob(glob):
            if not path.is_file() or any(part in IGNORE_DIRS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.append(
                        {
                            "path": path.relative_to(self.sandbox).as_posix(),
                            "line": number,
                            "text": line.strip()[:400],
                        }
                    )
                    if len(hits) >= limit:
                        return hits
        return hits

    # ------------------------------------------------------------------
    def _load_effects(self) -> None:
        if not self.effects_path.is_file():
            return
        for line in self.effects_path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("invocation_id"):
                self._effects[record["invocation_id"]] = record

    def effect(self, invocation_id: str) -> dict[str, Any] | None:
        return self._effects.get(invocation_id)

    def record_effect(self, invocation_id: str, payload: dict[str, Any]) -> None:
        record = {
            "invocation_id": invocation_id,
            "at": iso(utc_now()),
            **payload,
        }
        self._effects[invocation_id] = record
        self.effects_path.parent.mkdir(parents=True, exist_ok=True)
        with self.effects_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    # ------------------------------------------------------------------
    def verify(
        self,
        *,
        cmd: str,
        level: int,
        holdout: str | None,
        red_before_failed: bool | None,
        timeout: int,
        node: str = "verify",
    ) -> dict[str, Any]:
        """Run the deterministic gate.  Returns a VerifyResult-shaped dict."""
        started = time.perf_counter()
        main = self.run(cmd, timeout=timeout, node=node)
        passed = main.exit_code == 0
        holdout_passed: bool | None = None
        holdout_detail = ""
        if level >= 2 and holdout:
            holdout_path = Path(holdout)
            if not holdout_path.is_absolute():
                holdout_path = self.sandbox / holdout_path
            if not holdout_path.exists():
                return {
                    "command": cmd,
                    "level": level,
                    "passed": False,
                    "exit_code": main.exit_code,
                    "holdout_passed": False,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "stdout_tail": main.stdout[-4000:],
                    "stderr_tail": (main.stderr + f"\nholdout not found: {holdout}")[-4000:],
                    "failure_class": "F-VERIFY-WEAK",
                    "detail": {"reason": "holdout_missing"},
                }
            holdout_path = self._inject_holdout(holdout_path)
            holdout_cmd = f'python -m pytest "{holdout_path}" -q --rootdir="{self.sandbox}"'
            holdout_result = self.run(
                holdout_cmd,
                timeout=timeout,
                node=node,
                extra_env={"PYTHONPATH": str(self.sandbox)},
            )
            holdout_passed = holdout_result.exit_code == 0
            holdout_detail = holdout_result.tail
            passed = passed and holdout_passed
        red_before_flag: bool | None = red_before_failed
        duration = int((time.perf_counter() - started) * 1000)
        return {
            "command": cmd,
            "level": level,
            "passed": passed,
            "exit_code": main.exit_code,
            "holdout_passed": holdout_passed,
            "red_before_failed": red_before_flag,
            "duration_ms": duration,
            "stdout_tail": main.stdout[-4000:],
            "stderr_tail": ((main.stderr + ("\n" + holdout_detail if holdout_detail else ""))[-4000:]),
            "failure_class": "" if passed else (
                "F-VERIFY-FALSEPASS"
                if main.exit_code == 0 and holdout_passed is False
                else ""
            ),
            "detail": {
                "holdout": holdout,
                "holdout_passed": holdout_passed,
                "red_before_failed": red_before_failed,
            },
        }

    def _inject_holdout(self, source: Path) -> Path:
        """Copy a host-side holdout into the protected sandbox directory.

        The copy happens only when verification starts, so agent nodes never see
        the hidden tests while they are still allowed to call tools.
        """
        try:
            source.resolve().relative_to(self.sandbox.resolve())
            return source
        except ValueError:
            pass
        destination_root = self.sandbox / "tests_holdout"
        if destination_root.exists():
            shutil.rmtree(destination_root)
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / source.name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        return destination

    def red_before_check(self, cmd: str, timeout: int, node: str = "verify") -> bool:
        """True when the verify command fails on the untouched copy."""
        result = self.run(cmd, timeout=timeout, node=node)
        return result.exit_code != 0

    def snapshot_digest(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.sandbox.rglob("*")):
            if not path.is_file() or ".aw" in path.parts:
                continue
            relative = path.relative_to(self.sandbox).as_posix()
            digest.update(relative.encode("utf-8"))
            try:
                digest.update(path.read_bytes())
            except OSError:
                continue
        return digest.hexdigest()
