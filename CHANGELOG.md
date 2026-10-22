# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
semantic versioning.

## [1.0.0] - 2026-10-22

Initial public release.

### Added

- Declarative YAML workflows with conditional edges, loops, retries, and
  round-scoped convergence rules.
- Node runtimes for Python functions, JSON subprocesses, built-in actions, and
  LLM agents (module-based or fully inline).
- Sandboxed verification with holdout injection, red-before checks, patch
  protection, and explicit false-pass classification.
- Crash-safe checkpoints, atomic state rotation, run lease locking, and
  `resume` from the last cursor.
- Append-only event stream with secret redaction, Mermaid diagrams, Markdown and
  JSON reports, and a self-contained HTML replay.
- Offline demo suite, recorded LLM cassettes, project scaffold, and CLI.
- CI on Python 3.11 and 3.13 for Windows.
