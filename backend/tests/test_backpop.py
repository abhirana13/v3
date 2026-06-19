from datetime import date
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from app.backpop import duckdb_writer, run_backpop


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


def _create_chart(client, **overrides):
    payload = {
        "name": overrides.get("name", "bp-chart"),
        "query": (
            "SELECT event_date, country, dau FROM t "
            "WHERE event_date BETWEEN DATE '{START_DATE}' AND DATE '{END_DATE}'"
        ),
        "backpop_batch_size": 2,
        "cur_date_behavior": "daily",
        "cache_strategy": "append",
        "time_column": "event_date",
    }
    payload.update(overrides)
    r = client.post("/charts", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_backpop_replace_uses_configured_batch_size(client, duckdb_path):
    """Batched mode runs contiguous batch_size-day windows (no per-day fill-missing)."""
    chart = _create_chart(client, cur_date_behavior="batched", cache_strategy="replace")
    description = [
        ("event_date", 1082, None, None, None, None, None),
        ("country", 1043, None, None, None, None, None),
        ("dau", 20, None, None, None, None, None),
    ]
    rows = [(date(2026, 6, 14), "US", 100), (date(2026, 6, 14), "UK", 40)]
    ctx, _ = _mock_redshift(description, rows)
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        r = client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-13", "batch_size": 2},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["batches_completed"] == 2
    assert body["row_count"] == 4

    con = duckdb.connect(duckdb_path)
    count = con.execute(
        f"SELECT COUNT(*) FROM {duckdb_writer.table_name(chart['id'])}"
    ).fetchone()[0]
    con.close()
    assert count == 4


def test_backpop_append_first_run_fetches_one_batch_per_date(client, duckdb_path):
    """Append + time_column: fill-missing emits one batch per date regardless of batch_size."""
    chart = _create_chart(client, name="append-first-run", cache_strategy="append")
    description = [
        ("event_date", 1082, None, None, None, None, None),
        ("country", 1043, None, None, None, None, None),
        ("dau", 20, None, None, None, None, None),
    ]
    rows = [(date(2026, 6, 14), "US", 100), (date(2026, 6, 14), "UK", 40)]
    ctx, _ = _mock_redshift(description, rows)
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        r = client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-13", "batch_size": 2},
        )
    body = r.json()
    assert body["status"] == "success"
    assert body["batches_completed"] == 4   # one per missing day
    assert body["row_count"] == 8           # 4 batches * 2 rows


def test_backpop_substitutes_dates_into_query(client, duckdb_path):
    chart = _create_chart(client)
    captured_sql = []
    cursor = MagicMock()
    cursor.description = [("event_date", 1082, None, None, None, None, None)]
    cursor.fetchall.return_value = []
    cursor.execute.side_effect = lambda sql: captured_sql.append(sql)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-12", "batch_size": 1},
        )
    assert len(captured_sql) == 3
    assert "'2026-06-10'" in captured_sql[0]
    assert "'2026-06-11'" in captured_sql[1]
    assert "'2026-06-12'" in captured_sql[2]


def test_backpop_failure_records_error_and_partial_progress(client, duckdb_path):
    chart = _create_chart(client)
    description = [
        ("event_date", 1082, None, None, None, None, None),
        ("country", 1043, None, None, None, None, None),
        ("dau", 20, None, None, None, None, None),
    ]
    call = {"n": 0}

    def fake_execute(sql):
        call["n"] += 1
        if call["n"] == 2:
            raise RuntimeError("redshift kaboom")

    cursor = MagicMock()
    cursor.description = description
    cursor.fetchall.return_value = [(date(2026, 6, 10), "US", 50)]
    cursor.execute.side_effect = fake_execute
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        r = client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-13", "batch_size": 2},
        )
    body = r.json()
    assert body["status"] == "failed"
    assert "redshift kaboom" in body["error_message"]
    assert body["batches_completed"] == 1  # first batch succeeded


def test_backpop_replace_strategy_overwrites_batch_window(client, duckdb_path):
    chart = _create_chart(client, name="replace-chart", cache_strategy="replace")
    description = [
        ("event_date", 1082, None, None, None, None, None),
        ("dau", 20, None, None, None, None, None),
    ]
    ctx, _ = _mock_redshift(description, [(date(2026, 6, 10), 100)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-10", "batch_size": 1},
        )
    ctx2, _ = _mock_redshift(description, [(date(2026, 6, 10), 999)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx2):
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-10", "batch_size": 1},
        )

    con = duckdb.connect(duckdb_path)
    rows = con.execute(
        f"SELECT dau FROM {duckdb_writer.table_name(chart['id'])} ORDER BY 1"
    ).fetchall()
    con.close()
    assert rows == [(999,)]


def test_backpop_append_skips_dates_already_present(client, duckdb_path):
    """Append mode + time_column = fill-missing: re-running for the same date is a no-op."""
    chart = _create_chart(client, name="append-skip-chart", cache_strategy="append")
    description = [
        ("event_date", 1082, None, None, None, None, None),
        ("dau", 20, None, None, None, None, None),
    ]
    ctx, _ = _mock_redshift(description, [(date(2026, 6, 10), 100)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-10", "batch_size": 1},
        )
    ctx2, _ = _mock_redshift(description, [(date(2026, 6, 10), 999)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx2) as p:
        r = client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-10", "batch_size": 1},
        )
    # Second call should not have hit Redshift at all
    assert p.call_count == 0
    body = r.json()
    assert body["status"] == "success"
    assert body["batches_completed"] == 0
    assert body["row_count"] == 0

    con = duckdb.connect(duckdb_path)
    rows = con.execute(
        f"SELECT dau FROM {duckdb_writer.table_name(chart['id'])} ORDER BY 1"
    ).fetchall()
    con.close()
    assert rows == [(100,)]


def test_backpop_append_fills_only_missing_days(client, duckdb_path):
    """First run fills 2026-06-10 only. Second run for 2026-06-08..06-12 should
    fetch only the 4 missing days (08, 09, 11, 12) and skip 10."""
    chart = _create_chart(client, name="append-fill-chart", cache_strategy="append")
    description = [
        ("event_date", 1082, None, None, None, None, None),
        ("dau", 20, None, None, None, None, None),
    ]
    ctx1, _ = _mock_redshift(description, [(date(2026, 6, 10), 100)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx1):
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-10", "batch_size": 1},
        )

    captured_sql = []
    cursor2 = MagicMock()
    cursor2.description = description
    cursor2.fetchall.return_value = [(date(2026, 1, 1), 1)]
    cursor2.execute.side_effect = lambda sql: captured_sql.append(sql)
    conn = MagicMock()
    conn.cursor.return_value = cursor2
    ctx2 = MagicMock()
    ctx2.__enter__.return_value = conn
    ctx2.__exit__.return_value = False
    with patch("app.backpop.redshift_conn.connect", return_value=ctx2):
        r = client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-08", "to_date": "2026-06-12", "batch_size": 1},
        )
    body = r.json()
    assert body["status"] == "success"
    assert body["batches_completed"] == 4  # 08, 09, 11, 12 — skipped 10
    assert len(captured_sql) == 4
    sub_dates = ["2026-06-08", "2026-06-09", "2026-06-11", "2026-06-12"]
    for d in sub_dates:
        assert any(d in sql for sql in captured_sql), f"missing date {d} in queries"
    # 2026-06-10 should NOT have been re-queried
    assert not any("2026-06-10" in sql for sql in captured_sql)


def test_backpop_append_without_time_column_accumulates(client, duckdb_path):
    """When time_column is not set, fill-missing can't dedupe, so append accumulates."""
    chart = _create_chart(client, name="no-time-col", cache_strategy="append", time_column=None)
    description = [("dau", 20, None, None, None, None, None)]
    ctx, _ = _mock_redshift(description, [(100,)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-10", "batch_size": 1},
        )
    ctx2, _ = _mock_redshift(description, [(200,)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx2):
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-10", "batch_size": 1},
        )

    con = duckdb.connect(duckdb_path)
    count = con.execute(
        f"SELECT COUNT(*) FROM {duckdb_writer.table_name(chart['id'])}"
    ).fetchone()[0]
    con.close()
    assert count == 2


def test_backpop_batched_substitutes_window_dates(client, duckdb_path):
    """Batched mode fills {START_DATE}/{END_DATE} with each window's own bounds."""
    chart = _create_chart(
        client, name="batched-window", cur_date_behavior="batched", backpop_batch_size=2
    )
    captured_sql = []
    cursor = MagicMock()
    cursor.description = [("event_date", 1082, None, None, None, None, None)]
    cursor.fetchall.return_value = [(date(2026, 6, 10),)]
    cursor.execute.side_effect = lambda sql: captured_sql.append(sql)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-13", "batch_size": 2},
        )
    # 4 days / 2-day windows = 2 batches, each spanning its own START..END
    assert len(captured_sql) == 2
    assert "'2026-06-10'" in captured_sql[0] and "'2026-06-11'" in captured_sql[0]
    assert "'2026-06-12'" in captured_sql[1] and "'2026-06-13'" in captured_sql[1]


def test_backpop_batched_append_is_idempotent(client, duckdb_path):
    """A batched window overwrites its date range, so re-running the same range
    under cache_strategy='append' overwrites rather than duplicating rows."""
    chart = _create_chart(
        client,
        name="batched-idempotent",
        cur_date_behavior="batched",
        cache_strategy="append",
        backpop_batch_size=30,  # one window covers the whole range
    )
    description = [
        ("event_date", 1082, None, None, None, None, None),
        ("dau", 20, None, None, None, None, None),
    ]
    first = [(date(2026, 6, d), 100) for d in (10, 11, 12, 13)]
    ctx, _ = _mock_redshift(description, first)
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        r1 = client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-13"},
        )
    assert r1.json()["batches_completed"] == 1  # single 30-day window

    second = [(date(2026, 6, d), 999) for d in (10, 11, 12, 13)]
    ctx2, _ = _mock_redshift(description, second)
    with patch("app.backpop.redshift_conn.connect", return_value=ctx2):
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-13"},
        )

    con = duckdb.connect(duckdb_path)
    rows = con.execute(
        f"SELECT dau FROM {duckdb_writer.table_name(chart['id'])} ORDER BY 1"
    ).fetchall()
    con.close()
    assert rows == [(999,), (999,), (999,), (999,)]  # overwritten, not 8 rows


def test_backpop_rebuilds_cache_when_query_changes(client, duckdb_path):
    """Editing the query invalidates the cache: the next backpop REPLACES the
    already-cached dates instead of skipping them via append fill-missing."""
    chart = _create_chart(client, name="query-change", cache_strategy="append")
    description = [
        ("event_date", 1082, None, None, None, None, None),
        ("dau", 20, None, None, None, None, None),
    ]
    # first build: 2026-06-10 -> dau 100
    ctx, _ = _mock_redshift(description, [(date(2026, 6, 10), 100)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-10", "batch_size": 1},
        )

    # change the query (different SQL => different hash)
    new_query = "SELECT event_date, dau FROM t2 WHERE event_date BETWEEN DATE '{START_DATE}' AND DATE '{END_DATE}'"
    assert client.put(f"/charts/{chart['id']}", json={"query": new_query}).status_code == 200

    # re-backpop the SAME date: must hit Redshift (rebuild) and overwrite 100 -> 999
    ctx2, _ = _mock_redshift(description, [(date(2026, 6, 10), 999)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx2) as p:
        r = client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-10", "batch_size": 1},
        )
    assert p.call_count == 1  # NOT skipped — the query changed, so the date is refetched
    assert r.json()["status"] == "success"
    assert r.json()["row_count"] == 1

    con = duckdb.connect(duckdb_path)
    rows = con.execute(
        f"SELECT dau FROM {duckdb_writer.table_name(chart['id'])} ORDER BY 1"
    ).fetchall()
    con.close()
    assert rows == [(999,)]  # replaced — not [(100,)] (skipped) nor [(100,), (999,)] (appended)


def test_backpop_chart_not_found(client):
    r = client.post("/charts/9999/backpopulate", json={})
    assert r.status_code == 404


def test_list_backpop_runs_returns_most_recent_first(client, duckdb_path):
    chart = _create_chart(client, name="runs-list")
    description = [("event_date", 1082, None, None, None, None, None)]
    ctx, _ = _mock_redshift(description, [])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-10", "to_date": "2026-06-10", "batch_size": 1},
        )
        client.post(
            f"/charts/{chart['id']}/backpopulate",
            json={"from_date": "2026-06-11", "to_date": "2026-06-11", "batch_size": 1},
        )
    r = client.get(f"/charts/{chart['id']}/backpop-runs")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 2
    assert runs[0]["id"] > runs[1]["id"]


def test_backpop_rejects_from_after_to(client):
    chart = _create_chart(client, name="bad-range")
    r = client.post(
        f"/charts/{chart['id']}/backpopulate",
        json={"from_date": "2026-06-15", "to_date": "2026-06-10"},
    )
    assert r.status_code == 422
