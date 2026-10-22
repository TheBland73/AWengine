"""A tiny, safe expression language for ``when`` guards and ``converge.goal``.

Workflow YAML may contain expressions such as
``"verify.passed and reviewer.blockers == 0"``.  We never call ``eval``.  The
AST is validated against a whitelist (names, attributes, subscripts,
comparisons, boolean operators, literals) and evaluated against a plain dict
environment.  Missing names evaluate to ``None`` and are reported so the
engine can emit a ``warn`` event instead of crashing mid-run.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

MAX_EXPR_LENGTH = 500

_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
    ast.Name,
    ast.Load,
    ast.Attribute,
    ast.Subscript,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mod,
)

_KEYWORDS = {"true": True, "false": False, "null": None, "none": None}

_COMPARE_OPS = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Is: lambda a, b: a is b,
    ast.IsNot: lambda a, b: a is not b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


class ExprError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover
        return "<missing>"


MISSING = _Missing()


@dataclass(frozen=True)
class CompiledExpr:
    source: str
    tree: ast.Expression
    names: tuple[str, ...]

    def evaluate(self, env: Mapping[str, Any]) -> bool:
        value, _ = self._eval(env)
        return _truthy(value)

    def evaluate_with_missing(self, env: Mapping[str, Any]) -> tuple[bool, list[str]]:
        value, missing = self._eval(env)
        return _truthy(value), missing

    def value(self, env: Mapping[str, Any]) -> tuple[Any, list[str]]:
        value, missing = self._eval(env)
        # The public API reports a missing top-level binding as ``None``; the
        # sentinel stays internal so ``missing is None`` can remain False.
        return (None if value is MISSING else value), missing

    def _eval(self, env: Mapping[str, Any]) -> tuple[Any, list[str]]:
        missing: list[str] = []
        value = _eval_node(self.tree.body, env, missing)
        return value, missing


class _Validator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: list[str] = []

    def visit(self, node: ast.AST) -> None:  # type: ignore[override]
        if not isinstance(node, _ALLOWED_NODES):
            raise ExprError("E-EXPR-FORBIDDEN", f"{type(node).__name__} is not allowed in expressions")
        super().visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            raise ExprError("E-EXPR-FORBIDDEN", "dunder names are not allowed")
        self.names.append(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise ExprError("E-EXPR-FORBIDDEN", "private attributes are not allowed")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, (str, int, float, bool, type(None))):
            raise ExprError("E-EXPR-FORBIDDEN", f"literal {type(node.value).__name__} is not allowed")


def compile_expr(source: str) -> CompiledExpr:
    if not isinstance(source, str) or not source.strip():
        raise ExprError("E-EXPR-EMPTY", "expression is empty")
    if len(source) > MAX_EXPR_LENGTH:
        raise ExprError("E-EXPR-TOO-LONG", f"expression longer than {MAX_EXPR_LENGTH} characters")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExprError("E-EXPR-SYNTAX", f"cannot parse {source!r}: {exc.msg}") from exc
    validator = _Validator()
    validator.visit(tree)
    return CompiledExpr(source=source, tree=tree, names=tuple(dict.fromkeys(validator.names)))


def _truthy(value: Any) -> bool:
    if value is MISSING or value is None:
        return False
    return bool(value)


def _lookup(env: Mapping[str, Any], name: str) -> Any:
    if name in env:
        return env[name]
    lowered = name.lower()
    if lowered in _KEYWORDS:
        return _KEYWORDS[lowered]
    return MISSING


def _getattr(value: Any, attr: str) -> Any:
    if value is MISSING:
        return MISSING
    if isinstance(value, Mapping):
        return value.get(attr, MISSING)
    try:
        return getattr(value, attr)
    except AttributeError:
        return MISSING


def _getitem(value: Any, key: Any) -> Any:
    if value is MISSING or key is MISSING:
        return MISSING
    try:
        return value[key]
    except (KeyError, IndexError, TypeError):
        return MISSING


def _eval_node(node: ast.AST, env: Mapping[str, Any], missing: list[str]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        value = _lookup(env, node.id)
        if value is MISSING and node.id.lower() not in _KEYWORDS:
            missing.append(node.id)
        return value
    if isinstance(node, ast.Attribute):
        base = _eval_node(node.value, env, missing)
        return _getattr(base, node.attr)
    if isinstance(node, ast.Subscript):
        base = _eval_node(node.value, env, missing)
        key = _eval_node(node.slice, env, missing)
        return _getitem(base, key)
    if isinstance(node, ast.List):
        return [_eval_node(item, env, missing) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(item, env, missing) for item in node.elts)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for item in node.values:
                result = _eval_node(item, env, missing)
                if not _truthy(result):
                    return result
            return result
        result = False
        for item in node.values:
            result = _eval_node(item, env, missing)
            if _truthy(result):
                return result
        return result
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, env, missing)
        if isinstance(node.op, ast.Not):
            return not _truthy(operand)
        if isinstance(node.op, ast.USub):
            try:
                return -operand
            except TypeError:
                return MISSING
        return MISSING
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, env, missing)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, env, missing)
            if left is MISSING or right is MISSING:
                return False
            if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                if left is None or right is None or type(left) is not type(right):
                    if not (isinstance(left, (int, float)) and isinstance(right, (int, float))):
                        return False
            try:
                if not _COMPARE_OPS[type(op)](left, right):
                    return False
            except TypeError:
                return False
            left = right
        return True
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, env, missing)
        right = _eval_node(node.right, env, missing)
        if left is MISSING or right is MISSING:
            return MISSING
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mod):
                return left % right
        except (TypeError, ZeroDivisionError):
            return MISSING
        return MISSING
    return MISSING


def evaluate(source: str, env: Mapping[str, Any]) -> bool:
    return compile_expr(source).evaluate(env)


def evaluate_many(sources: Iterable[str], env: Mapping[str, Any]) -> list[bool]:
    return [compile_expr(source).evaluate(env) for source in sources]
