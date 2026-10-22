# Support

## Where to ask

| Need | Channel |
|---|---|
| Usage questions, setup help, workflow design | [GitHub Discussions](https://github.com/TheBland73/AWengine/discussions) |
| Reproducible bug | [Bug report](https://github.com/TheBland73/AWengine/issues/new?template=bug_report.yml) |
| Feature request | [Feature request](https://github.com/TheBland73/AWengine/issues/new?template=feature_request.yml) |
| Security vulnerability | Follow [`SECURITY.md`](SECURITY.md); do not open a public issue |
| Documentation gap | Open an issue with the `documentation` label |

## Before opening an issue

1. Search existing issues and discussions.
2. Re-read the troubleshooting table in [README](README.md#十四常见问题排查).
3. Run `awf explain <run_id>` and `awf show <run_id> --events 30`.
4. Prepare a minimal reproduction: workflow YAML, command, run id, and the
   relevant excerpt from `report.md` or `events.jsonl`.

## What to include

- AWEngine version (`awf --version`), Python version, and OS;
- the command you ran and its exit code;
- whether the run used `--fake`, `--live`, or `--record`;
- the smallest workflow that reproduces the issue;
- error output with API keys, private code, and personal data removed.

## Scope

Support is provided on a best-effort basis by the maintainer and community.
There is no commercial SLA. Security reports are handled privately according to
[`SECURITY.md`](SECURITY.md).
