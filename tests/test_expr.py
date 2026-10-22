"""The expression language is deliberately smaller than Python."""

from __future__ import annotations

import pytest

from awengine.expr import ExprError, compile_expr, evaluate


def test_scalar_and_boolean_expressions() -> None:
    env = {"count": 3, "name": "ada", "ok": True}
    assert evaluate("count >= 2", env) is True
    assert evaluate("name == 'ada' and ok", env) is True
    assert evaluate("count in [1, 2, 3]", env) is True
    assert evaluate("not (count == 2 or not ok)", env) is True


def test_missing_names_are_none_not_errors() -> None:
    expr = compile_expr("missing > 1")
    ok, missing = expr.evaluate_with_missing({})
    assert ok is False
    assert missing == ["missing"]
    assert evaluate("missing is None", {}) is False
    assert compile_expr("missing").value({}) == (None, ["missing"])


def test_attribute_and_subscript_are_allowed() -> None:
    env = {"review": {"blockers": 2}, "items": [{"x": 4}]}
    assert evaluate("review.blockers > 0", env) is True
    assert evaluate("items[0].x == 4", env) is True


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os').system('echo bad')",
        "(lambda: 1)()",
        "open('x')",
        "a ** b",
        "[x for x in [1, 2]]",
    ],
)
def test_unsafe_syntax_is_rejected(source: str) -> None:
    with pytest.raises(ExprError):
        compile_expr(source)


def test_error_has_a_stable_code() -> None:
    with pytest.raises(ExprError) as info:
        compile_expr("a if b else c")
    assert info.value.code == "E-EXPR-FORBIDDEN"
