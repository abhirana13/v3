"""One DuckDB file per chart, and the migration off the old shared file.

The cache used to be a single `aggregates.duckdb` with a `chart_<id>_data` table per chart.
The data was already isolated — nothing ever joined two charts' tables — but **DuckDB's write
lock is on the FILE**, so a backpop of one chart locked out reads of every other chart. Users
opening an unrelated chart mid-backpop got a 503.

These tests deliberately do NOT use conftest's collapsed layout (which maps every chart onto
one file so the ~33 seeding sites elsewhere keep working). Here the real per-chart layout is
the thing under test, so `chart_db_path` is restored to the genuine implementation.
"""

import os

import duckdb
import pytest

from app.backpop.duckdb_writer import table_name
from app.connections import duckdb as duckdb_conn


@pytest.fixture
def real_layout(tmp_path, monkeypatch):
    """Undo conftest's collapse: genuine per-chart files, rooted in a temp dir."""
    root = str(tmp_path / "aggregates.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", root)
    monkeypatch.setattr(
        "app.connections.duckdb.chart_db_path",
        lambda chart_id: os.path.join(tmp_path, "charts", f"chart_{chart_id}.duckdb"),
    )
    return root


def _write(chart_id, rows):
    conn = duckdb_conn.get_connection(chart_id)
    t = table_name(chart_id)
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{t}" (n INTEGER)')
    conn.executemany(f'INSERT INTO "{t}" VALUES (?)', [(r,) for r in rows])
    conn.close()


def _count(chart_id):
    conn = duckdb_conn.get_connection(chart_id, read_only=True)
    try:
        return conn.execute(f'SELECT count(*) FROM "{table_name(chart_id)}"').fetchone()[0]
    finally:
        conn.close()


def test_each_chart_gets_its_own_file(real_layout):
    _write(4, [1, 2, 3])
    _write(18, [1, 2])
    assert os.path.exists(duckdb_conn.chart_db_path(4))
    assert os.path.exists(duckdb_conn.chart_db_path(18))
    assert duckdb_conn.chart_db_path(4) != duckdb_conn.chart_db_path(18)
    assert _count(4) == 3 and _count(18) == 2


def test_a_write_lock_on_one_chart_does_not_block_another(real_layout):
    """THE POINT OF THE SPLIT. Hold chart 18's cache open read-write — exactly what a backpop
    does — and chart 4 must still be readable. On the shared file that was impossible: the lock
    is per FILE, so writing any chart locked out reads of all of them.

    Only the cross-chart isolation is asserted. Same-chart contention is NOT checked here
    because it does not reproduce faithfully in one process: DuckDB caches database instances
    per process, so a second open of the same path returns
    `Can't open a connection to same database file with a different configuration` rather than
    the `Could not set lock on file` a genuinely separate process gets. Verified cross-process
    against the real cache instead — measured lock stretches of 4.2-6.2s during a live backpop.
    """
    _write(4, [1, 2, 3])
    _write(18, [1, 2])

    holder = duckdb.connect(duckdb_conn.chart_db_path(18))  # bind it: an unbound connect is
    holder.execute("SELECT 1")                              # refcounted away immediately
    try:
        assert _count(4) == 3, "an unrelated chart must be readable while another is written"
    finally:
        holder.close()


def test_drop_chart_db_removes_only_that_chart(real_layout):
    _write(4, [1, 2, 3])
    _write(18, [1, 2])
    duckdb_conn.drop_chart_db(18)
    assert not os.path.exists(duckdb_conn.chart_db_path(18))
    assert os.path.exists(duckdb_conn.chart_db_path(4))
    assert _count(4) == 3


def test_read_only_open_of_a_never_backpopped_chart_does_not_raise(real_layout):
    """A read_only open of a missing file errors in DuckDB, so get_connection creates it
    empty first. Callers already treat "table not present" as "no data yet"."""
    conn = duckdb_conn.get_connection(999, read_only=True)
    try:
        assert conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            [table_name(999)],
        ).fetchone() is None
    finally:
        conn.close()


def test_migration_splits_the_legacy_file_and_removes_it(real_layout, tmp_path):
    """The one-off migration: copy each chart_<id>_data table out into its own file, then
    delete the legacy file once nothing is left in it."""
    legacy = real_layout
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    conn = duckdb.connect(legacy)
    for cid, n in ((4, 3), (18, 5), (7, 1)):
        conn.execute(f'CREATE TABLE "{table_name(cid)}" (n INTEGER)')
        conn.executemany(f'INSERT INTO "{table_name(cid)}" VALUES (?)', [(i,) for i in range(n)])
    conn.execute("CREATE TABLE not_a_chart (x INTEGER)")  # must be ignored, not migrated
    conn.close()

    from app.db import ensure_schema

    ensure_schema()

    assert _count(4) == 3
    assert _count(18) == 5
    assert _count(7) == 1
    assert not os.path.exists(legacy), "legacy file should be gone once emptied"


def test_migration_is_idempotent(real_layout):
    """Runs at both backend and worker startup, so it must be safe to run repeatedly."""
    legacy = real_layout
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    conn = duckdb.connect(legacy)
    conn.execute(f'CREATE TABLE "{table_name(4)}" (n INTEGER)')
    conn.executemany(f'INSERT INTO "{table_name(4)}" VALUES (?)', [(1,), (2,)])
    conn.close()

    from app.db import ensure_schema

    ensure_schema()
    ensure_schema()          # second boot: legacy file already gone
    ensure_schema()
    assert _count(4) == 2    # not duplicated, not lost


def test_legacy_chart_ids_ignores_non_chart_tables(real_layout):
    legacy = real_layout
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    conn = duckdb.connect(legacy)
    conn.execute('CREATE TABLE "chart_12_data" (n INTEGER)')
    conn.execute('CREATE TABLE "chart_3_data" (n INTEGER)')
    conn.execute('CREATE TABLE "_health" (n INTEGER)')
    conn.execute('CREATE TABLE "chart_notanumber_data" (n INTEGER)')
    try:
        assert duckdb_conn.legacy_chart_ids(conn) == [3, 12]
    finally:
        conn.close()
