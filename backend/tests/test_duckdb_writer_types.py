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
