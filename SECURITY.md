# Security policy

## Reporting a vulnerability

Please do not open a public issue for security-sensitive reports. Send a private
report to the repository owner through GitHub's private vulnerability reporting
or a direct message on GitHub, including:

- the affected version or commit,
- a minimal workflow or command that reproduces the issue,
- the expected and observed behavior,
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
- Holdout tests are hidden from agents during execution and injected only at
  verification time; they are not a substitute for isolating untrusted code.
- API keys are read from the environment and are not written to run artifacts.
  Event fields that look like credentials are redacted before persistence.

If you need hard isolation, run the engine inside a container or a dedicated
virtual machine and restrict the working directory to a disposable volume.
