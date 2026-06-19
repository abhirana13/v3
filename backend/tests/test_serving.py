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
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(f'"{c}"' for c in cols)
    conn.executemany(
        f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})',
        rows,
    )
    conn.close()


def _make_chart_with_config(client, dims, metrics, name="serve-test"):
    r = client.post(
        "/charts",
        json={
            "name": name,
            "query": "SELECT 1",
            "time_column": "event_date",
            "default_backpop_days": 7,
        },
    )
    chart_id = r.json()["id"]
    client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [{"name": d, "column_name": d} for d in dims],
            "metrics": [{"name": m, "column_name": m} for m in metrics],
        },
    )
    return chart_id


# ---------- Sample dataset ----------
# 2 days × 3 (country, platform) combos = 6 rows
SAMPLE_COLUMNS = [
    ("event_date", "DATE"),
    ("country", "VARCHAR"),
    ("platform", "VARCHAR"),
    ("dau", "BIGINT"),
    ("revenue", "DOUBLE"),
]

SAMPLE_ROWS = [
    (date(2026, 6, 12), "US", "ANDROID", 1700, 970.97),
    (date(2026, 6, 12), "US", "IOS", 800, 600.00),
    (date(2026, 6, 12), "UK", "ANDROID", 200, 50.00),
    (date(2026, 6, 13), "US", "ANDROID", 1650, 950.00),
    (date(2026, 6, 13), "US", "IOS", 820, 610.00),
    (date(2026, 6, 13), "UK", "ANDROID", 195, 48.00),
]


@pytest.fixture
def seeded_chart(client, duckdb_path):
    chart_id = _make_chart_with_config(
        client,
        dims=["country", "platform"],
        metrics=["dau", "revenue"],
    )
    _seed(duckdb_path, chart_id, SAMPLE_COLUMNS, SAMPLE_ROWS)
    return chart_id


# ---------- Tests ----------

def test_serve_default_returns_full_grain(client, seeded_chart):
    r = client.get(f"/charts/{seeded_chart}/data")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row_count"] == 6
    assert body["dimensions"] == ["country", "platform"]
    assert body["metrics"] == ["dau", "revenue"]
    # First row: 2026-06-12 / UK / ANDROID (lex order)
    assert body["rows"][0]["country"] == "UK"
    assert body["rows"][0]["platform"] == "ANDROID"


def test_serve_groups_by_subset_sums_across_others(client, seeded_chart):
    """Group by country only → platform collapsed, dau/revenue summed across platforms."""
    r = client.get(f"/charts/{seeded_chart}/data", params={"group_by": ["country"]})
    body = r.json()
    rows = body["rows"]
    by_date_country = {(row["event_date"], row["country"]): row for row in rows}
    # 2026-06-12 US should sum across platforms: 1700 + 800 = 2500
    us_12 = by_date_country[("2026-06-12", "US")]
    assert us_12["dau"] == 2500
    assert us_12["revenue"] == pytest.approx(1570.97)
    # 2026-06-12 UK: only ANDROID, 200
    uk_12 = by_date_country[("2026-06-12", "UK")]
    assert uk_12["dau"] == 200


def test_serve_date_range_filter(client, seeded_chart):
    r = client.get(
        f"/charts/{seeded_chart}/data",
        params={"from_date": "2026-06-13", "to_date": "2026-06-13"},
    )
    body = r.json()
    assert body["row_count"] == 3
    assert all(row["event_date"] == "2026-06-13" for row in body["rows"])


def test_serve_dim_filter_via_json(client, seeded_chart):
    import json
    r = client.get(
        f"/charts/{seeded_chart}/data",
        params={"filters": json.dumps({"country": ["UK"]})},
    )
    body = r.json()
    assert all(row["country"] == "UK" for row in body["rows"])
    assert body["row_count"] == 2  # 2 days × 1 platform


def test_serve_multi_value_filter(client, seeded_chart):
    import json
    r = client.get(
        f"/charts/{seeded_chart}/data",
        params={"filters": json.dumps({"platform": ["IOS", "ANDROID"]})},
    )
    body = r.json()
    assert body["row_count"] == 6


def test_serve_granularity_week_collapses_dates(client, seeded_chart):
    """Both sample dates are in the same week (2026-06-08 starts Monday). Group by week → 3 rows."""
    r = client.get(
        f"/charts/{seeded_chart}/data",
        params={"granularity": "week", "group_by": ["country", "platform"]},
    )
    body = r.json()
    assert body["row_count"] == 3  # 3 (country, platform) combos, one week
    # All rows should share the same event_date (week-start)
    weeks = {row["event_date"] for row in body["rows"]}
    assert len(weeks) == 1


def test_serve_granularity_month_collapses_dates(client, seeded_chart):
    r = client.get(
        f"/charts/{seeded_chart}/data",
        params={"granularity": "month", "group_by": ["country"]},
    )
    body = r.json()
    # 2 countries × 1 month = 2 rows
    assert body["row_count"] == 2
    months = {row["event_date"] for row in body["rows"]}
    assert months == {"2026-06-01"}


def test_serve_hide_zero(client, duckdb_path):
    chart_id = _make_chart_with_config(
        client,
        dims=["country"],
        metrics=["dau"],
        name="hide-zero-test",
    )
    _seed(
        duckdb_path,
        chart_id,
        [("event_date", "DATE"), ("country", "VARCHAR"), ("dau", "BIGINT")],
        [
            (date(2026, 6, 12), "US", 100),
            (date(2026, 6, 12), "UK", 0),  # zero — should be hidden
            (date(2026, 6, 13), "US", 0),  # zero — should be hidden
            (date(2026, 6, 13), "UK", 50),
        ],
    )
    r = client.get(f"/charts/{chart_id}/data", params={"hide_zero": "true"})
    body = r.json()
    assert body["row_count"] == 2
    pairs = {(row["event_date"], row["country"]) for row in body["rows"]}
    assert pairs == {("2026-06-12", "US"), ("2026-06-13", "UK")}


def test_serve_select_metrics_subset(client, seeded_chart):
    r = client.get(f"/charts/{seeded_chart}/data", params={"metrics": ["dau"]})
    body = r.json()
    assert "dau" in body["metrics"]
    assert "revenue" not in body["metrics"]
    for row in body["rows"]:
        assert "dau" in row
        assert "revenue" not in row


def test_serve_no_dim_grouping_sums_across_all(client, duckdb_path):
    """Explicit empty group_by → time-only grouping, metrics summed across all dims."""
    chart_id = _make_chart_with_config(
        client,
        dims=["country", "platform"],
        metrics=["dau"],
        name="time-only",
    )
    _seed(
        duckdb_path,
        chart_id,
        [("event_date", "DATE"), ("country", "VARCHAR"), ("platform", "VARCHAR"), ("dau", "BIGINT")],
        [
            (date(2026, 6, 12), "US", "ANDROID", 100),
            (date(2026, 6, 12), "UK", "ANDROID", 50),
            (date(2026, 6, 12), "US", "IOS", 30),
            (date(2026, 6, 13), "US", "ANDROID", 200),
        ],
    )
    r = client.get(f"/charts/{chart_id}/data?group_by=&metrics=dau")
    body = r.json()
    assert body["row_count"] == 2
    by_date = {row["event_date"]: row for row in body["rows"]}
    assert by_date["2026-06-12"]["dau"] == 180
    assert by_date["2026-06-13"]["dau"] == 200


def test_serve_unknown_chart_404(client):
    r = client.get("/charts/9999/data")
    assert r.status_code == 404


def test_serve_unknown_dimension_400(client, seeded_chart):
    r = client.get(f"/charts/{seeded_chart}/data", params={"group_by": ["bogus"]})
    assert r.status_code == 400
    assert "bogus" in r.json()["detail"]


def test_serve_unknown_metric_400(client, seeded_chart):
    r = client.get(f"/charts/{seeded_chart}/data", params={"metrics": ["bogus"]})
    assert r.status_code == 400


def test_serve_invalid_filter_json_400(client, seeded_chart):
    r = client.get(f"/charts/{seeded_chart}/data", params={"filters": "{not-json"})
    assert r.status_code == 400


def test_serve_filter_dim_unknown_400(client, seeded_chart):
    import json
    r = client.get(
        f"/charts/{seeded_chart}/data",
        params={"filters": json.dumps({"bogus": ["x"]})},
    )
    assert r.status_code == 400


def test_serve_chart_no_time_column_400(client, duckdb_path):
    r = client.post("/charts", json={"name": "no-time", "query": "SELECT 1"})
    chart_id = r.json()["id"]
    r = client.get(f"/charts/{chart_id}/data")
    assert r.status_code == 400
    assert "time_column" in r.json()["detail"]


def test_serve_from_after_to_422(client, seeded_chart):
    r = client.get(
        f"/charts/{seeded_chart}/data",
        params={"from_date": "2026-06-13", "to_date": "2026-06-12"},
    )
    assert r.status_code == 422


def test_serve_no_data_yet_returns_empty(client, duckdb_path):
    chart_id = _make_chart_with_config(
        client, dims=["country"], metrics=["dau"], name="empty-test"
    )
    # Do NOT seed DuckDB — table doesn't exist
    r = client.get(f"/charts/{chart_id}/data")
    assert r.status_code == 200
    assert r.json()["row_count"] == 0
    assert r.json()["rows"] == []
