from __future__ import annotations

import pytest

from awengine.errors import SpecError
from awengine.loader import load_spec_text


def test_llm_agent_without_impl_needs_inline_system_prompt() -> None:
    text = """
name: inline
nodes:
  a:
    type: llm_agent
    config:
      task: "say hi"
"""
    with pytest.raises(SpecError) as excinfo:
        load_spec_text(text, path="inline.yaml")
    assert excinfo.value.code == "SPEC_NODE_INVALID"
    assert "inline" in str(excinfo.value).lower()
