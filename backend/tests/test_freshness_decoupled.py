"""The home page's freshness must not depend on the DuckDB cache.

DuckDB is single-writer across processes. /charts/overview used to call latest_data_date()
per chart — one DuckDB connection each — so while the worker held a write lock during a
backpop, every chart's freshness read retried against it and the home page stalled (measured
1.2s on a small chart; unusable with big tables, where a batch inserts tens of thousands of
rows and materialize_derived runs a full-table UPDATE).

Freshness is now mirrored into charts.cache_latest_date by the worker after each backpop and
the home page reads only Postgres.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.backpop import drain_backpop_queue


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


def _create_chart(client, name):
    r = client.post("/charts", json={
        "name": name,
        "query": "SELECT event_date, country, dau FROM t WHERE event_date = DATE '{CUR_DATE_HIPHEN}'",
        "cur_date_behavior": "daily", "cache_strategy": "append", "time_column": "event_date",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_backpop_mirrors_latest_date_into_postgres(client, db_session, duckdb_path):
    cid = _create_chart(client, "freshness-mirror")
    desc = [("event_date",), ("country",), ("dau",)]
    ctx, _ = _mock_redshift(desc, [(date(2026, 6, 12), "US", 100)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        client.post(f"/charts/{cid}/backpopulate",
                    json={"from_date": "2026-06-11", "to_date": "2026-06-12"})
        drain_backpop_queue(db_session)

    from app.models import Chart
    chart = db_session.get(Chart, cid)
    db_session.refresh(chart)
    assert chart.cache_latest_date == date(2026, 6, 12)

    row = next(c for c in client.get("/charts/overview").json() if c["id"] == cid)
    assert row["latest_data_date"] == "2026-06-12"


def test_overview_never_opens_duckdb(client, db_session, duckdb_path, monkeypatch):
    """The regression guard: make the cache unopenable and the home page must still work.

    If anything reintroduces a per-chart DuckDB read here, this fails — which is exactly the
    condition a running backpop creates (the writer holds the lock).
    """
    cid = _create_chart(client, "overview-no-duckdb")
    from app.models import Chart
    chart = db_session.get(Chart, cid)
    chart.cache_latest_date = date(2026, 7, 1)
    db_session.commit()

    def boom(*a, **k):
        raise AssertionError("charts_overview must not open DuckDB")

    monkeypatch.setattr("app.connections.duckdb.get_connection", boom)
    r = client.get("/charts/overview")
    assert r.status_code == 200, r.text
    row = next(c for c in r.json() if c["id"] == cid)
    assert row["latest_data_date"] == "2026-07-01"


def test_overview_reports_no_data_when_never_backpopped(client, duckdb_path):
    cid = _create_chart(client, "overview-fresh-chart")
    row = next(c for c in client.get("/charts/overview").json() if c["id"] == cid)
    assert row["latest_data_date"] is None


def test_freshness_endpoint_prefers_mirrored_value(client, db_session, duckdb_path, monkeypatch):
    """The single-chart freshness endpoint uses the mirrored value too, so opening a chart
    while its own backpop runs doesn't wait on the cache lock."""
    cid = _create_chart(client, "freshness-endpoint")
    from app.models import Chart
    chart = db_session.get(Chart, cid)
    chart.cache_latest_date = date(2026, 7, 5)
    db_session.commit()

    def boom(*a, **k):
        raise AssertionError("freshness must not open DuckDB when the value is mirrored")

    monkeypatch.setattr("app.connections.duckdb.get_connection", boom)
    r = client.get(f"/charts/{cid}/freshness")
    assert r.status_code == 200, r.text
    assert r.json()["latest_data_date"] == "2026-07-05"
