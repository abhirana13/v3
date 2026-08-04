"""x-axis pivot: plot a *dimension* on the x-axis instead of the time bucket.

Storage stays time-anchored (charts mature cheaply); the pivot is a display/serving
concern that reuses the SAME aggregation + independent-metric dedup, just keyed on the
chosen dimension with time collapsed to a filter. These prove:
  1. correct aggregation over the whole range, grouped by a dimension,
  2. the event_date-anchored retention shape reads as a clean cohort view when pivoted,
  3. the independent-metric (double-count) protection is preserved through the pivot,
  4. the chart's configured default x_axis is applied, and time-axis behaviour is unchanged.
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


def _seed(duckdb_path, chart_id, columns_with_types, rows):
    conn = duckdb.connect(duckdb_path)
    table = table_name(chart_id)
    cols_def = ", ".join(f'"{c}" {t}' for c, t in columns_with_types)
    conn.execute(f'CREATE TABLE "{table}" ({cols_def})')
    cols = [c for c, _ in columns_with_types]
    ph = ", ".join("?" * len(cols))
    col_list = ", ".join(f'"{c}"' for c in cols)
    conn.executemany(f'INSERT INTO "{table}" ({col_list}) VALUES ({ph})', rows)
    conn.close()


def _make_chart(client, dims, metrics_config, name, x_axis=None):
    body = {"name": name, "query": "SELECT 1", "time_column": "event_date"}
    if x_axis:
        body["x_axis"] = x_axis
    cid = client.post("/charts", json=body).json()["id"]
    r = client.put(
        f"/charts/{cid}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [{"name": d, "column_name": d} for d in dims],
            "metrics": [
                {
                    "name": m["name"],
                    "column_name": m["column_name"],
                    "independent_dimensions": m.get("independent_dimensions", []),
                }
                for m in metrics_config
            ],
        },
    )
    assert r.status_code == 200, r.text
    return cid


# ---------- 1) pivot a normal chart by a dimension: aggregate over the whole range ----------
def test_pivot_by_dimension_sums_over_time(client, duckdb_path):
    cid = _make_chart(client, ["country", "platform"], [{"name": "dau", "column_name": "dau"}], "pivot-basic")
    _seed(
        duckdb_path, cid,
        [("event_date", "DATE"), ("country", "VARCHAR"), ("platform", "VARCHAR"), ("dau", "BIGINT")],
        [
            (date(2026, 6, 12), "US", "ANDROID", 100),
            (date(2026, 6, 12), "US", "IOS", 30),
            (date(2026, 6, 12), "UK", "ANDROID", 50),
            (date(2026, 6, 13), "US", "ANDROID", 200),
        ],
    )
    r = client.get(f"/charts/{cid}/data", params={"x_axis": "country", "group_by": ""})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["x_axis"] == "country"
    rows = {row["country"]: row for row in body["rows"]}
    assert rows["US"]["dau"] == 330  # 100 + 30 + 200 across days+platforms
    assert rows["UK"]["dau"] == 50
    assert "event_date" not in body["rows"][0]  # pivot rows carry no time column


# ---------- 2) event_date-anchored retention reads as a cohort view when pivoted ----------
def test_pivot_retention_cohort_shape(client, duckdb_path):
    cid = _make_chart(
        client,
        ["install_date", "days_since_install"],
        [{"name": "installs", "column_name": "installs"}, {"name": "retained_users", "column_name": "retained_users"}],
        "pivot-retention",
    )
    # event_date-anchored rows: installs baseline at dsi=0 (keyed on install day),
    # returns at dsi>=1 (keyed on the activity day).
    _seed(
        duckdb_path, cid,
        [("event_date", "DATE"), ("install_date", "DATE"), ("days_since_install", "BIGINT"),
         ("installs", "BIGINT"), ("retained_users", "BIGINT")],
        [
            (date(2026, 6, 1), date(2026, 6, 1), 0, 10, 0),  # cohort 06-01, size 10
            (date(2026, 6, 2), date(2026, 6, 1), 1, 0, 6),   #   D1
            (date(2026, 6, 4), date(2026, 6, 1), 3, 0, 4),   #   D3
            (date(2026, 6, 2), date(2026, 6, 2), 0, 20, 0),  # cohort 06-02, size 20
            (date(2026, 6, 3), date(2026, 6, 2), 1, 0, 12),  #   D1
        ],
    )
    body = client.get(
        f"/charts/{cid}/data", params={"x_axis": "install_date", "group_by": "days_since_install"}
    ).json()
    assert body["x_axis"] == "install_date"
    cell = {(row["install_date"], row["days_since_install"]): row for row in body["rows"]}
    assert cell[("2026-06-01", 0)]["installs"] == 10
    assert cell[("2026-06-01", 0)]["retained_users"] == 0
    assert cell[("2026-06-01", 1)]["retained_users"] == 6
    assert cell[("2026-06-01", 3)]["retained_users"] == 4
    assert cell[("2026-06-02", 0)]["installs"] == 20
    assert cell[("2026-06-02", 1)]["retained_users"] == 12

    # per-cohort totals (no series): installs counted once, retained = sum of all returns
    tot = {row["install_date"]: row
           for row in client.get(f"/charts/{cid}/data",
                                 params={"x_axis": "install_date", "group_by": ""}).json()["rows"]}
    assert tot["2026-06-01"]["installs"] == 10
    assert tot["2026-06-01"]["retained_users"] == 10  # 6 + 4
    assert tot["2026-06-02"]["installs"] == 20
    assert tot["2026-06-02"]["retained_users"] == 12


# ---------- 3) independent-metric dedup survives the pivot ----------
INDEP_COLUMNS = [("event_date", "DATE"), ("source", "VARCHAR"), ("country", "VARCHAR"), ("dau", "BIGINT")]
INDEP_DATA = [
    (date(2026, 6, 12), "A", "US", 100), (date(2026, 6, 12), "B", "US", 100), (date(2026, 6, 12), "C", "US", 100),
    (date(2026, 6, 12), "A", "UK", 50), (date(2026, 6, 12), "B", "UK", 50), (date(2026, 6, 12), "C", "UK", 50),
    (date(2026, 6, 13), "A", "US", 120), (date(2026, 6, 13), "B", "US", 120), (date(2026, 6, 13), "C", "US", 120),
]


def test_pivot_preserves_independent_dedup(client, duckdb_path):
    cid = _make_chart(client, ["source", "country"],
                      [{"name": "dau", "column_name": "dau", "independent_dimensions": ["source"]}], "pivot-indep")
    _seed(duckdb_path, cid, INDEP_COLUMNS, INDEP_DATA)
    rows = {row["country"]: row
            for row in client.get(f"/charts/{cid}/data",
                                  params={"x_axis": "country", "group_by": ""}).json()["rows"]}
    assert rows["US"]["dau"] == 220  # 100 + 120 across days, deduped across source (naive x3 = 660)
    assert rows["UK"]["dau"] == 50


def test_pivot_x_axis_independent_repeats_value(client, duckdb_path):
    """Pivot BY the dimension a metric is independent of → the value repeats (doesn't split)."""
    cid = _make_chart(client, ["source", "country"],
                      [{"name": "dau", "column_name": "dau", "independent_dimensions": ["source"]}], "pivot-indep-x")
    _seed(duckdb_path, cid, INDEP_COLUMNS, INDEP_DATA)
    cell = {(row["source"], row["country"]): row
            for row in client.get(f"/charts/{cid}/data",
                                  params={"x_axis": "source", "group_by": "country"}).json()["rows"]}
    for s in ("A", "B", "C"):
        assert cell[(s, "US")]["dau"] == 220
        assert cell[(s, "UK")]["dau"] == 50


# ---------- 4) chart default x_axis + time-axis regression ----------
def test_chart_default_x_axis_applied(client, duckdb_path):
    cid = _make_chart(client, ["country"], [{"name": "dau", "column_name": "dau"}], "pivot-default", x_axis="country")
    _seed(duckdb_path, cid, [("event_date", "DATE"), ("country", "VARCHAR"), ("dau", "BIGINT")],
          [(date(2026, 6, 12), "US", 100), (date(2026, 6, 13), "US", 50), (date(2026, 6, 12), "UK", 10)])
    body = client.get(f"/charts/{cid}/data").json()  # no x_axis param → chart default
    assert body["x_axis"] == "country"
    rows = {row["country"]: row for row in body["rows"]}
    assert rows["US"]["dau"] == 150
    assert rows["UK"]["dau"] == 10


def test_time_axis_default_unchanged(client, duckdb_path):
    cid = _make_chart(client, ["country"], [{"name": "dau", "column_name": "dau"}], "pivot-none")
    _seed(duckdb_path, cid, [("event_date", "DATE"), ("country", "VARCHAR"), ("dau", "BIGINT")],
          [(date(2026, 6, 12), "US", 100), (date(2026, 6, 13), "US", 50)])
    body = client.get(f"/charts/{cid}/data", params={"group_by": ""}).json()
    assert body["x_axis"] is None
    assert {row["event_date"] for row in body["rows"]} == {"2026-06-12", "2026-06-13"}


def test_pivot_unknown_x_axis_400(client, duckdb_path):
    cid = _make_chart(client, ["country"], [{"name": "dau", "column_name": "dau"}], "pivot-bad")
    _seed(duckdb_path, cid, [("event_date", "DATE"), ("country", "VARCHAR"), ("dau", "BIGINT")],
          [(date(2026, 6, 12), "US", 100)])
    r = client.get(f"/charts/{cid}/data", params={"x_axis": "bogus"})
    assert r.status_code == 400
    assert "bogus" in r.json()["detail"]


def test_pivot_time_axis_explicit_is_time_series(client, duckdb_path):
    """Passing the time column name as x_axis is treated as the normal time series."""
    cid = _make_chart(client, ["country"], [{"name": "dau", "column_name": "dau"}], "pivot-explicit-time")
    _seed(duckdb_path, cid, [("event_date", "DATE"), ("country", "VARCHAR"), ("dau", "BIGINT")],
          [(date(2026, 6, 12), "US", 100), (date(2026, 6, 13), "US", 50)])
    body = client.get(f"/charts/{cid}/data", params={"x_axis": "event_date", "group_by": ""}).json()
    assert body["x_axis"] is None  # normalized to time series
    assert {row["event_date"] for row in body["rows"]} == {"2026-06-12", "2026-06-13"}
