from __future__ import annotations

from pathlib import Path


def test_notes_file_exists_with_expected_content():
    notes = Path("NOTES.md")
    assert notes.is_file(), "the writer must produce NOTES.md"
    text = notes.read_text(encoding="utf-8")
    assert "AWEngine" in text
    assert "workflow" in text.lower()
