"""Safe formula-metric evaluation.

A formula metric derives its value from other (base) metrics in the same
chart, e.g. ``revenue / dau`` or ``levels_won * 1.0 / dau``. Formulas are
evaluated at serve time, per output row, from the already-aggregated base
metric values.

Security: formulas are parsed with the ``ast`` module and walked against a
strict node whitelist — never ``eval``. Only arithmetic over numeric
literals and base-metric names is allowed. Anything else (calls, attributes,
comprehensions, names that aren't base metrics) is rejected at config time.

Null/zero semantics: if any referenced metric is NULL (None) the formula
result is None; division or modulo by zero yields None (rendered as a gap).
"""

import ast
import operator

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class FormulaError(Exception):
    pass


def _parse(formula: str) -> ast.Expression:
    try:
        return ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"invalid formula syntax: {e}") from e


def _walk_names(node: ast.AST) -> set[str]:
    """Validate every node against the whitelist; return referenced names."""
    names: set[str] = set()

    def visit(n: ast.AST) -> None:
        if isinstance(n, ast.Expression):
            visit(n.body)
        elif isinstance(n, ast.BinOp):
            if type(n.op) not in _BIN_OPS:
                raise FormulaError(f"operator {type(n.op).__name__} not allowed")
            visit(n.left)
            visit(n.right)
        elif isinstance(n, ast.UnaryOp):
            if type(n.op) not in _UNARY_OPS:
                raise FormulaError(f"unary {type(n.op).__name__} not allowed")
            visit(n.operand)
        elif isinstance(n, ast.Constant):
            if not isinstance(n.value, (int, float)) or isinstance(n.value, bool):
                raise FormulaError("only numeric literals are allowed")
        elif isinstance(n, ast.Name):
            names.add(n.id)
        else:
            raise FormulaError(f"{type(n).__name__} is not allowed in a formula")

    visit(node)
    return names


def validate_formula(formula: str, allowed_names: set[str]) -> set[str]:
    """Parse + whitelist-check a formula. Returns referenced base-metric names.

    Raises FormulaError on bad syntax, disallowed constructs, or references to
    names that aren't base metrics of the chart.
    """
    refs = _walk_names(_parse(formula))
    unknown = refs - allowed_names
    if unknown:
        raise FormulaError(
            "formula references unknown base metric(s): "
            + ", ".join(sorted(unknown))
        )
    if not refs:
        raise FormulaError("formula must reference at least one base metric")
    return refs


def eval_formula(formula: str, values: dict) -> float | None:
    """Evaluate a validated formula against base-metric values.

    Returns None if any referenced value is None or a division/modulo by zero
    occurs. Assumes the formula passed validate_formula already.
    """
    tree = _parse(formula)

    def ev(n: ast.AST):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return values.get(n.id)
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            if v is None:
                return None
            return _UNARY_OPS[type(n.op)](v)
        if isinstance(n, ast.BinOp):
            left, right = ev(n.left), ev(n.right)
            if left is None or right is None:
                return None
            if type(n.op) in (ast.Div, ast.Mod) and right == 0:
                return None
            return _BIN_OPS[type(n.op)](left, right)
        raise FormulaError(f"{type(n).__name__} is not allowed in a formula")

    return ev(tree)
