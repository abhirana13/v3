"""Write Redshift result rows into a per-chart DuckDB table.

One table per chart: `chart_<id>_data`. Column types are inferred from the first
non-NULL value of each column on first write. Subsequent batches insert into the
existing table; if `cache_strategy=='replace'` the batch's date range is deleted
first (requires `time_column` to be set on the chart).

`present_dates()` powers the append-mode fill-missing path: the runner asks
which dates in a window are already in DuckDB so it can skip them.
"""

from datetime import date, datetime, time

from app.connections import duckdb as duckdb_conn
from app.templating import DateBatch


def table_name(chart_id: int) -> str:
    return f"chart_{chart_id}_data"


_PY_TO_DUCKDB = {
    bool: "BOOLEAN",
    int: "BIGINT",
    float: "DOUBLE",
    str: "VARCHAR",
    bytes: "BLOB",
}


def _duckdb_type(value) -> str:
    if value is None:
        return "VARCHAR"
    if isinstance(value, datetime):
        return "TIMESTAMP"
    if isinstance(value, date):
        return "DATE"
    if isinstance(value, time):
        return "TIME"
    return _PY_TO_DUCKDB.get(type(value), "VARCHAR")


def _infer_types(columns: list[str], rows: list[tuple]) -> list[str]:
    types: list[str] = []
    for i in range(len(columns)):
        value = next((row[i] for row in rows if row[i] is not None), None)
        types.append(_duckdb_type(value))
    return types


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def drop_table(chart_id: int) -> None:
    """Discard a chart's cached table — used when the query changed and the cache
    is stale (a fresh build then recreates it, picking up any column changes)."""
    conn = duckdb_conn.get_connection()
    try:
        conn.execute(f"DROP TABLE IF EXISTS {_quote(table_name(chart_id))}")
    finally:
        conn.close()


def cache_columns(chart_id: int) -> set[str]:
    """Column names in a chart's cache table (empty set if it doesn't exist yet)."""
    conn = duckdb_conn.get_connection()
    try:
        table = table_name(chart_id)
        if not conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone():
            return set()
        return {r[1] for r in conn.execute(f'PRAGMA table_info({_quote(table)})').fetchall()}
    finally:
        conn.close()


def materialize_derived(chart) -> None:
    """Compute backend-defined derived dimension columns into the cache (e.g.
    country_tier from country) when the query doesn't already supply them.
    Recomputed on each backpop so appended rows and mapping changes stay in sync."""
    from app.derived_dims import DERIVED_DIMENSIONS, case_sql

    dim_names = {d.name for d in chart.dimensions}
    conn = duckdb_conn.get_connection()
    try:
        table = table_name(chart.id)
        if not conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone():
            return
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info({_quote(table)})').fetchall()}
        for d in DERIVED_DIMENSIONS:
            # skip if the query already supplies this dim, or there's no source column
            if d.name in dim_names or d.source_column not in cols:
                continue
            if d.name not in cols:
                conn.execute(f'ALTER TABLE {_quote(table)} ADD COLUMN {_quote(d.name)} VARCHAR')
            conn.execute(f'UPDATE {_quote(table)} SET {_quote(d.name)} = {case_sql(d)}')
    finally:
        conn.close()


def present_dates(
    chart_id: int, time_column: str, from_date: date, to_date: date
) -> set[date]:
    conn = duckdb_conn.get_connection()
    try:
        table = table_name(chart_id)
        exists = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            [table],
        ).fetchone()
        if not exists:
            return set()
        rows = conn.execute(
            f"SELECT DISTINCT CAST({_quote(time_column)} AS DATE) FROM {_quote(table)} "
            f"WHERE CAST({_quote(time_column)} AS DATE) BETWEEN ? AND ?",
            [from_date, to_date],
        ).fetchall()
        return {row[0] for row in rows if row[0] is not None}
    finally:
        conn.close()


def write_batch(
    chart_id: int,
    columns: list[str],
    rows: list[tuple],
    batch: DateBatch,
    cache_strategy: str,
    time_column: str | None,
) -> int:
    conn = duckdb_conn.get_connection()
    try:
        table = table_name(chart_id)
        existing = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            [table],
        ).fetchone()

        if not existing:
            if not columns:
                return 0
            types = _infer_types(columns, rows) if rows else ["VARCHAR"] * len(columns)
            cols_def = ", ".join(f"{_quote(c)} {t}" for c, t in zip(columns, types))
            conn.execute(f"CREATE TABLE {_quote(table)} ({cols_def})")

        # Only clear the batch window when the table already existed: a table we
        # just created has nothing to delete, and skipping avoids a type error if
        # the first window came back empty (columns would default to VARCHAR).
        if existing and cache_strategy == "replace" and time_column:
            conn.execute(
                f"DELETE FROM {_quote(table)} WHERE {_quote(time_column)} BETWEEN ? AND ?",
                [batch.start_date, batch.end_date],
            )

        if rows:
            placeholders = ", ".join(["?"] * len(columns))
            cols_list = ", ".join(_quote(c) for c in columns)
            conn.executemany(
                f"INSERT INTO {_quote(table)} ({cols_list}) VALUES ({placeholders})",
                [tuple(r) for r in rows],
            )
        return len(rows)
    finally:
        conn.close()
