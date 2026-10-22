"""AWEngine - a declarative, verifiable, resumable agent workflow engine.

The public entry point is the ``awf`` command line (see :mod:`awengine.cli`).
Library users normally only need :func:`awengine.cli.main` or the runner in
:mod:`awengine.engine`.
"""

from __future__ import annotations

__version__ = "1.1.0"
SCHEMA_VERSION = 2

__all__ = ["__version__", "SCHEMA_VERSION"]
