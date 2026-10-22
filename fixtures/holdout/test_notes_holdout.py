from __future__ import annotations

from pathlib import Path


def test_notes_mentions_collaboration_and_agents():
    text = Path("NOTES.md").read_text(encoding="utf-8").lower()
    assert "collaboration" in text
    assert "agent" in text
