"""Regression: an EMPTY first backpop batch must not create the cache table.

This is exactly how chart 15's cache got poisoned in the real DB: the first day
in its backpop range had no rows, the table was created with every column typed
VARCHAR (nothing to infer from), and every later batch's numeric values were
coerced to text — making all aggregations fail with DuckDB binder errors."""

from datetime import date

import duckdb
import pytest

from app.backpop.duckdb_writer import table_name, write_batch
from app.templating import DateBatch


@pytest.fixture
def duckdb_path(tmp_path, monkeypatch):
    path = str(tmp_path / "test.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


def _types(duckdb_path, chart_id):
    conn = duckdb.connect(duckdb_path)
    try:
        rows = conn.execute(f"PRAGMA table_info('{table_name(chart_id)}')").fetchall()
    except duckdb.CatalogException:
        return {}  # table doesn't exist
    finally:
        conn.close()
    return {r[1]: r[2] for r in rows}


def test_empty_first_batch_defers_table_creation(duckdb_path):
    cols = ["event_date", "country", "dau", "revenue"]

    # Day 1 of the backfill has no data — table must NOT be created all-VARCHAR.
    n = write_batch(
        999, cols, [], DateBatch(date(2026, 6, 1), date(2026, 6, 1)), "append", "event_date"
    )
    assert n == 0
    assert _types(duckdb_path, 999) == {}  # no table yet

    # First day WITH data creates the table with real inferred types...
    n = write_batch(
        999,
        cols,
        [(date(2026, 6, 2), "US", 100, 12.5)],
        DateBatch(date(2026, 6, 2), date(2026, 6, 2)),
        "append",
        "event_date",
    )
    assert n == 1
    types = _types(duckdb_path, 999)
    assert types["event_date"] == "DATE"
    assert types["dau"] == "BIGINT"
    assert types["revenue"] == "DOUBLE"
    assert types["country"] == "VARCHAR"

    # ...so aggregations work (the thing that breaks on an all-VARCHAR table).
    conn = duckdb.connect(duckdb_path)
    total = conn.execute(f'SELECT SUM(dau) FROM "{table_name(999)}"').fetchone()[0]
    conn.close()
    assert total == 100


def test_empty_batch_on_existing_table_still_clears_replace_window(duckdb_path):
    """The force-refresh contract ('an empty re-fetch clears the day') must keep
    working: once the table exists, an empty replace-mode batch deletes its window."""
    cols = ["event_date", "dau"]
    write_batch(
        998, cols, [(date(2026, 6, 2), 100)],
        DateBatch(date(2026, 6, 2), date(2026, 6, 2)), "replace", "event_date",
    )
    n = write_batch(
        998, cols, [], DateBatch(date(2026, 6, 2), date(2026, 6, 2)), "replace", "event_date"
    )
    assert n == 0
    conn = duckdb.connect(duckdb_path)
    count = conn.execute(f'SELECT COUNT(*) FROM "{table_name(998)}"').fetchone()[0]
    conn.close()
    assert count == 0


def test_decimal_values_infer_as_double(duckdb_path):
    """redshift_connector returns NUMERIC/DECIMAL columns as decimal.Decimal — these
    must infer as a numeric DuckDB type (DOUBLE), not VARCHAR, or SUM() would fail."""
    from decimal import Decimal

    cols = ["event_date", "country", "revenue"]
    n = write_batch(
        997, cols,
        [(date(2026, 6, 2), "US", Decimal("12.3400")), (date(2026, 6, 2), "UK", Decimal("0.0001"))],
        DateBatch(date(2026, 6, 2), date(2026, 6, 2)), "append", "event_date",
    )
    assert n == 2
    types = _types(duckdb_path, 997)
    assert types["revenue"] == "DOUBLE"

    conn = duckdb.connect(duckdb_path)
    total = conn.execute(f'SELECT SUM(revenue) FROM "{table_name(997)}"').fetchone()[0]
    conn.close()
    assert abs(total - 12.3401) < 1e-6


def test_all_null_metric_column_forced_numeric(duckdb_path):
    """A declared metric that is all-NULL in the first batch (a sparse metric) must
    still be typed numeric, not VARCHAR — otherwise later real values get coerced to
    text and SUM() breaks. This is exactly chart 16's revenue_usd case."""
    cols = ["event_date", "country", "dau", "revenue_usd"]
    # day 1: dau present, revenue_usd entirely NULL (no monetization joined)
    write_batch(
        996, cols,
        [(date(2026, 6, 2), "US", 100, None), (date(2026, 6, 2), "UK", 50, None)],
        DateBatch(date(2026, 6, 2), date(2026, 6, 2)), "append", "event_date",
        numeric_columns={"dau", "revenue_usd"},
    )
    types = _types(duckdb_path, 996)
    assert types["dau"] == "BIGINT"        # inferred from real values
    assert types["revenue_usd"] == "DOUBLE"  # forced numeric despite all-NULL

    # day 2 brings real revenue — inserts as numbers, SUM works
    write_batch(
        996, cols,
        [(date(2026, 6, 3), "US", 110, 12.5)],
        DateBatch(date(2026, 6, 3), date(2026, 6, 3)), "append", "event_date",
        numeric_columns={"dau", "revenue_usd"},
    )
    conn = duckdb.connect(duckdb_path)
    total = conn.execute(f'SELECT SUM(revenue_usd) FROM "{table_name(996)}"').fetchone()[0]
    conn.close()
    assert total == 12.5


def test_poisoned_metric_detection_by_type(duckdb_path):
    """int / float / decimal metric columns are healthy; only non-numeric (VARCHAR)
    metric columns are flagged as poisoned."""
    from app.backpop.duckdb_writer import poisoned_metric_columns, data_extent

    conn = duckdb.connect(duckdb_path)
    t = table_name(500)
    conn.execute(
        f'CREATE TABLE "{t}" (event_date DATE, country VARCHAR, '
        'dau BIGINT, cnt INTEGER, revenue DOUBLE, price DECIMAL(18,4), amt REAL, bad VARCHAR)'
    )
    conn.execute(
        f"INSERT INTO \"{t}\" VALUES "
        "(DATE '2026-06-10','US',10,1,1.5,2.5000,3.0,'99'),"
        "(DATE '2026-06-12','UK',20,2,2.5,3.5000,4.0,'88')"
    )
    conn.close()

    all_metrics = {"dau", "cnt", "revenue", "price", "amt", "bad"}
    assert poisoned_metric_columns(500, all_metrics) == ["bad"]        # only the VARCHAR one
    assert poisoned_metric_columns(500, {"dau", "cnt", "revenue", "price", "amt"}) == []  # all numeric kinds healthy
    assert data_extent(500, "event_date") == (date(2026, 6, 10), date(2026, 6, 12))


def test_poisoned_metric_detection_absent_table(duckdb_path):
    from app.backpop.duckdb_writer import poisoned_metric_columns, data_extent
    assert poisoned_metric_columns(4321, {"dau"}) == []
    assert data_extent(4321, "event_date") == (None, None)
