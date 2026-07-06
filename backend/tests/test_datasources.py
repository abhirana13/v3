"""Per-chart Redshift database selection (same cluster, different db name)."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.backpop import drain_backpop_queue


@pytest.fixture
def duckdb_path(tmp_path, monkeypatch):
    path = str(tmp_path / "ds.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


@pytest.fixture
def two_databases(monkeypatch):
    """Configure the allowlist: default 'analytics' + a second 'events' on the same cluster."""
    monkeypatch.setattr("app.config.settings.redshift_database", "analytics")
    monkeypatch.setattr("app.config.settings.redshift_databases", "analytics,events")


def _ctx(rows=None):
    cursor = MagicMock()
    cursor.description = [
        ("event_date", 1082, None, None, None, None, None),
        ("dau", 20, None, None, None, None, None),
    ]
    cursor.fetchall.return_value = rows or []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def _create(client, **overrides):
    payload = {
        "name": overrides.pop("name", "ds-chart"),
        "query": "SELECT event_date, dau FROM t WHERE event_date = DATE '{CUR_DATE_HIPHEN}'",
        "time_column": "event_date",
        "cache_strategy": "append",
        "cur_date_behavior": "daily",
    }
    payload.update(overrides)
    return client.post("/charts", json=payload)


def test_datasources_lists_allowlist_and_default(client, two_databases):
    r = client.get("/datasources")
    assert r.status_code == 200
    body = r.json()
    assert body["databases"] == ["analytics", "events"]
    assert body["default"] == "analytics"


def test_datasources_defaults_to_single_db_when_no_allowlist(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.redshift_database", "analytics")
    monkeypatch.setattr("app.config.settings.redshift_databases", "")
    body = client.get("/datasources").json()
    assert body["databases"] == ["analytics"]


def test_chart_persists_selected_database(client, two_databases):
    r = _create(client, name="on-events", database="events")
    assert r.status_code == 201, r.text
    assert r.json()["database"] == "events"
    # round-trips on read
    got = client.get(f"/charts/{r.json()['id']}").json()
    assert got["database"] == "events"


def test_chart_defaults_database_to_null(client, two_databases):
    r = _create(client, name="default-db")
    assert r.status_code == 201, r.text
    assert r.json()["database"] is None  # null => backend uses settings.redshift_database


def test_chart_rejects_unknown_database(client, two_databases):
    r = _create(client, name="bad-db", database="nope")
    assert r.status_code == 422, r.text


def test_update_rejects_unknown_database(client, two_databases):
    cid = _create(client, name="upd-db").json()["id"]
    assert client.put(f"/charts/{cid}", json={"database": "events"}).status_code == 200
    assert client.put(f"/charts/{cid}", json={"database": "ghost"}).status_code == 422


def test_backpop_connects_to_chart_database(client, db_session, duckdb_path, two_databases):
    """A backpop run opens Redshift against the chart's selected database."""
    cid = _create(client, name="bp-events", database="events").json()["id"]
    with patch("app.backpop.redshift_conn.connect") as connect:
        connect.return_value = _ctx([(date(2026, 6, 10), 5)])
        client.post(f"/charts/{cid}/backpopulate", json={"from_date": "2026-06-10", "to_date": "2026-06-10", "batch_size": 1})
        drain_backpop_queue(db_session)
    assert connect.call_count >= 1
    assert all(c.kwargs.get("database") == "events" for c in connect.call_args_list)


def test_introspect_connects_to_chart_database(client, two_databases):
    """The query editor's introspection runs against the chart's selected database."""
    cid = _create(client, name="intro-events", database="events").json()["id"]
    with patch("app.introspection.redshift_conn.connect") as connect:
        connect.return_value = _ctx()
        r = client.post(f"/charts/{cid}/introspect")
    assert r.status_code == 200, r.text
    assert connect.call_args.kwargs.get("database") == "events"


def test_default_chart_connects_to_default_database(client):
    """A chart with no database selected passes database=None (connect falls back to default)."""
    r = _create(client, name="intro-default")
    cid = r.json()["id"]
    with patch("app.introspection.redshift_conn.connect") as connect:
        connect.return_value = _ctx()
        client.post(f"/charts/{cid}/introspect")
    assert connect.call_args.kwargs.get("database") is None
