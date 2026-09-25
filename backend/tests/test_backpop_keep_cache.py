"""Keeping a chart's cached history across an ADDITIVE query edit.

The scenario these exist for: a chart is backpopulated over months, then a new game is added
to the query's `game_id IN (...)` list. That edit changes which ROWS come back, not what the
existing ones mean — but it changes the query hash, and a hash change drops the whole cache
and rebuilds only the range of the run that noticed. Months of history, gone, in exchange for
however many days were asked for.

`keep_cache` is the opt-in that says "this edit only adds rows": don't drop, re-pull just this
range, leave everything outside it alone.

It is opt-in rather than automatic because the backend cannot tell an additive edit from a
corrective one. Changing a filter or fixing an aggregation makes the CACHED rows wrong, and
for those a rebuild is the only right answer — so the destructive default stays the default.
"""

import re
from datetime import date
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from app.backpop import drain_backpop_queue, duckdb_writer


@pytest.fixture
def duckdb_path(tmp_path, monkeypatch):
    path = str(tmp_path / "test.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


def _mock_per_day(description, rows_for_day):
    """A Redshift mock whose result depends on which day the batch substituted into the SQL.

    The shared `_mock_redshift` helper returns one fixed result for every call, which cannot
    express "these days were cached earlier and those are being re-pulled now" — the whole
    thing under test here.
    """
    state = {"sql": ""}
    cursor = MagicMock()
    cursor.description = description
    cursor.execute.side_effect = lambda sql: state.__setitem__("sql", sql)

    def _fetchall():
        m = re.search(r"\d{4}-\d{2}-\d{2}", state["sql"])
        return rows_for_day(date.fromisoformat(m.group(0))) if m else []

    cursor.fetchall.side_effect = _fetchall
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def _bp(client, db_session, chart_id, **body):
    r = client.post(f"/charts/{chart_id}/backpopulate", json=body)
    assert r.status_code == 200, r.text
    drain_backpop_queue(db_session)
    return client.get(f"/charts/{chart_id}/backpop-runs").json()[0]


GAME_DESC = [
    ("event_date", 1082, None, None, None, None, None),
    ("game_id", 20, None, None, None, None, None),
    ("dau", 20, None, None, None, None, None),
]

Q_TWO_GAMES = (
    "SELECT event_date, game_id, dau FROM t "
    "WHERE event_date BETWEEN DATE '{START_DATE}' AND DATE '{END_DATE}' "
    "AND game_id IN (3, 4)"
)
# the edit under test: one more game in the IN list, same columns
Q_THREE_GAMES = Q_TWO_GAMES.replace("IN (3, 4)", "IN (3, 4, 8)")


def _chart(client, query=Q_TWO_GAMES, **overrides):
    payload = {
        "name": overrides.pop("name", "keep-cache-chart"),
        "query": query,
        "backpop_batch_size": 1,
        "cur_date_behavior": "daily",
        "cache_strategy": "append",
        "time_column": "event_date",
    }
    payload.update(overrides)
    r = client.post("/charts", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _cached(path, chart_id, columns="event_date, game_id, dau"):
    con = duckdb.connect(path)
    try:
        return con.execute(
            f"SELECT {columns} FROM {duckdb_writer.table_name(chart_id)} ORDER BY 1, 2"
        ).fetchall()
    finally:
        con.close()


def _build_five_days(client, db_session, chart_id):
    """Cache 2026-06-01..05 with games 3 and 4, the history a rebuild would destroy."""
    ctx = _mock_per_day(GAME_DESC, lambda d: [(d, 3, 100), (d, 4, 200)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        body = _bp(client, db_session, chart_id, from_date="2026-06-01", to_date="2026-06-05")
    assert body["status"] == "success", body
    return body


# ---------------------------------------------------------------------------------------
# the reported behaviour, and its fix
# ---------------------------------------------------------------------------------------


def test_query_edit_without_keep_cache_still_rebuilds(client, db_session, duckdb_path):
    """The existing, destructive default — pinned deliberately.

    This is the behaviour that loses history, and it stays the default because the backend
    cannot tell an additive edit from a corrective one. Changing it silently would leave stale
    rows behind after a genuine fix, which is worse than losing a cache that can be re-pulled.
    """
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])
    assert client.put(f"/charts/{chart['id']}", json={"query": Q_THREE_GAMES}).status_code == 200

    ctx = _mock_per_day(GAME_DESC, lambda d: [(d, 3, 100), (d, 4, 200), (d, 8, 50)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        body = _bp(client, db_session, chart["id"], from_date="2026-06-04", to_date="2026-06-05")
    assert body["status"] == "success"

    days = {r[0] for r in _cached(duckdb_path, chart["id"])}
    assert days == {date(2026, 6, 4), date(2026, 6, 5)}, "the first three days should be gone"


def test_keep_cache_preserves_history_and_adds_the_new_game(client, db_session, duckdb_path):
    """The fix: old days survive untouched, and the new game lands on the re-pulled range."""
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])
    assert client.put(f"/charts/{chart['id']}", json={"query": Q_THREE_GAMES}).status_code == 200

    ctx = _mock_per_day(GAME_DESC, lambda d: [(d, 3, 100), (d, 4, 200), (d, 8, 50)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        body = _bp(
            client, db_session, chart["id"],
            from_date="2026-06-04", to_date="2026-06-05", keep_cache=True,
        )
    assert body["status"] == "success", body

    rows = _cached(duckdb_path, chart["id"])
    days = sorted({r[0] for r in rows})
    assert days == [date(2026, 6, d) for d in (1, 2, 3, 4, 5)], "history was not preserved"

    # untouched days keep exactly the two games they were built with
    for d in (1, 2, 3):
        games = sorted(r[1] for r in rows if r[0] == date(2026, 6, d))
        assert games == [3, 4], f"2026-06-0{d} should not have changed"

    # the re-pulled range gained the new game
    for d in (4, 5):
        games = sorted(r[1] for r in rows if r[0] == date(2026, 6, d))
        assert games == [3, 4, 8], f"2026-06-0{d} should now include game 8"


def test_keep_cache_repulls_days_that_were_already_cached(client, db_session, duckdb_path):
    """Without the re-pull this feature would be a no-op that reports success.

    The days needing the new game are exactly the days already cached, and append-mode
    fill-missing skips precisely those. Suppressing the drop alone would leave the cache
    unchanged while the run claimed to have worked — a worse answer than the data loss.
    """
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])
    assert client.put(f"/charts/{chart['id']}", json={"query": Q_THREE_GAMES}).status_code == 200

    ctx = _mock_per_day(GAME_DESC, lambda d: [(d, 3, 100), (d, 4, 200), (d, 8, 50)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx) as p:
        body = _bp(
            client, db_session, chart["id"],
            from_date="2026-06-01", to_date="2026-06-03", keep_cache=True,
        )
    assert p.call_count == 3, "every requested day must be re-queried, not skipped"
    assert body["status"] == "success"

    rows = _cached(duckdb_path, chart["id"])
    for d in (1, 2, 3):
        assert sorted(r[1] for r in rows if r[0] == date(2026, 6, d)) == [3, 4, 8]
    for d in (4, 5):
        assert sorted(r[1] for r in rows if r[0] == date(2026, 6, d)) == [3, 4]


def test_keep_cache_clears_the_stale_hash_so_the_next_run_is_normal(
    client, db_session, duckdb_path
):
    """A successful keep-cache run means the cache now reflects the current query."""
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])
    assert client.put(f"/charts/{chart['id']}", json={"query": Q_THREE_GAMES}).status_code == 200
    assert client.get(f"/charts/{chart['id']}/freshness").json()["query_changed"] is True

    ctx = _mock_per_day(GAME_DESC, lambda d: [(d, 3, 100), (d, 4, 200), (d, 8, 50)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        _bp(client, db_session, chart["id"], from_date="2026-06-05", to_date="2026-06-05", keep_cache=True)

    assert client.get(f"/charts/{chart['id']}/freshness").json()["query_changed"] is False


# ---------------------------------------------------------------------------------------
# the guard: "additive" is the caller's claim, and this is the part we can check
# ---------------------------------------------------------------------------------------


def test_keep_cache_refuses_a_query_that_gained_a_column(client, db_session, duckdb_path):
    """A new column cannot be inserted into the old table — and the cache must survive saying so."""
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])
    wider = Q_THREE_GAMES.replace("SELECT event_date, game_id, dau", "SELECT event_date, game_id, dau, revenue")
    assert client.put(f"/charts/{chart['id']}", json={"query": wider}).status_code == 200

    desc = GAME_DESC + [("revenue", 20, None, None, None, None, None)]
    ctx = _mock_per_day(desc, lambda d: [(d, 3, 100, 1.5)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        body = _bp(
            client, db_session, chart["id"],
            from_date="2026-06-04", to_date="2026-06-05", keep_cache=True,
        )
    assert body["status"] == "failed"
    assert "revenue" in body["error_message"]
    assert "ColumnMismatch" in body["error_message"]

    # the point of failing early: nothing was dropped and no day was deleted
    rows = _cached(duckdb_path, chart["id"])
    assert sorted({r[0] for r in rows}) == [date(2026, 6, d) for d in (1, 2, 3, 4, 5)]
    assert len(rows) == 10


def test_keep_cache_refuses_a_query_that_lost_a_column(client, db_session, duckdb_path):
    """The quiet direction: the insert would succeed and write NULLs into the dropped column."""
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])
    narrower = "SELECT event_date, dau FROM t WHERE event_date BETWEEN DATE '{START_DATE}' AND DATE '{END_DATE}'"
    assert client.put(f"/charts/{chart['id']}", json={"query": narrower}).status_code == 200

    desc = [GAME_DESC[0], GAME_DESC[2]]
    ctx = _mock_per_day(desc, lambda d: [(d, 100)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        body = _bp(
            client, db_session, chart["id"],
            from_date="2026-06-04", to_date="2026-06-05", keep_cache=True,
        )
    assert body["status"] == "failed"
    assert "game_id" in body["error_message"]
    assert len(_cached(duckdb_path, chart["id"])) == 10  # untouched


def test_backend_derived_columns_do_not_trip_the_guard(client, db_session, duckdb_path):
    """country_tier is written by the backend, not the query, so it is not a mismatch.

    materialize_derived ALTERs the cache table to add it. Comparing the query's columns to
    the table's raw column list would therefore flag every chart that has a country column as
    incompatible, making keep_cache unusable on exactly the charts most likely to need it.
    """
    q = (
        "SELECT event_date, country, dau FROM t "
        "WHERE event_date BETWEEN DATE '{START_DATE}' AND DATE '{END_DATE}' AND game_id IN (3)"
    )
    chart = _chart(client, query=q, name="derived-cols")
    desc = [
        ("event_date", 1082, None, None, None, None, None),
        ("country", 1043, None, None, None, None, None),
        ("dau", 20, None, None, None, None, None),
    ]
    ctx = _mock_per_day(desc, lambda d: [(d, "US", 100), (d, "PH", 20)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        _bp(client, db_session, chart["id"], from_date="2026-06-01", to_date="2026-06-02")

    # the derived column really is in the table — otherwise this test proves nothing
    assert "country_tier" in duckdb_writer.cache_columns(chart["id"])

    assert client.put(
        f"/charts/{chart['id']}", json={"query": q.replace("IN (3)", "IN (3, 8)")}
    ).status_code == 200
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        body = _bp(
            client, db_session, chart["id"],
            from_date="2026-06-02", to_date="2026-06-02", keep_cache=True,
        )
    assert body["status"] == "success", body["error_message"]


def test_keep_cache_does_not_clear_a_day_that_reads_empty(client, db_session, duckdb_path):
    """Unlike force, a keep-cache run never lets a blank read delete history.

    force means "match Redshift for this range", so it may clear a day. keep_cache means "do
    not lose my data", so a transient empty read must leave the cached day standing.
    """
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])
    assert client.put(f"/charts/{chart['id']}", json={"query": Q_THREE_GAMES}).status_code == 200

    ctx = _mock_per_day(GAME_DESC, lambda d: [])  # Redshift returns nothing for every day
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        body = _bp(
            client, db_session, chart["id"],
            from_date="2026-06-01", to_date="2026-06-05", keep_cache=True,
        )
    assert body["status"] == "success"
    assert len(_cached(duckdb_path, chart["id"])) == 10, "an empty read wiped cached days"


def test_force_still_clears_a_day_that_reads_empty(client, db_session, duckdb_path):
    """The contrast that makes the previous test meaningful."""
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])

    ctx = _mock_per_day(GAME_DESC, lambda d: [])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        body = _bp(
            client, db_session, chart["id"],
            from_date="2026-06-01", to_date="2026-06-05", force=True,
        )
    assert body["status"] == "success"
    assert _cached(duckdb_path, chart["id"]) == []


# ---------------------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------------------


def test_keep_cache_survives_the_queue_hop(client, db_session, duckdb_path):
    """The API queues; a separate worker process executes. A flag not on the row is lost."""
    chart = _chart(client)
    r = client.post(
        f"/charts/{chart['id']}/backpopulate",
        json={"from_date": "2026-06-01", "to_date": "2026-06-01", "keep_cache": True},
    )
    assert r.status_code == 200
    assert r.json()["keep_cache"] is True

    from app.models import BackpopRun

    assert db_session.get(BackpopRun, r.json()["id"]).keep_cache is True


def test_keep_cache_defaults_off(client, db_session, duckdb_path):
    """Nothing about an ordinary backpop changes."""
    chart = _chart(client)
    r = client.post(f"/charts/{chart['id']}/backpopulate", json={"from_date": "2026-06-01", "to_date": "2026-06-01"})
    assert r.json()["keep_cache"] is False


def test_freshness_reports_query_changed(client, db_session, duckdb_path):
    chart = _chart(client)
    # never backpopped => nothing to compare against, so not "changed"
    assert client.get(f"/charts/{chart['id']}/freshness").json()["query_changed"] is False

    _build_five_days(client, db_session, chart["id"])
    assert client.get(f"/charts/{chart['id']}/freshness").json()["query_changed"] is False

    assert client.put(f"/charts/{chart['id']}", json={"query": Q_THREE_GAMES}).status_code == 200
    assert client.get(f"/charts/{chart['id']}/freshness").json()["query_changed"] is True


# ---------------------------------------------------------------------------------------
# the pre-flight check: the tool answers "was this edit structural?" for itself
# ---------------------------------------------------------------------------------------


def _mock_describe(columns):
    """A LIMIT 0 round trip: cursor.description only, no rows."""
    cursor = MagicMock()
    cursor.description = [(c, 20, None, None, None, None, None) for c in columns]
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def test_compat_says_adding_a_game_is_safe(client, db_session, duckdb_path):
    """The reported complaint: an IN-list edit cannot change columns, so nothing should warn."""
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])
    with patch("app.introspection.redshift_conn.connect",
               return_value=_mock_describe(["event_date", "game_id", "dau"])):
        r = client.post(f"/charts/{chart['id']}/cache-compat", json={"query": Q_THREE_GAMES})
    assert r.status_code == 200
    body = r.json()
    # asserted field-by-field rather than as a whole dict: this response grows fields, and a
    # test that fails because one was ADDED tells you nothing about whether the verdict is right
    assert body["checked"] is True and body["has_cache"] is True
    assert body["columns_match"] is True
    assert body["added"] == [] and body["removed"] == []
    assert body["keep_supported"] is True


def test_compat_flags_an_added_column(client, db_session, duckdb_path):
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])
    with patch("app.introspection.redshift_conn.connect",
               return_value=_mock_describe(["event_date", "game_id", "dau", "revenue"])):
        body = client.post(f"/charts/{chart['id']}/cache-compat", json={"query": "select 1"}).json()
    assert body["columns_match"] is False
    assert body["added"] == ["revenue"] and body["removed"] == []


def test_compat_flags_a_removed_column(client, db_session, duckdb_path):
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])
    with patch("app.introspection.redshift_conn.connect",
               return_value=_mock_describe(["event_date", "dau"])):
        body = client.post(f"/charts/{chart['id']}/cache-compat", json={"query": "select 1"}).json()
    assert body["columns_match"] is False
    assert body["removed"] == ["game_id"]


def test_compat_needs_no_redshift_when_there_is_no_cache(client, db_session, duckdb_path):
    """Nothing to protect => don't spend a round trip, and don't imply a risk."""
    chart = _chart(client)
    with patch("app.introspection.redshift_conn.connect", side_effect=AssertionError("must not connect")):
        body = client.post(f"/charts/{chart['id']}/cache-compat", json={}).json()
    assert body["has_cache"] is False and body["columns_match"] is True


def test_compat_degrades_instead_of_blocking_when_redshift_is_down(client, db_session, duckdb_path):
    """An unreachable warehouse is a reason to ask the editor, not to refuse a dialog."""
    chart = _chart(client)
    _build_five_days(client, db_session, chart["id"])
    with patch("app.introspection.redshift_conn.connect", side_effect=OSError("connection refused")):
        r = client.post(f"/charts/{chart['id']}/cache-compat", json={"query": Q_THREE_GAMES})
    assert r.status_code == 200
    body = r.json()
    assert body["checked"] is False and "connection refused" in body["message"]


def test_compat_and_the_run_guard_cannot_disagree(client, db_session, duckdb_path):
    """Both answer from column_diff(), so a green dialog can't be followed by a refusal."""
    from app.backpop import column_diff, incompatible_columns
    from app.models import Chart

    chart_row = db_session.get(Chart, _chart(client)["id"])
    _build_five_days(client, db_session, chart_row.id)
    for cols in (["event_date", "game_id", "dau"], ["event_date", "game_id", "dau", "x"], ["event_date", "dau"]):
        added, removed = column_diff(chart_row, cols)
        assert (not added and not removed) == (incompatible_columns(chart_row, cols) is None)


# ---------------------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------------------


def test_keep_cache_with_no_existing_cache_just_builds(client, db_session, duckdb_path):
    """Nothing to preserve — must behave exactly like an ordinary first build."""
    chart = _chart(client, name="no-cache-yet")
    ctx = _mock_per_day(GAME_DESC, lambda d: [(d, 3, 100)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        body = _bp(client, db_session, chart["id"], from_date="2026-06-01", to_date="2026-06-03", keep_cache=True)
    assert body["status"] == "success"
    assert sorted({r[0] for r in _cached(duckdb_path, chart["id"])}) == [date(2026, 6, d) for d in (1, 2, 3)]


def test_keep_cache_with_force_clears_an_empty_day(client, db_session, duckdb_path):
    """Both ticked: force's "match Redshift" wins over keep's "never delete".

    Documented in the dialog, and asserted here so the precedence can't drift silently.
    """
    chart = _chart(client, name="keep-and-force")
    _build_five_days(client, db_session, chart["id"])
    assert client.put(f"/charts/{chart['id']}", json={"query": Q_THREE_GAMES}).status_code == 200
    ctx = _mock_per_day(GAME_DESC, lambda d: [])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        body = _bp(client, db_session, chart["id"], from_date="2026-06-04", to_date="2026-06-05",
                   keep_cache=True, force=True)
    assert body["status"] == "success"
    days = sorted({r[0] for r in _cached(duckdb_path, chart["id"])})
    assert days == [date(2026, 6, d) for d in (1, 2, 3)], "force should have cleared 4-5, keep should have spared 1-3"


def test_keep_cache_on_a_batched_chart(client, db_session, duckdb_path):
    """Batched mode replaces its whole window anyway; keep_cache must still spare days outside it."""
    q = ("SELECT event_date, game_id, dau FROM t "
         "WHERE event_date BETWEEN DATE '{START_DATE}' AND DATE '{END_DATE}' AND game_id IN (3)")
    chart = _chart(client, query=q, name="batched-keep", cur_date_behavior="batched",
                   cache_strategy="replace", backpop_batch_size=2)
    ctx = _mock_per_day(GAME_DESC, lambda d: [(d, 3, 100)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        _bp(client, db_session, chart["id"], from_date="2026-06-01", to_date="2026-06-04", batch_size=2)
    built = sorted({r[0] for r in _cached(duckdb_path, chart["id"])})
    assert len(built) >= 2

    assert client.put(f"/charts/{chart['id']}", json={"query": q.replace("IN (3)", "IN (3, 8)")}).status_code == 200
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        body = _bp(client, db_session, chart["id"], from_date=str(built[-1]), to_date=str(built[-1]),
                   batch_size=2, keep_cache=True)
    assert body["status"] == "success"
    assert built[0] in {r[0] for r in _cached(duckdb_path, chart["id"])}, "earlier window was dropped"


def test_keep_cache_on_an_unchanged_query_is_just_a_repull(client, db_session, duckdb_path):
    """No hash change => nothing would have been dropped anyway; the range still re-reads."""
    chart = _chart(client, name="unchanged-query")
    _build_five_days(client, db_session, chart["id"])
    ctx = _mock_per_day(GAME_DESC, lambda d: [(d, 3, 777), (d, 4, 888)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx) as p:
        body = _bp(client, db_session, chart["id"], from_date="2026-06-01", to_date="2026-06-02", keep_cache=True)
    assert p.call_count == 2 and body["status"] == "success"
    rows = _cached(duckdb_path, chart["id"])
    assert sorted(r[2] for r in rows if r[0] == date(2026, 6, 1)) == [777, 888]   # re-read
    assert sorted(r[2] for r in rows if r[0] == date(2026, 6, 5)) == [100, 200]   # untouched


def test_keep_cache_refuses_a_chart_with_no_time_column(client, db_session, duckdb_path):
    """Without a time column the per-day delete is impossible, so a re-read would duplicate.

    Measured before the guard: a 2-row cache became 4 rows after one keep_cache run and 6
    after a force run. Double counting is the failure this codebase treats as worse than
    not running, so the run is refused rather than silently doubling a metric.
    """
    r = client.post("/charts", json={
        "name": "no-time-column", "query": "SELECT game_id, dau FROM t WHERE d='{CUR_DATE_HIPHEN}'",
        "cur_date_behavior": "daily", "cache_strategy": "append", "backpop_batch_size": 1})
    cid = r.json()["id"]
    assert r.json()["time_column"] is None

    ctx = _mock_per_day(GAME_DESC, lambda d: [(d, 3, 100)])
    with patch("app.backpop.redshift_conn.connect", return_value=ctx):
        _bp(client, db_session, cid, from_date="2026-06-01", to_date="2026-06-02")
        body = _bp(client, db_session, cid, from_date="2026-06-01", to_date="2026-06-02", keep_cache=True)
    assert body["status"] == "failed"
    assert "time column" in body["error_message"]
    assert body["row_count"] == 0  # refused before doing any work
