"""Phase 4 pressure: batch tiling coverage + append idempotency.

(Templating, fill-missing-skips-present, failure/partial-progress, run-state,
and scheduler targeting are in tests/test_backpop.py, test_scheduler.py.)"""

import re
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from app.backpop import drain_backpop_queue, duckdb_writer

DESC = [
    ("event_date", 1082, None, None, None, None, None),
    ("country", 1043, None, None, None, None, None),
    ("dau", 20, None, None, None, None, None),
]
QUERY = (
    "SELECT event_date, country, dau FROM t "
    "WHERE event_date BETWEEN DATE '{START_DATE}' AND DATE '{END_DATE}'"
)


@pytest.fixture
def duckdb_path(tmp_path, monkeypatch):
    path = str(tmp_path / "bp.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


def _ctx(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def _create(client, **overrides):
    payload = {"name": "bp", "query": QUERY, "time_column": "event_date", "cache_strategy": "append"}
    payload.update(overrides)
    r = client.post("/charts", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_replace_batches_tile_the_range_contiguously(client, db_session, duckdb_path):
    """Every day in [from,to] is covered by exactly one batch, each <= batch_size,
    no overlap, no gap."""
    cid = _create(client, cur_date_behavior="batched", cache_strategy="replace", backpop_batch_size=7)
    calls = []
    cursor = MagicMock()
    cursor.description = DESC
    cursor.execute.side_effect = lambda sql, *a, **k: calls.append(sql)

    def _rows_for_last_batch():
        ds = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", calls[-1])))
        if len(ds) < 2:
            return []
        s, e = date.fromisoformat(ds[0]), date.fromisoformat(ds[-1])
        out, d = [], s
        while d <= e:
            out.append((d, "US", 100))
            d += timedelta(days=1)
        return out

    cursor.fetchall.side_effect = _rows_for_last_batch

    with patch("app.backpop.redshift_conn.connect", return_value=_ctx(cursor)):
        r = client.post(
            f"/charts/{cid}/backpopulate",
            json={"from_date": "2026-06-01", "to_date": "2026-06-20", "batch_size": 7},
        )
        assert r.status_code == 200, r.text
        drain_backpop_queue(db_session)  # the work happens in the drain now (async endpoint)
    run = client.get(f"/charts/{cid}/backpop-runs").json()[0]
    assert run["batches_completed"] == 3  # ceil(20/7)

    windows = []
    for sql in calls:
        ds = re.findall(r"(\d{4}-\d{2}-\d{2})", sql)
        if len(ds) >= 2:
            windows.append((date.fromisoformat(ds[0]), date.fromisoformat(ds[1])))

    covered = set()
    for s, e in windows:
        assert e >= s
        assert (e - s).days + 1 <= 7  # batch-size bound
        d = s
        while d <= e:
            assert d not in covered, f"day {d} covered twice"  # no overlap
            covered.add(d)
            d += timedelta(days=1)
    expected = {date(2026, 6, 1) + timedelta(days=i) for i in range(20)}
    assert covered == expected  # full coverage, no gaps


def test_append_backpop_is_idempotent(client, db_session, duckdb_path):
    """Running the same append backpop twice fetches the gaps once, then nothing —
    row count stable, no duplicate days."""
    cid = _create(client, cache_strategy="append")

    state = {"sql": ""}
    cursor = MagicMock()
    cursor.description = DESC
    cursor.execute.side_effect = lambda sql, *a, **k: state.update(sql=sql)
    # fill-missing => one single-day batch per missing day; return a row dated to it
    cursor.fetchall.side_effect = lambda: [
        (date.fromisoformat(d), "US", 100)
        for d in sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", state["sql"])))
    ]

    body = {"from_date": "2026-06-01", "to_date": "2026-06-05"}
    with patch("app.backpop.redshift_conn.connect", return_value=_ctx(cursor)):
        client.post(f"/charts/{cid}/backpopulate", json=body)
        drain_backpop_queue(db_session)  # run 1 fetches the 5 missing days
        run1 = client.get(f"/charts/{cid}/backpop-runs").json()[0]
        client.post(f"/charts/{cid}/backpopulate", json=body)
        drain_backpop_queue(db_session)  # run 2: all present now -> nothing to fetch
        run2 = client.get(f"/charts/{cid}/backpop-runs").json()[0]

    assert run1["row_count"] == 5   # 5 missing days fetched
    assert run2["row_count"] == 0   # nothing left to fetch (idempotent)

    con = duckdb.connect(duckdb_path)
    table = duckdb_writer.table_name(cid)
    total = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    distinct_days = con.execute(f'SELECT COUNT(DISTINCT event_date) FROM "{table}"').fetchone()[0]
    con.close()
    assert total == 5
    assert distinct_days == 5  # no duplicate-day rows
