# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 1.1.x | Yes |
| < 1.1 | No; upgrade to the latest release |

## Reporting a vulnerability

Please do not open a public issue for security-sensitive reports. Use GitHub's
[private vulnerability reporting](https://github.com/TheBland73/AWengine/security/advisories/new)
or a private message to [@TheBland73](https://github.com/TheBland73). Include:

- the affected version or commit;
- a minimal workflow or command that reproduces the issue;
- the expected and observed behavior;
- whether the issue can expose files, credentials, or the host environment.

You can expect an acknowledgement within a reasonable time. Please allow a fix
to be prepared before public disclosure.

## Security model

AWEngine executes workflows in a **directory copy**, not a container. It is a
coordination and verification tool, not a sandbox for untrusted code.

- Only run workflows and node implementations you trust.
- `process` nodes and Python nodes can access the host filesystem and environment.
- The executor filters environment variables for child processes, but this is a
  hygiene measure, not a security boundary.
- Command execution is restricted to an allowlist and does not use a shell.
- Holdout tests are hidden from agents during execution and injected only at
  verification time; they are not a substitute for isolating untrusted code.
- API keys are read from the environment and are not written to run artifacts.
  Event fields that look like credentials are redacted before persistence.
- The dashboard, shell, and CLI do not authenticate users; protect the machine
  and the run directory with normal operating-system permissions.

If you need hard isolation, run the engine inside a container or a dedicated
virtual machine and restrict the working directory to a disposable volume.

## Scope

Reports about the following are especially useful:

- escaping the directory-copy boundary or the protected-test guard;
- credentials appearing in events, reports, logs, or cassettes;
- command injection through workflow fields or tool arguments;
- denial of service caused by a workflow or a provider response.
