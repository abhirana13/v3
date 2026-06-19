from datetime import date

import pytest

from app.templating import (
    DateBatch,
    UnresolvedVariableError,
    expand_date_range,
    substitute,
)


def test_expand_date_range_evenly_divisible():
    batches = expand_date_range(date(2026, 6, 10), date(2026, 6, 15), 2)
    assert batches == [
        DateBatch(date(2026, 6, 10), date(2026, 6, 11)),
        DateBatch(date(2026, 6, 12), date(2026, 6, 13)),
        DateBatch(date(2026, 6, 14), date(2026, 6, 15)),
    ]


def test_expand_date_range_partial_last_batch():
    batches = expand_date_range(date(2026, 6, 10), date(2026, 6, 14), 2)
    assert batches[-1] == DateBatch(date(2026, 6, 14), date(2026, 6, 14))
    assert len(batches) == 3


def test_expand_date_range_single_day():
    batches = expand_date_range(date(2026, 6, 10), date(2026, 6, 10), 7)
    assert batches == [DateBatch(date(2026, 6, 10), date(2026, 6, 10))]


def test_expand_date_range_inverted_returns_empty():
    assert expand_date_range(date(2026, 6, 10), date(2026, 6, 1), 5) == []


def test_expand_date_range_invalid_batch_size():
    with pytest.raises(ValueError):
        expand_date_range(date(2026, 6, 1), date(2026, 6, 5), 0)


def test_substitute_builtin_date_vars():
    batch = DateBatch(date(2026, 6, 10), date(2026, 6, 12))
    out = substitute(
        "SELECT * FROM t WHERE d BETWEEN '{START_DATE}' AND '{END_DATE}'",
        {},
        batch,
    )
    assert out == "SELECT * FROM t WHERE d BETWEEN '2026-06-10' AND '2026-06-12'"


def test_substitute_cur_date_aliases():
    batch = DateBatch(date(2026, 6, 10), date(2026, 6, 12))
    out = substitute(
        "{CUR_DATE_HIPHEN} {CUR_DATE_UNDERSCORE}",
        {},
        batch,
    )
    assert out == "2026-06-12 2026_06_12"


def test_substitute_static_scalar():
    batch = DateBatch(date(2026, 6, 10), date(2026, 6, 10))
    out = substitute("country = '{COUNTRY}'", {"COUNTRY": "US"}, batch)
    assert out == "country = 'US'"


def test_substitute_static_list_becomes_quoted_csv():
    batch = DateBatch(date(2026, 6, 10), date(2026, 6, 10))
    out = substitute(
        "country IN ({TIER1_COUNTRIES})",
        {"TIER1_COUNTRIES": ["US", "UK", "CA"]},
        batch,
    )
    assert out == "country IN ('US', 'UK', 'CA')"


def test_substitute_unresolved_var_raises():
    batch = DateBatch(date(2026, 6, 10), date(2026, 6, 10))
    with pytest.raises(UnresolvedVariableError):
        substitute("WHERE x = {MISSING}", {}, batch)


def test_substitute_ignores_lowercase_braces():
    """Lower-cased {foo} or partial tokens are not treated as variables."""
    batch = DateBatch(date(2026, 6, 10), date(2026, 6, 10))
    out = substitute("a {foo} b {Bar} c", {}, batch)
    assert out == "a {foo} b {Bar} c"
