"""Phase 7: safe formula evaluator — validation + evaluation, no eval()."""

import pytest

from app.formulas import FormulaError, eval_formula, validate_formula


# ---------- validate_formula ----------
def test_validate_returns_referenced_base_names():
    assert validate_formula("revenue / dau", {"revenue", "dau"}) == {"revenue", "dau"}


def test_validate_rejects_unknown_metric():
    with pytest.raises(FormulaError, match="unknown base metric"):
        validate_formula("revenue / mau", {"revenue", "dau"})


def test_validate_rejects_function_call():
    with pytest.raises(FormulaError):
        validate_formula("abs(revenue)", {"revenue"})


def test_validate_rejects_attribute_access():
    with pytest.raises(FormulaError):
        validate_formula("revenue.__class__", {"revenue"})


def test_validate_rejects_bad_syntax():
    with pytest.raises(FormulaError, match="syntax"):
        validate_formula("revenue /", {"revenue"})


def test_validate_rejects_no_references():
    with pytest.raises(FormulaError, match="at least one"):
        validate_formula("1 + 2", set())


def test_validate_allows_numeric_literals_and_parens():
    assert validate_formula("(levels_won * 1.0) / dau", {"levels_won", "dau"}) == {
        "levels_won",
        "dau",
    }


def test_validate_rejects_string_literal():
    with pytest.raises(FormulaError, match="numeric"):
        validate_formula("revenue + 'x'", {"revenue"})


# ---------- eval_formula ----------
def test_eval_basic_division():
    assert eval_formula("revenue / dau", {"revenue": 100.0, "dau": 50}) == 2.0


def test_eval_respects_precedence_and_parens():
    assert eval_formula("a + b * 2", {"a": 1, "b": 3}) == 7
    assert eval_formula("(a + b) * 2", {"a": 1, "b": 3}) == 8


def test_eval_division_by_zero_returns_none():
    assert eval_formula("revenue / dau", {"revenue": 100.0, "dau": 0}) is None


def test_eval_none_operand_propagates_none():
    assert eval_formula("revenue / dau", {"revenue": None, "dau": 50}) is None


def test_eval_unary_minus():
    assert eval_formula("-revenue", {"revenue": 5}) == -5


def test_eval_modulo_by_zero_returns_none():
    assert eval_formula("a % b", {"a": 5, "b": 0}) is None
