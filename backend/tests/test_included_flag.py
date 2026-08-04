"""Excluding a dimension/metric HIDES it, it must never delete it.

Two real bugs this pins down (2026-08-04):
  1. The config page dropped excluded columns from the PUT payload, and `replace()`
     clears-then-re-adds — so toggling Include off deleted the column outright. It then
     vanished from the config table too, unrecoverable without re-introspecting.
  2. That deletion left `chart.x_axis` pointing at a dimension that no longer existed, so
     every /data call 400'd with "unknown x_axis dimension" — the chart couldn't be opened
     at all, including to undo the change.
"""

from datetime import date

import duckdb
import pytest

from app.backpop.duckdb_writer import table_name


@pytest.fixture
def duckdb_path(tmp_path, monkeypatch):
    path = str(tmp_path / "test.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


def _seed(duckdb_path, chart_id):
    conn = duckdb.connect(duckdb_path)
    t = table_name(chart_id)
    conn.execute(f'CREATE TABLE "{t}" (event_date DATE, country VARCHAR, cohort VARCHAR, dau BIGINT)')
    conn.executemany(
        f'INSERT INTO "{t}" VALUES (?, ?, ?, ?)',
        [
            (date(2026, 6, 12), "US", "D0", 100),
            (date(2026, 6, 12), "UK", "D1", 50),
            (date(2026, 6, 13), "US", "D0", 120),
        ],
    )
    conn.close()


def _chart(client, x_axis=None):
    body = {"name": "included-test", "query": "SELECT 1", "time_column": "event_date"}
    if x_axis:
        body["x_axis"] = x_axis
    return client.post("/charts", json=body).json()["id"]


def _put(client, cid, dims, metrics):
    r = client.put(f"/charts/{cid}/dims-metrics", json={
        "time_column": "event_date", "dimensions": dims, "metrics": metrics,
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_excluded_dimension_is_kept_not_deleted(client, duckdb_path):
    cid = _chart(client)
    _seed(duckdb_path, cid)
    _put(client, cid,
         [{"name": "country", "column_name": "country"},
          {"name": "cohort", "column_name": "cohort", "included": False}],
         [{"name": "dau", "column_name": "dau"}])

    dims = {d["name"]: d for d in client.get(f"/charts/{cid}/dims-metrics").json()["dimensions"]}
    assert "cohort" in dims, "excluded dimension must still be configured (re-includable)"
    assert dims["cohort"]["included"] is False
    assert dims["country"]["included"] is True

    # and it can be toggled back on
    _put(client, cid,
         [{"name": "country", "column_name": "country"},
          {"name": "cohort", "column_name": "cohort", "included": True}],
         [{"name": "dau", "column_name": "dau"}])
    dims = {d["name"]: d for d in client.get(f"/charts/{cid}/dims-metrics").json()["dimensions"]}
    assert dims["cohort"]["included"] is True


def test_excluded_metric_is_kept_not_deleted(client, duckdb_path):
    cid = _chart(client)
    _seed(duckdb_path, cid)
    _put(client, cid, [{"name": "country", "column_name": "country"}],
         [{"name": "dau", "column_name": "dau"},
          {"name": "hidden_dau", "column_name": "dau", "included": False}])
    mets = {m["name"]: m for m in client.get(f"/charts/{cid}/dims-metrics").json()["metrics"]}
    assert set(mets) == {"dau", "hidden_dau"}
    assert mets["hidden_dau"]["included"] is False


def test_excluded_dimension_still_works_as_the_x_axis(client, duckdb_path):
    """Excluding only removes a dimension's filter chip — it stays valid as the x-axis.

    This is the whole point for a high-cardinality date dimension (install_date): you never
    want to pick cohorts from a dropdown (the date picker drives the range), but you do want
    to plot retention against them. Excluding it must NOT silently drop the chart back to a
    time series.
    """
    cid = _chart(client, x_axis="cohort")
    _seed(duckdb_path, cid)
    _put(client, cid,
         [{"name": "country", "column_name": "country"},
          {"name": "cohort", "column_name": "cohort", "included": False}],
         [{"name": "dau", "column_name": "dau"}])

    r = client.get(f"/charts/{cid}/data", params={"group_by": "", "metrics": "dau"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["x_axis"] == "cohort"                   # still pivoted on the excluded dim
    rows = {row["cohort"]: row["dau"] for row in body["rows"]}
    assert rows == {"D0": 220, "D1": 50}                # 100 + 120 across days, and 50
    assert "event_date" not in body["rows"][0]


def test_default_x_axis_falls_back_when_its_dimension_is_gone(client, duckdb_path):
    """Same protection when a query edit drops the dimension entirely."""
    cid = _chart(client, x_axis="cohort")
    _seed(duckdb_path, cid)
    _put(client, cid, [{"name": "country", "column_name": "country"}],
         [{"name": "dau", "column_name": "dau"}])
    r = client.get(f"/charts/{cid}/data", params={"group_by": "", "metrics": "dau"})
    assert r.status_code == 200, r.text
    assert r.json()["x_axis"] is None


def test_explicit_unknown_x_axis_still_400s(client, duckdb_path):
    """The tolerant fallback applies to the chart's DEFAULT only — an explicitly requested
    unknown dimension is a caller bug and must still be reported."""
    cid = _chart(client)
    _seed(duckdb_path, cid)
    _put(client, cid, [{"name": "country", "column_name": "country"}],
         [{"name": "dau", "column_name": "dau"}])
    r = client.get(f"/charts/{cid}/data", params={"x_axis": "nope", "metrics": "dau"})
    assert r.status_code == 400
    assert "nope" in r.json()["detail"]


def test_included_defaults_true_for_payloads_that_omit_it(client, duckdb_path):
    """Older/API callers that don't send `included` keep every column visible."""
    cid = _chart(client)
    _seed(duckdb_path, cid)
    _put(client, cid, [{"name": "country", "column_name": "country"}],
         [{"name": "dau", "column_name": "dau"}])
    dm = client.get(f"/charts/{cid}/dims-metrics").json()
    assert all(d["included"] is True for d in dm["dimensions"])
    assert all(m["included"] is True for m in dm["metrics"])
