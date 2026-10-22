"""Workflow loading: strict validation and the disable-splicing rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from awengine.errors import SpecError
from awengine.loader import load_spec_text

BASE = """
name: demo
version: 1
nodes:
  a: {type: builtin, impl: echo, config: {value: hello}}
  b: {type: builtin, impl: noop}
  c: {type: builtin, impl: noop}
edges:
  - {src: a, dst: b}
  - {src: b, dst: c}
converge:
  goal: "true"
"""


def load(text: str = BASE, **kwargs):
    return load_spec_text(text, path="demo.yaml", **kwargs)


def test_mapping_nodes_get_their_key_as_name() -> None:
    spec = load()
    assert [node.name for node in spec.nodes] == ["a", "b", "c"]
    assert spec.entry == "a"


def test_duplicate_node_is_rejected() -> None:
    text = """
name: dup
version: 1
nodes:
  - {name: a, type: builtin, impl: noop}
  - {name: a, type: builtin, impl: noop}
edges: []
"""
    with pytest.raises(SpecError) as info:
        load(text)
    assert info.value.code == "SPEC_DUP_NODE"


def test_unknown_edge_target_is_rejected() -> None:
    text = BASE.replace("{src: b, dst: c}", "{src: b, dst: missing}")
    with pytest.raises(SpecError) as info:
        load(text)
    assert info.value.code == "SPEC_UNKNOWN_EDGE"


def test_cycle_without_root_is_rejected() -> None:
    text = """
name: loop
version: 1
nodes:
  a: {type: builtin, impl: noop}
  b: {type: builtin, impl: noop}
edges:
  - {src: a, dst: b}
  - {src: b, dst: a}
"""
    with pytest.raises(SpecError) as info:
        load(text)
    assert info.value.code in {"SPEC_NO_ENTRY", "SPEC_MULTI_ENTRY"}


def test_disable_splices_a_linear_node_and_combines_conditions() -> None:
    text = """
name: cond
version: 1
nodes:
  a: {type: builtin, impl: noop}
  gate: {type: builtin, impl: noop}
  c: {type: builtin, impl: noop}
  d: {type: builtin, impl: noop}
edges:
  - {src: a, dst: gate, when: "a.ok"}
  - {src: gate, dst: c, when: "gate.ok"}
  - {src: a, dst: d, when: "not a.ok"}
"""
    spec = load(text, disable=("gate",))
    edges = {(edge.src, edge.dst): edge.when for edge in spec.edges}
    assert ("a", "c") in edges
    assert edges[("a", "c")] == "(a.ok) and (gate.ok)"
    assert spec.node("gate") is None


def test_disable_ambiguous_fanout_is_rejected() -> None:
    text = """
name: fan
version: 1
nodes:
  a: {type: builtin, impl: noop}
  b: {type: builtin, impl: noop}
  c: {type: builtin, impl: noop}
  d: {type: builtin, impl: noop}
edges:
  - {src: a, dst: b}
  - {src: b, dst: c}
  - {src: b, dst: d}
"""
    with pytest.raises(SpecError) as info:
        load(text, disable=("b",))
    assert info.value.code == "SPEC_DISABLED_AMBIGUOUS"


def test_goal_defaults_to_verify_passed_when_verify_exists() -> None:
    text = BASE.replace(
        'converge:\n  goal: "true"',
        'converge: {}\nverify:\n  level: 1\n  cmd: "python -c \\"pass\\""',
    )
    spec = load(text)
    assert spec.converge.goal == "verify.passed"


def test_missing_holdout_is_reported_next_to_the_spec(tmp_path: Path) -> None:
    text = BASE.replace(
        'converge:\n  goal: "true"',
        'converge: {}\nverify:\n  level: 2\n  cmd: "python -c \\"pass\\""\n  holdout: nope/test_x.py',
    )
    with pytest.raises(SpecError) as info:
        load_spec_text(text, path=str(tmp_path / "demo.yaml"))
    assert info.value.code == "SPEC_VERIFY_HOLDOUT_MISSING"


def test_unknown_builtin_is_rejected() -> None:
    text = BASE.replace("impl: noop", "impl: does_not_exist", 1)
    with pytest.raises(SpecError) as info:
        load(text)
    assert info.value.code == "SPEC_UNKNOWN_BUILTIN"
