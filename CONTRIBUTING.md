# Contributing

Thanks for taking the time to improve AWEngine.

## Development setup

```powershell
git clone https://github.com/TheBland73/AWengine.git
cd AWengine
python -m pip install -e ".[dev]"
python -m pytest -q
```

Python 3.11+ and `git` on `PATH` are required. The OpenAI client is optional and
is only imported for live or recording runs.

## Before opening a pull request

1. Run the full offline suite: `python -m pytest -q`.
2. Run the demos: `awf demo`.
3. Keep control flow in YAML. Engine modules must not learn business node names;
   `tests/test_layering.py` enforces the dependency direction.
4. Add or update tests for behavior changes. Tests must not require network access
   or provider credentials.
5. Update the relevant document when you change a public contract: workflow
   fields, CLI flags, exit codes, event names, or artifact shape.
6. Keep commits focused. A commit should explain one change and why it is needed.

## Code style

- Follow the existing typing style (`from __future__ import annotations`,
  explicit return types on public functions).
- Prefer small functions with a single responsibility.
- Do not add runtime dependencies without a clear justification; the engine is
  intentionally lightweight.
- Never log API keys, authorization headers, or raw provider payloads. Use the
  event bus redaction path.

## Reporting issues

Include the workflow YAML, the command you ran, the run id, and the relevant
excerpt from `runs/<run_id>/events.jsonl` or `report.md`. Redact credentials
before attaching logs.

## Security

Please report security-sensitive findings privately as described in
[`SECURITY.md`](SECURITY.md), not in a public issue.

## License

By contributing, you agree that your contribution is licensed under the MIT
License.
