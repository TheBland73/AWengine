# Contributing to AWEngine

Thanks for taking the time to improve AWEngine. This project welcomes bug
reports, documentation fixes, workflow examples, and focused code changes.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
For usage questions, see [SUPPORT.md](SUPPORT.md).

## Development setup

```powershell
git clone https://github.com/TheBland73/AWengine.git
cd AWengine
python -m pip install -e ".[dev,shell]"
python -m pytest -q
```

Python 3.11+ and `git` on `PATH` are required. `shell` is optional; it installs
`prompt_toolkit` for the interactive `awf` interface. The OpenAI client is also
optional and is imported only for live or recording runs.

If you want to run the demos exactly as CI does:

```powershell
python -m awengine demo
```

## Before opening a pull request

1. Run the full offline suite: `python -m pytest -q`.
2. Run the demos: `python -m awengine demo`.
3. Keep control flow in YAML. Engine modules must not learn business node names;
   `tests/test_layering.py` enforces the dependency direction.
4. Add or update tests for behavior changes. Tests must not require network
   access or provider credentials.
5. Update the relevant document when you change a public contract: workflow
   fields, CLI flags, exit codes, event names, artifact shape, or configuration.
6. Keep commits focused. A commit should explain one change and why it is needed.
7. Do not commit `runs/`, cassettes containing private code, API keys, or local
   absolute paths.

## Pull request checklist

- [ ] Tests pass locally and in CI.
- [ ] New behavior has a regression test (or a documented reason it cannot).
- [ ] User-facing changes are reflected in `README.md`, `MANUAL.md`, or `docs/`.
- [ ] No secrets, credentials, customer code, or machine-specific paths added.
- [ ] Backward compatibility considered for YAML fields, events, and exit codes.
- [ ] Commit message uses a clear prefix such as `feat:`, `fix:`, `docs:`, or
      `test:`.

## Code style

- Follow the existing typing style (`from __future__ import annotations`,
  explicit return types on public functions).
- Prefer small functions with a single responsibility.
- Do not add runtime dependencies without a clear justification; the engine is
  intentionally lightweight.
- Never log API keys, authorization headers, or raw provider payloads. Use the
  event bus redaction path.
- Keep the layered imports intact: lower layers must not import `cli`, `engine`,
  or business-specific node modules.

## Reporting issues

Use the issue templates. Include the workflow YAML, the command you ran, the run
id, and the relevant excerpt from `runs/<run_id>/events.jsonl` or `report.md`.
Redact credentials before attaching logs. See [SUPPORT.md](SUPPORT.md) for the
full checklist.

## Security

Please report security-sensitive findings privately as described in
[`SECURITY.md`](SECURITY.md), not in a public issue.

## License

By contributing, you agree that your contribution is licensed under the MIT
License.
