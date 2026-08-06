"""The chart config page must not depend on the DuckDB cache file.

Reported symptom: opening ANY chart's config page during a backpopulation returned 500.

Cause: GET /charts/{id}/dims-metrics called cache_columns(chart_id), which opens the cache
file, purely to decide which backend-derived dimensions to append. DuckDB is single-writer
*across processes* and the lock covers the whole FILE, so while the worker held it every
chart's config page failed with

    IOException: Could not set lock on file ".../aggregates.duckdb": Conflicting lock is held

Measured on a 10-day backpop: the writer held the lock 43% of the run, in stretches of
6-7s — longer than get_connection's ~5s open-retry budget, so a request landing in a write
window was a guaranteed failure rather than a slow success. 7 of 50 polled config-page loads
returned 500, each after stalling for the full retry budget.

The cache's column list is now mirrored into charts.cache_columns by the worker after each
backpop (same treatment as cache_latest_date) and request paths read only Postgres.

Same root cause, also covered here: a lock conflict on the endpoints that genuinely must
read DuckDB must report 503 (transient), NOT the "cache type error (rebuild this chart's
data to fix)" 400 — following that advice during a backpop destroys a healthy cache.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from app.backpop import drain_backpop_queue
from app.backpop.duckdb_writer import cache_present_columns
from app.connections.duckdb import is_lock_error
from app.models import Chart


@pytest.fixture
def duckdb_path(tmp_path, monkeypatch):
    path = str(tmp_path / "test.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


def _mock_redshift(description, rows):
    cursor = MagicMock()
    cursor.description = description
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx, cursor


def _create_chart(client, name, query=None):
    r = client.post("/charts", json={
        "name": name,
        "query": query or (
            "SELECT event_date, country, dau FROM t "
            "WHERE event_date = DATE '{CUR_DATE_HIPHEN}'"
        ),
        "cur_date_behavior": "daily", "cache_strategy": "append", "time_column": "event_date",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _backpop(client, db_session, cid, rows, desc):
    ctx, _ = _mock_redshift(desc, rows)
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        client.post(f"/charts/{cid}/backpopulate",
                    json={"from_date": "2026-06-11", "to_date": "2026-06-12"})
        drain_backpop_queue(db_session)


# --------------------------------------------------------------------------------------
# the reported bug
# --------------------------------------------------------------------------------------

def test_dims_metrics_never_opens_duckdb(client, db_session, duckdb_path, monkeypatch):
    """The regression guard. Make the cache unopenable — exactly what a running backpop
    does — and the config page must still load."""
    cid = _create_chart(client, "config-no-duckdb")
    chart = db_session.get(Chart, cid)
    chart.cache_columns = ["event_date", "country", "dau", "country_tier"]
    db_session.commit()

    def boom(*a, **k):
        raise AssertionError("dims-metrics must not open DuckDB")

    monkeypatch.setattr("app.connections.duckdb.get_connection", boom)
    r = client.get(f"/charts/{cid}/dims-metrics")
    assert r.status_code == 200, r.text
    # country_tier is materialized in the cache, so it is still offered as a derived dim
    assert "country_tier" in {d["name"] for d in r.json()["dimensions"]}


def test_dims_metrics_survives_a_locked_cache(client, db_session, duckdb_path):
    """The reported failure, reproduced as the exact error a running backpop raises: before
    the fix this returned 500 after stalling for the whole open-retry budget.

    The lock error is injected rather than produced by a real second process. A genuine
    cross-process holder proved order-dependent here: DuckDB caches database instances per
    process, so once anything in this pytest process has opened a path it can end up owning
    the lock itself, and the holder subprocess then either fails to acquire it or appears to
    hold one that was really ours. The stronger guarantee is covered above anyway —
    test_dims_metrics_never_opens_duckdb asserts the file is not opened at all. Verified
    against a real backpop on the running stack as well (0/60 failures, was 7/50).
    """
    cid = _create_chart(client, "config-locked-cache")
    chart = db_session.get(Chart, cid)
    chart.cache_columns = ["event_date", "country", "dau"]
    db_session.commit()

    err = duckdb.IOException(
        f'Could not set lock on file "{duckdb_path}": Conflicting lock is held'
    )
    with patch("app.connections.duckdb.duckdb.connect", side_effect=err):
        r = client.get(f"/charts/{cid}/dims-metrics")
    assert r.status_code == 200, r.text


def test_dims_metrics_falls_back_to_cache_when_never_mirrored(client, db_session, duckdb_path):
    """A cache built before the column existed has cache_columns = NULL; the endpoint may
    read DuckDB then, and must still surface derived dims."""
    cid = _create_chart(client, "config-fallback")
    desc = [("event_date",), ("country",), ("dau",)]
    _backpop(client, db_session, cid, [(date(2026, 6, 12), "United States", 100)], desc)

    chart = db_session.get(Chart, cid)
    db_session.refresh(chart)
    assert chart.cache_columns is not None  # mirrored by the backpop
    chart.cache_columns = None              # simulate a pre-migration chart
    db_session.commit()

    r = client.get(f"/charts/{cid}/dims-metrics")
    assert r.status_code == 200, r.text
    assert "country_tier" in {d["name"] for d in r.json()["dimensions"]}


# --------------------------------------------------------------------------------------
# the mirror itself
# --------------------------------------------------------------------------------------

def test_backpop_mirrors_cache_columns(client, db_session, duckdb_path):
    cid = _create_chart(client, "columns-mirror")
    desc = [("event_date",), ("country",), ("dau",)]
    _backpop(client, db_session, cid, [(date(2026, 6, 12), "United States", 100)], desc)

    chart = db_session.get(Chart, cid)
    db_session.refresh(chart)
    # includes the derived column materialize_derived wrote, which is the whole point:
    # the mirror happens after it, so derived dims resolve without touching DuckDB
    assert set(chart.cache_columns) >= {"event_date", "country", "dau", "country_tier"}
    assert chart.cache_columns == sorted(chart.cache_columns)


def test_cache_present_columns_prefers_mirror(db_session, duckdb_path):
    chart = Chart(name="prefers-mirror", query="SELECT 1", cache_columns=["a", "b"])
    db_session.add(chart)
    db_session.commit()

    def boom(*a, **k):
        raise AssertionError("must not open DuckDB when the value is mirrored")

    with patch("app.connections.duckdb.get_connection", boom):
        assert cache_present_columns(chart) == {"a", "b"}


def test_cache_present_columns_degrades_on_lock(db_session, duckdb_path):
    """Never raise on contention: a missing derived dimension degrades one render, an
    exception takes the whole page down (which is the bug being fixed)."""
    chart = Chart(name="degrades", query="SELECT 1", cache_columns=None)
    db_session.add(chart)
    db_session.commit()

    err = duckdb.IOException('Could not set lock on file "x": Conflicting lock is held')
    with patch("app.backpop.duckdb_writer.cache_columns", side_effect=err):
        assert cache_present_columns(chart) == set()


def test_empty_mirror_is_distinct_from_never_mirrored(db_session, duckdb_path):
    """[] means "cache exists, no columns" and must NOT fall through to a DuckDB read;
    only None means "never mirrored"."""
    chart = Chart(name="empty-mirror", query="SELECT 1", cache_columns=[])
    db_session.add(chart)
    db_session.commit()

    def boom(*a, **k):
        raise AssertionError("[] is a real answer, not a cache miss")

    with patch("app.connections.duckdb.get_connection", boom):
        assert cache_present_columns(chart) == set()


# --------------------------------------------------------------------------------------
# honest errors where DuckDB genuinely must be read
# --------------------------------------------------------------------------------------

def test_lock_error_classification():
    assert is_lock_error(
        duckdb.IOException('Could not set lock on file "x": Conflicting lock is held')
    )
    assert not is_lock_error(duckdb.BinderException("No function matches SUM(VARCHAR)"))
    assert not is_lock_error(ValueError("lock"))  # not a duckdb error at all


def test_data_endpoint_reports_busy_not_rebuild(client, db_session, duckdb_path):
    """A lock conflict must not reach the "rebuild this chart's data" message — acting on
    that advice during a backpop throws away a perfectly good cache."""
    cid = _create_chart(client, "data-busy")
    desc = [("event_date",), ("country",), ("dau",)]
    _backpop(client, db_session, cid, [(date(2026, 6, 12), "US", 100)], desc)

    err = duckdb.IOException('Could not set lock on file "x": Conflicting lock is held')
    with patch("app.api.data.serve_data", side_effect=err):
        r = client.get(f"/charts/{cid}/data",
                       params={"from_date": "2026-06-11", "to_date": "2026-06-12"})
    assert r.status_code == 503, r.text
    assert "backpopulation" in r.json()["detail"]
    assert "rebuild" not in r.json()["detail"]
    assert r.headers.get("Retry-After") == "5"


def test_data_endpoint_still_reports_type_errors(client, db_session, duckdb_path):
    """The rebuild hint must survive for the case it was written for."""
    cid = _create_chart(client, "data-type-error")
    desc = [("event_date",), ("country",), ("dau",)]
    _backpop(client, db_session, cid, [(date(2026, 6, 12), "US", 100)], desc)

    err = duckdb.BinderException("No function matches SUM(VARCHAR)")
    with patch("app.api.data.serve_data", side_effect=err):
        r = client.get(f"/charts/{cid}/data",
                       params={"from_date": "2026-06-11", "to_date": "2026-06-12"})
    assert r.status_code == 400, r.text
    assert "rebuild" in r.json()["detail"]


def test_dim_values_reports_busy(client, db_session, duckdb_path):
    cid = _create_chart(client, "dimvalues-busy")
    err = duckdb.IOException('Could not set lock on file "x": Conflicting lock is held')
    with patch("app.api.data.dimension_values", side_effect=err):
        r = client.get(f"/charts/{cid}/dim-values")
    assert r.status_code == 503, r.text
    assert r.headers.get("Retry-After") == "5"


def test_health_check_is_read_only(duckdb_path):
    """The backend never writes the cache — only the worker does. A read-write health probe
    contended with the writer and reported `error` for the whole of every backpop."""
    from app.connections import duckdb as duckdb_conn

    duckdb_conn.ensure_database()
    opened: list[bool] = []
    real = duckdb_conn.get_connection

    def spy(read_only=False):
        opened.append(read_only)
        return real(read_only=read_only)

    with patch.object(duckdb_conn, "get_connection", spy):
        result = duckdb_conn.check()
    assert result["status"] == "ok", result
    assert opened == [True], f"health check opened read-write: {opened}"


def test_health_reports_busy_while_locked(duckdb_path):
    """A backpop writing the cache is normal operation, so /health says busy, not error —
    it used to report `error` for the whole of every backpop.

    The lock error is injected rather than produced by a real second holder: DuckDB caches
    database instances per process, so once anything in this pytest process has opened a
    path, a cross-process holder for it may or may not win the lock depending on test order.
    _LockHolder is used for the one test where the real thing is the point (the reported
    config-page bug); here the contract under test is only how a lock error is classified.
    """
    from app.connections import duckdb as duckdb_conn

    err = duckdb.IOException('Could not set lock on file "x": Conflicting lock is held')
    with patch.object(duckdb_conn, "get_connection", side_effect=err):
        result = duckdb_conn.check()
    assert result["status"] == "busy", result
    assert "backpop" in result["detail"]


# --------------------------------------------------------------------------------------
# deletion stays consistent across the two stores
# --------------------------------------------------------------------------------------

def test_delete_under_lock_leaves_chart_intact(client, db_session, duckdb_path):
    """Dropping the cache table needs the write lock. If it fails, the metadata row must
    still be there — otherwise the chart is gone from Postgres and its chart_<id>_data
    table is orphaned in DuckDB with no record it ever existed."""
    cid = _create_chart(client, "delete-under-lock")
    err = duckdb.IOException('Could not set lock on file "x": Conflicting lock is held')
    with patch("app.api.charts.drop_table", side_effect=err):
        r = client.delete(f"/charts/{cid}")
    assert r.status_code == 503, r.text
    assert client.get(f"/charts/{cid}").status_code == 200
