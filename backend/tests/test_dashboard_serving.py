"""Phase 2 — chart-widget serving: one serve_data() call over the source chart's
cache with the widget's config (metrics/group_by/filters/offset) merged with the
dashboard's global filter state and date window.

Key invariant proven here: a widget returns EXACTLY what its source chart's own
/data endpoint returns for the equivalent request (same cache, same logic)."""

import json
from datetime import date

import duckdb
import pytest

from app.backpop.duckdb_writer import table_name


@pytest.fixture
def duckdb_path(tmp_path, monkeypatch):
    path = str(tmp_path / "test.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


@pytest.fixture
def fake_today(monkeypatch):
    """Pin 'today' so offset-cap math is deterministic: 2026-06-16."""
    monkeypatch.setattr("app.dashboards.serving._today", lambda: date(2026, 6, 16))
    return date(2026, 6, 16)


def _seed(duckdb_path, chart_id, rows):
    conn = duckdb.connect(duckdb_path)
    table = table_name(chart_id)
    conn.execute(
        f'CREATE TABLE "{table}" '
        "(event_date DATE, country VARCHAR, platform VARCHAR, dau BIGINT, revenue DOUBLE)"
    )
    conn.executemany(f'INSERT INTO "{table}" VALUES (?, ?, ?, ?, ?)', rows)
    conn.close()


SEED_ROWS = [
    (date(2026, 6, 12), "US", "ANDROID", 1700, 970.97),
    (date(2026, 6, 12), "US", "IOS", 800, 600.00),
    (date(2026, 6, 12), "UK", "ANDROID", 200, 50.00),
    (date(2026, 6, 13), "US", "ANDROID", 1650, 950.00),
    (date(2026, 6, 13), "US", "IOS", 820, 610.00),
    (date(2026, 6, 13), "UK", "ANDROID", 195, 48.00),
    (date(2026, 6, 14), "US", "ANDROID", 1600, 940.00),
    (date(2026, 6, 14), "UK", "ANDROID", 190, 47.00),
    (date(2026, 6, 15), "US", "ANDROID", 1580, 930.00),
    (date(2026, 6, 15), "US", "IOS", 810, 605.00),
]


def make_chart(client, name="widget-src"):
    r = client.post(
        "/charts", json={"name": name, "query": "SELECT 1", "time_column": "event_date"}
    )
    chart_id = r.json()["id"]
    client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [
                {"name": "country", "column_name": "country"},
                {"name": "platform", "column_name": "platform"},
            ],
            "metrics": [
                {"name": "dau", "column_name": "dau"},
                {"name": "revenue", "column_name": "revenue"},
            ],
        },
    )
    return chart_id


@pytest.fixture
def seeded_chart(client, duckdb_path):
    chart_id = make_chart(client)
    _seed(duckdb_path, chart_id, SEED_ROWS)
    return chart_id


@pytest.fixture
def dash(client):
    r = client.post("/dashboards", json={"name": "Serve Dash"})
    return r.json()


def add_widget(client, dash, chart_id, config, wtype="chart", name="w"):
    tab_id = client.get(f"/dashboards/{dash['id']}").json()["tabs"][0]["id"]
    r = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets",
        json={
            "type": wtype,
            "source_chart_id": chart_id,
            "name": name,
            "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
            "config": config,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def widget_data(client, dash, widget, **params):
    if "filters" in params and isinstance(params["filters"], dict):
        params["filters"] = json.dumps(params["filters"])
    r = client.get(f"/dashboards/{dash['id']}/widgets/{widget['id']}/data", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- config → request mapping ----------

def test_widget_metrics_subset(client, seeded_chart, dash, fake_today):
    w = add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}]})
    body = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-13")
    assert body["metrics"] == ["revenue"]
    assert body["widget_id"] == w["id"]
    assert all("dau" not in row for row in body["rows"])
    # group_by defaults to [] -> time-only rows, metrics summed across all dims
    assert body["row_count"] == 2
    by_date = {r["event_date"]: r for r in body["rows"]}
    assert by_date["2026-06-12"]["revenue"] == pytest.approx(970.97 + 600.00 + 50.00)


def test_widget_group_by(client, seeded_chart, dash, fake_today):
    w = add_widget(
        client, dash, seeded_chart,
        {"metrics": [{"name": "revenue"}], "group_by": ["country"]},
    )
    body = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-12")
    assert body["dimensions"] == ["country"]
    by_country = {r["country"]: r for r in body["rows"]}
    # platforms collapsed (summed) within each country
    assert by_country["US"]["revenue"] == pytest.approx(970.97 + 600.00)
    assert by_country["UK"]["revenue"] == pytest.approx(50.00)


def test_widget_own_filters(client, seeded_chart, dash, fake_today):
    w = add_widget(
        client, dash, seeded_chart,
        {"metrics": [{"name": "revenue"}], "filters": {"platform": ["ANDROID"]}},
    )
    body = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-12")
    assert body["row_count"] == 1
    assert body["rows"][0]["revenue"] == pytest.approx(970.97 + 50.00)  # US+UK ANDROID


# ---------- global cascade ----------

def test_global_filters_apply(client, seeded_chart, dash, fake_today):
    w = add_widget(
        client, dash, seeded_chart,
        {"metrics": [{"name": "revenue"}], "group_by": ["country"]},
    )
    body = widget_data(
        client, dash, w,
        from_date="2026-06-12", to_date="2026-06-13", filters={"country": ["US"]},
    )
    assert {r["country"] for r in body["rows"]} == {"US"}


def test_same_dim_intersection_narrows(client, seeded_chart, dash, fake_today):
    """Global picks [US, UK]; the widget's own filter [UK, DE] can only narrow ->
    effective selection is the intersection {UK}."""
    w = add_widget(
        client, dash, seeded_chart,
        {"metrics": [{"name": "revenue"}], "group_by": ["country"],
         "filters": {"country": ["UK", "DE"]}},
    )
    body = widget_data(
        client, dash, w,
        from_date="2026-06-12", to_date="2026-06-13", filters={"country": ["US", "UK"]},
    )
    assert {r["country"] for r in body["rows"]} == {"UK"}


def test_empty_intersection_returns_no_rows(client, seeded_chart, dash, fake_today):
    """Disjoint global vs widget selections must yield ZERO rows — not silently
    widen back to everything."""
    w = add_widget(
        client, dash, seeded_chart,
        {"metrics": [{"name": "revenue"}], "filters": {"country": ["UK"]}},
    )
    body = widget_data(
        client, dash, w,
        from_date="2026-06-12", to_date="2026-06-13", filters={"country": ["US"]},
    )
    assert body["row_count"] == 0
    assert body["rows"] == []


def _pivot_chart(client, duckdb_path):
    """A chart whose saved x_axis is a DIMENSION, so widgets on it should pivot too."""
    r = client.post(
        "/charts", json={"name": "pivot-src", "query": "SELECT 1", "time_column": "event_date"}
    )
    chart_id = r.json()["id"]
    client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [{"name": "bucket", "column_name": "bucket"}],
            "metrics": [{"name": "installs", "column_name": "installs"}],
        },
    )
    client.put(f"/charts/{chart_id}", json={"x_axis": "bucket"})
    conn = duckdb.connect(duckdb_path)
    t = table_name(chart_id)
    conn.execute(f'CREATE TABLE "{t}" (event_date DATE, bucket VARCHAR, installs BIGINT)')
    conn.executemany(
        f'INSERT INTO "{t}" VALUES (?, ?, ?)',
        [(date(2026, 6, 13), "D0", 10), (date(2026, 6, 13), "D1", 5),
         (date(2026, 6, 14), "D0", 20), (date(2026, 6, 14), "D1", 7)],
    )
    conn.close()
    return chart_id


def test_widget_inherits_the_charts_pivot_axis(client, duckdb_path, dash, fake_today):
    """A widget on a PIVOTED chart pivots too, instead of flattening to a time series.

    ChartWidgetConfig.x_axis was Literal["time"], hardcoded, and serving never passed an x_axis
    at all — so the same chart returned cohort buckets on its own page and dates in a widget,
    while the module docstring claimed a widget always matches its source chart.
    """
    cid = _pivot_chart(client, duckdb_path)
    w = add_widget(client, dash, cid, {"metrics": [{"name": "installs"}]}, name="w-pivot")
    body = widget_data(client, dash, w, from_date="2026-06-13", to_date="2026-06-14")

    assert body["x_axis"] == "bucket"
    assert body["x_axis_column"] == "bucket"
    # keyed on the cohort bucket, and time has collapsed: 2 rows, not 2 dates x 2 buckets
    assert {r["bucket"] for r in body["rows"]} == {"D0", "D1"}
    assert body["row_count"] == 2
    # installs summed across both days per bucket
    assert {r["bucket"]: r["installs"] for r in body["rows"]} == {"D0": 30, "D1": 12}


def test_widget_can_force_a_time_series_on_a_pivoted_chart(client, duckdb_path, dash, fake_today):
    """Naming the chart's time column overrides an inherited pivot."""
    cid = _pivot_chart(client, duckdb_path)
    w = add_widget(
        client, dash, cid,
        {"metrics": [{"name": "installs"}], "x_axis": "event_date"}, name="w-forced-time",
    )
    body = widget_data(client, dash, w, from_date="2026-06-13", to_date="2026-06-14")
    assert body["x_axis"] is None
    assert body["x_axis_column"] is None
    assert {str(r["event_date"]) for r in body["rows"]} == {"2026-06-13", "2026-06-14"}


def test_legacy_time_placeholder_reads_as_inherit(client, duckdb_path, dash, fake_today):
    """Stored configs say x_axis="time" — the only value the old Literal allowed.

    It was never an expressed preference, so it must read as inherit; that is what lets existing
    widgets pick up their chart's axis without a data migration.
    """
    cid = _pivot_chart(client, duckdb_path)
    w = add_widget(
        client, dash, cid,
        {"metrics": [{"name": "installs"}], "x_axis": "time"}, name="w-legacy",
    )
    body = widget_data(client, dash, w, from_date="2026-06-13", to_date="2026-06-14")
    assert body["x_axis"] == "bucket", "legacy 'time' should inherit, not force a time series"


def test_widget_x_axis_must_exist_on_the_chart(client, duckdb_path, dash, fake_today):
    """A bogus x_axis is rejected at save time rather than silently ignored.

    chart 17 shipped with x_axis='installs' (a METRIC name); the resolver's guard turned that
    into a plain time series and nobody noticed for weeks.
    """
    cid = _pivot_chart(client, duckdb_path)
    tab_id = client.get(f"/dashboards/{dash['id']}").json()["tabs"][0]["id"]
    r = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets",
        json={"type": "chart", "source_chart_id": cid, "name": "bad",
              "layout": {"x": 0, "y": 0, "w": 4, "h": 3},
              # 'installs' is a METRIC on this chart, not a dimension
              "config": {"metrics": [{"name": "installs"}], "x_axis": "installs"}},
    )
    assert r.status_code == 400, r.text
    assert "x_axis" in r.text


def test_global_offset_replaces_dashboard_default(client, seeded_chart, dash, fake_today):
    """The viewer's recency control REPLACES default_end_offset_days, so it can loosen too.

    First attempt at this capped `to_date` client-side instead, which could only ever pull the
    window earlier: resolve_window still clamped to today - default_offset, so choosing a
    SMALLER offset than the dashboard's default changed nothing and the control looked dead.
    fake_today is 2026-06-16 and the dash fixture's default offset is 2 (=> 2026-06-14).
    """
    w = add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}]})

    default_window = widget_data(client, dash, w)
    assert default_window["to_date"] == "2026-06-14"  # today - 2, the dashboard default

    # LOOSER than the default — the case the client-side cap could not express
    assert widget_data(client, dash, w, offset_days=0)["to_date"] == "2026-06-16"
    # and tighter still works
    assert widget_data(client, dash, w, offset_days=7)["to_date"] == "2026-06-09"


def test_widget_own_offset_beats_the_global_control(client, seeded_chart, dash, fake_today):
    """A widget's own offset_days is a deliberate per-widget setting and still wins."""
    w = add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}], "offset_days": 5})
    # global says 0 (today), the widget says 5 -> the widget's own value holds
    assert widget_data(client, dash, w, offset_days=0)["to_date"] == "2026-06-11"


def test_explicitly_empty_global_filter_returns_no_rows(client, seeded_chart, dash, fake_today):
    """The filter bar's None button: an EXPLICITLY empty global selection means no rows.

    This used to be indistinguishable from All — `if values and dim in servable` skipped any
    falsy list, so clearing a chip silently widened the selection back to everything and a
    "None" control could not exist. An untouched chip omits its key entirely (see
    chipsToFilters), so an empty list only ever arrives when the user cleared it on purpose.
    """
    w = add_widget(
        client, dash, seeded_chart,
        {"metrics": [{"name": "revenue"}], "group_by": ["country"]},
    )
    none_selected = widget_data(
        client, dash, w,
        from_date="2026-06-12", to_date="2026-06-13", filters={"country": []},
    )
    assert none_selected["row_count"] == 0
    assert none_selected["rows"] == []

    # ... and omitting the key entirely still means All, so None and All stay distinguishable
    all_values = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-13")
    assert all_values["row_count"] > 0


def test_empty_global_filter_on_missing_dim_does_not_blank_widget(client, seeded_chart, dash, fake_today):
    """A None'd chip for a dimension this chart lacks must NOT empty the widget.

    Same applicability rule as every other global filter: a chip that doesn't bind here is not
    this widget's constraint. Getting this wrong would make one cleared chip blank every widget
    on a mixed-chart dashboard.
    """
    w = add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}]})
    body = widget_data(
        client, dash, w,
        from_date="2026-06-12", to_date="2026-06-13", filters={"gid": []},
    )
    without = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-13")
    assert body["rows"] == without["rows"]


def test_global_filter_on_missing_dim_skipped(client, seeded_chart, dash, fake_today):
    """A global chip for a dimension this source chart doesn't have (gid) simply
    doesn't bind to this widget — the widget still returns its data."""
    w = add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}]})
    with_gid = widget_data(
        client, dash, w,
        from_date="2026-06-12", to_date="2026-06-13", filters={"gid": ["wca_ios"]},
    )
    without = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-13")
    assert with_gid["rows"] == without["rows"]


# ---------- date window + offset ----------

def test_offset_caps_end_date(client, seeded_chart, dash, fake_today):
    """today=2026-06-16, dashboard default offset=2 -> cap 06-14: a request through
    06-20 is clamped and 06-15 data is excluded. offset_days=0 lifts the cap."""
    w = add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}]})
    body = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-20")
    assert body["to_date"] == "2026-06-14"
    assert {r["event_date"] for r in body["rows"]} == {"2026-06-12", "2026-06-13", "2026-06-14"}

    w0 = add_widget(
        client, dash, seeded_chart,
        {"metrics": [{"name": "revenue"}], "offset_days": 0}, name="no-offset",
    )
    body = widget_data(client, dash, w0, from_date="2026-06-12", to_date="2026-06-20")
    assert body["to_date"] == "2026-06-16"
    assert "2026-06-15" in {r["event_date"] for r in body["rows"]}


def test_default_window_from_dashboard(client, seeded_chart, fake_today):
    """No dates in the request -> dashboard defaults: range=2 days ending at the
    offset cap (06-14) -> [06-13, 06-14]."""
    r = client.post(
        "/dashboards",
        json={"name": "Windowed", "default_date_range_days": 2, "default_end_offset_days": 2},
    )
    dash = r.json()
    w = add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}]})
    body = widget_data(client, dash, w)
    assert body["from_date"] == "2026-06-13"
    assert body["to_date"] == "2026-06-14"
    assert {r["event_date"] for r in body["rows"]} == {"2026-06-13", "2026-06-14"}


# ---------- the invariant: widget == its chart ----------

def test_widget_matches_direct_chart_serving(client, seeded_chart, dash, fake_today):
    """A widget must show EXACTLY what its source chart shows for the equivalent
    query — same cache, same aggregation logic."""
    w = add_widget(
        client, dash, seeded_chart,
        {"metrics": [{"name": "revenue"}, {"name": "dau"}],
         "group_by": ["country"], "filters": {"platform": ["ANDROID"]}},
    )
    via_widget = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-14")

    direct = client.get(
        f"/charts/{seeded_chart}/data",
        params={
            "from_date": "2026-06-12",
            "to_date": "2026-06-14",
            "group_by": ["country"],
            "metrics": ["revenue", "dau"],
            "filters": json.dumps({"platform": ["ANDROID"]}),
        },
    ).json()

    assert via_widget["rows"] == direct["rows"]
    assert via_widget["row_count"] == direct["row_count"]


# ---------- errors ----------

def test_number_widget_served_on_same_endpoint(client, seeded_chart, dash, fake_today):
    """Both widget types resolve through the one /data endpoint (detailed number
    math is covered in test_number_widgets.py)."""
    w = add_widget(client, dash, seeded_chart, {"metric": "revenue"}, wtype="number")
    r = client.get(f"/dashboards/{dash['id']}/widgets/{w['id']}/data")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metric"] == "revenue"
    assert "value" in body and "compares" in body


def test_widget_data_errors(client, seeded_chart, dash, fake_today):
    w = add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}]})
    assert client.get(f"/dashboards/{dash['id']}/widgets/9999/data").status_code == 404
    assert (
        client.get(
            f"/dashboards/{dash['id']}/widgets/{w['id']}/data", params={"filters": "{bad"}
        ).status_code
        == 400
    )
    assert (
        client.get(
            f"/dashboards/{dash['id']}/widgets/{w['id']}/data",
            params={"from_date": "2026-06-14", "to_date": "2026-06-12"},
        ).status_code
        == 422
    )


# ---------- phase 4: filter-values for the global bar ----------

def _make_second_chart(client, duckdb_path):
    """A chart with a DIFFERENT dim set (country + gid, no platform) and
    different country values, to prove the union + applicability rules."""
    r = client.post(
        "/charts", json={"name": "second-src", "query": "SELECT 1", "time_column": "event_date"}
    )
    chart_id = r.json()["id"]
    client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [
                {"name": "country", "column_name": "country"},
                {"name": "gid", "column_name": "gid"},
            ],
            "metrics": [{"name": "installs", "column_name": "installs"}],
        },
    )
    conn = duckdb.connect(duckdb_path)
    table = table_name(chart_id)
    conn.execute(
        f'CREATE TABLE "{table}" (event_date DATE, country VARCHAR, gid VARCHAR, installs BIGINT)'
    )
    conn.executemany(
        f'INSERT INTO "{table}" VALUES (?, ?, ?, ?)',
        [
            (date(2026, 6, 13), "US", "wca_ios", 10),
            (date(2026, 6, 13), "DE", "wca_android", 20),
        ],
    )
    conn.close()
    return chart_id


def test_filter_values_union_across_source_charts(client, duckdb_path, seeded_chart, dash, fake_today):
    """country exists on both charts -> union of both value sets; gid exists only
    on the second chart -> only its values; platform (not a configured filter)
    isn't returned."""
    second = _make_second_chart(client, duckdb_path)
    add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}]}, name="w1")
    add_widget(client, dash, second, {"metrics": [{"name": "installs"}]}, name="w2")
    client.put(
        f"/dashboards/{dash['id']}/filters",
        json={"filters": [{"dimension": "country"}, {"dimension": "gid"}]},
    )

    r = client.get(f"/dashboards/{dash['id']}/filter-values")
    assert r.status_code == 200, r.text
    values = r.json()["values"]
    assert values["country"] == ["DE", "UK", "US"]  # union, naturally sorted
    assert values["gid"] == ["wca_android", "wca_ios"]  # second chart only
    assert set(values.keys()) == {"country", "gid"}


def test_filter_values_respect_metric_value_order(client, duckdb_path, dash, fake_today):
    """A dimension set to value_order='metric' keeps its biggest-first order in the chip.

    filter_values() used to collect into a set and re-sort by _natural_key unconditionally, so a
    'metric'-ordered dimension came out alphabetical — and disagreed with the dashboard's own
    edit mode, which builds options from per-chart dimension_values and so kept the ranking.
    """
    r = client.post(
        "/charts", json={"name": "metric-order-src", "query": "SELECT 1", "time_column": "event_date"}
    )
    chart_id = r.json()["id"]
    client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [{"name": "country", "column_name": "country", "value_order": "metric"}],
            "metrics": [{"name": "installs", "column_name": "installs"}],
        },
    )
    conn = duckdb.connect(duckdb_path)
    table = table_name(chart_id)
    conn.execute(f'CREATE TABLE "{table}" (event_date DATE, country VARCHAR, installs BIGINT)')
    # alphabetically AR < US < ZA; by installs the ranking is ZA, AR, US — so the two orders
    # disagree on every position and a natural sort cannot pass by luck
    conn.executemany(
        f'INSERT INTO "{table}" VALUES (?, ?, ?)',
        [
            (date(2026, 6, 13), "AR", 50),
            (date(2026, 6, 13), "US", 10),
            (date(2026, 6, 13), "ZA", 90),
        ],
    )
    conn.close()

    add_widget(client, dash, chart_id, {"metrics": [{"name": "installs"}]}, name="w-metric")
    client.put(f"/dashboards/{dash['id']}/filters", json={"filters": [{"dimension": "country"}]})

    values = client.get(f"/dashboards/{dash['id']}/filter-values").json()["values"]
    assert values["country"] == ["ZA", "AR", "US"], "metric ranking lost — re-sorted naturally?"


def test_filter_values_no_filters_configured(client, dash):
    r = client.get(f"/dashboards/{dash['id']}/filter-values")
    assert r.status_code == 200
    assert r.json() == {"values": {}}


def test_filter_values_dim_on_no_chart_is_empty(client, seeded_chart, dash, fake_today):
    """A configured filter whose dimension no source chart has yields an empty
    option list (the chip renders, binds to nothing)."""
    add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}]})
    client.put(
        f"/dashboards/{dash['id']}/filters", json={"filters": [{"dimension": "install_type"}]}
    )
    values = client.get(f"/dashboards/{dash['id']}/filter-values").json()["values"]
    assert values["install_type"] == []


def test_widget_data_bad_cache_type_is_400_not_500(client, duckdb_path, dash, fake_today):
    """A metric column cached with a non-numeric type (first-value inference gone
    wrong) must surface as a readable 400 on the widget, not a blank 500."""
    r = client.post(
        "/charts", json={"name": "varchar-src", "query": "SELECT 1", "time_column": "event_date"}
    )
    chart_id = r.json()["id"]
    client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [{"name": "country", "column_name": "country"}],
            "metrics": [{"name": "dau", "column_name": "dau"}],
        },
    )
    conn = duckdb.connect(duckdb_path)
    table = table_name(chart_id)
    conn.execute(f'CREATE TABLE "{table}" (event_date VARCHAR, country VARCHAR, dau VARCHAR)')
    conn.execute(f'INSERT INTO "{table}" VALUES (?, ?, ?)', ("2026-06-13", "US", "100"))
    conn.close()

    w = add_widget(client, dash, chart_id, {"metrics": [{"name": "dau"}]})
    r = client.get(f"/dashboards/{dash['id']}/widgets/{w['id']}/data")
    assert r.status_code == 400
    assert "cache error" in r.json()["detail"]


# ---------- phase 8: widget-preview (ad-hoc, unsaved widget rendering) ----------

def test_preview_chart_matches_saved_widget(client, seeded_chart, dash, fake_today):
    """A preview of a config equals what a saved widget with the same config returns —
    the edit-mode working copy renders identically to the committed widget."""
    saved = add_widget(
        client, dash, seeded_chart,
        {"metrics": [{"name": "revenue"}], "group_by": ["country"], "filters": {"platform": ["ANDROID"]}},
    )
    via_saved = widget_data(client, dash, saved, from_date="2026-06-12", to_date="2026-06-14")

    r = client.post(
        f"/dashboards/{dash['id']}/widget-preview",
        json={
            "type": "chart",
            "source_chart_id": seeded_chart,
            "config": {"metrics": [{"name": "revenue"}], "group_by": ["country"], "filters": {"platform": ["ANDROID"]}},
            "from_date": "2026-06-12",
            "to_date": "2026-06-14",
            "granularity": "day",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == via_saved["rows"]


def test_preview_number_matches_saved(client, seeded_chart, dash, fake_today):
    saved = add_widget(client, dash, seeded_chart, {"metric": "revenue"}, wtype="number")
    via_saved = widget_data(client, dash, saved)

    r = client.post(
        f"/dashboards/{dash['id']}/widget-preview",
        json={"type": "number", "source_chart_id": seeded_chart, "config": {"metric": "revenue"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == via_saved["value"]
    assert r.json()["compares"] == via_saved["compares"]


def test_preview_honors_global_filters(client, seeded_chart, dash, fake_today):
    r = client.post(
        f"/dashboards/{dash['id']}/widget-preview",
        json={
            "type": "chart",
            "source_chart_id": seeded_chart,
            "config": {"metrics": [{"name": "revenue"}], "group_by": ["country"]},
            "from_date": "2026-06-12",
            "to_date": "2026-06-13",
            "filters": {"country": ["US"]},
        },
    )
    assert r.status_code == 200, r.text
    assert {row["country"] for row in r.json()["rows"]} == {"US"}


def test_preview_validates_and_never_persists(client, seeded_chart, dash, fake_today):
    did = dash["id"]
    # unknown chart -> 404
    assert client.post(f"/dashboards/{did}/widget-preview",
                       json={"type": "chart", "source_chart_id": 99999, "config": {"metrics": [{"name": "revenue"}]}}).status_code == 404
    # metric not on source chart -> 400 (same guard as save)
    assert client.post(f"/dashboards/{did}/widget-preview",
                       json={"type": "chart", "source_chart_id": seeded_chart, "config": {"metrics": [{"name": "nope"}]}}).status_code == 400
    # malformed config -> 422
    assert client.post(f"/dashboards/{did}/widget-preview",
                       json={"type": "chart", "source_chart_id": seeded_chart, "config": {"metrics": []}}).status_code == 422
    # from > to -> 422
    assert client.post(f"/dashboards/{did}/widget-preview",
                       json={"type": "chart", "source_chart_id": seeded_chart, "config": {"metrics": [{"name": "revenue"}]},
                             "from_date": "2026-06-14", "to_date": "2026-06-12"}).status_code == 422
    # preview created no tabs/widgets on the dashboard
    tree = client.get(f"/dashboards/{did}").json()
    assert all(t["widgets"] == [] for t in tree["tabs"])


# ---------- global split (unchecking a filter chip cuts the chart) ----------

def test_split_adds_group_by(client, seeded_chart, dash, fake_today):
    """A global split dimension is added to a chart widget's group_by — a widget
    with no group_by, split by country, returns one series per country."""
    w = add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}]})
    body = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-12", split=["country"])
    assert body["dimension_columns"] == ["country"]
    assert {r["country"] for r in body["rows"]} == {"US", "UK"}


def test_split_merges_with_widget_group_by(client, seeded_chart, dash, fake_today):
    w = add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}], "group_by": ["country"]})
    body = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-12", split=["platform"])
    assert body["dimension_columns"] == ["country", "platform"]


def test_split_dim_not_on_chart_is_ignored(client, seeded_chart, dash, fake_today):
    w = add_widget(client, dash, seeded_chart, {"metrics": [{"name": "revenue"}]})
    with_split = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-13", split=["gid"])
    without = widget_data(client, dash, w, from_date="2026-06-12", to_date="2026-06-13")
    assert with_split["rows"] == without["rows"]
    assert with_split["dimension_columns"] == []


def test_number_widget_ignores_split(client, seeded_chart, dash, fake_today):
    w = add_widget(client, dash, seeded_chart, {"metric": "revenue"}, wtype="number")
    body = widget_data(client, dash, w, split=["country"])
    assert "value" in body  # still a scalar tile; split has no effect


def test_preview_split(client, seeded_chart, dash, fake_today):
    r = client.post(
        f"/dashboards/{dash['id']}/widget-preview",
        json={"type": "chart", "source_chart_id": seeded_chart,
              "config": {"metrics": [{"name": "revenue"}]},
              "from_date": "2026-06-12", "to_date": "2026-06-12", "split": ["country"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["dimension_columns"] == ["country"]
    assert {row["country"] for row in r.json()["rows"]} == {"US", "UK"}
