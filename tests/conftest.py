"""Shared fixtures for the offline test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def project_root() -> Path:
    return ROOT


@pytest.fixture()
def cassette_dir(project_root: Path) -> Path:
    return project_root / "tests" / "cassettes"


@pytest.fixture()
def workflow_dir(project_root: Path) -> Path:
    return project_root / "workflows"


@pytest.fixture()
def fixture_workdir(project_root: Path) -> Path:
    return project_root / "fixtures" / "calcfix"


@pytest.fixture()
def docsync_workdir(project_root: Path) -> Path:
    return project_root / "fixtures" / "docsync"
