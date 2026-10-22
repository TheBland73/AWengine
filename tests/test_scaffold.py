"""Project scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest

from awengine.errors import UsageError
from awengine.loader import load_spec
from awengine.scaffold import create_project


def test_create_project_writes_runnable_layout(tmp_path: Path) -> None:
    root = create_project("demo", tmp_path / "demo")
    expected = [
        "workflows/hello.yaml",
        "workflows/uppercase.yaml",
        "nodes/uppercase.py",
        "tests/test_workflow.py",
        "tests/cassettes/.gitkeep",
        "README.md",
        ".gitignore",
        "pyproject.toml",
    ]
    for relative in expected:
        assert (root / relative).is_file(), relative
    assert load_spec(root / "workflows" / "hello.yaml").name == "hello"
    assert load_spec(root / "workflows" / "uppercase.yaml").name == "uppercase-demo"


def test_create_project_refuses_non_empty_destination(tmp_path: Path) -> None:
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(UsageError) as excinfo:
        create_project("occupied", destination)
    assert excinfo.value.code == "E-SCAFFOLD-EXISTS"
    # --force overwrites generated files but leaves unrelated ones alone.
    create_project("occupied", destination, force=True)
    assert (destination / "keep.txt").is_file()
    assert (destination / "workflows" / "hello.yaml").is_file()

