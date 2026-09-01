"""Phase 3 — number-widget serving: value at the as-of date + previous-day and
last-week (same weekday, −7d) deltas.

Every expected number in here is HAND-CALCULATED from the seed rows (see the
worked table below). The suite proves the two spec-critical properties:
  - a tile inherits the chart's independent-metric dedupe (dau is NOT summed
    across platform) and formula evaluation (rev_per_dau) — it always equals
    the number its source chart would plot for that day;
  - delta math: abs = v − v_prev, pct = (v/v_prev − 1)·100, null when a side is
    missing, pct null when v_prev = 0.

Seed (event_date, country, platform, dau, revenue) with dau INDEPENDENT of
platform (same value repeated across platforms by definition):

  6/07: US/AND (1000, 500), US/IOS (1000, 300)
  6/13: US/AND (1200, 600), US/IOS (1200, 350), UK/AND (50, 0)
  6/14: US/AND (1300, 700), US/IOS (1300, 380), UK/AND (100, 20), UK/IOS (100, 5)

Day totals (dau deduped per country via MAX over platform, then summed;
revenue plain-summed; rev_per_dau = revenue/dau at FULL PRECISION — `decimals` is a
display setting and serving no longer rounds by it):

  date   dau                revenue  rev_per_dau
  6/07   1000               800      0.8                   (exact)
  6/13   1200+50   = 1250   950      0.76                  (exact)
  6/14   1300+100  = 1400   1105     0.7892857142857143    (1105/1400, non-terminating)

fake today = 2026-06-16, dashboard offset 2 → as_of = 2026-06-14.
"""

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
    monkeypatch.setattr("app.dashboards.serving._today", lambda: date(2026, 6, 16))
    return date(2026, 6, 16)


SEED_ROWS = [
    (date(2026, 6, 7), "US", "ANDROID", 1000, 500.0),
    (date(2026, 6, 7), "US", "IOS", 1000, 300.0),
    (date(2026, 6, 13), "US", "ANDROID", 1200, 600.0),
    (date(2026, 6, 13), "US", "IOS", 1200, 350.0),
    (date(2026, 6, 13), "UK", "ANDROID", 50, 0.0),
    (date(2026, 6, 14), "US", "ANDROID", 1300, 700.0),
    (date(2026, 6, 14), "US", "IOS", 1300, 380.0),
    (date(2026, 6, 14), "UK", "ANDROID", 100, 20.0),
    (date(2026, 6, 14), "UK", "IOS", 100, 5.0),
]


@pytest.fixture
def seeded_chart(client, duckdb_path):
    r = client.post(
        "/charts", json={"name": "tile-src", "query": "SELECT 1", "time_column": "event_date"}
    )
    chart_id = r.json()["id"]
    r = client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [
                {"name": "country", "column_name": "country"},
                {"name": "platform", "column_name": "platform"},
            ],
            "metrics": [
                # dau is the same across platforms -> declared independent of platform
                {"name": "dau", "column_name": "dau", "independent_dimensions": ["platform"]},
                {"name": "revenue", "column_name": "revenue"},
                {"name": "rev_per_dau", "formula": "revenue / dau", "decimals": 4},
            ],
        },
    )
    assert r.status_code == 200, r.text

    conn = duckdb.connect(duckdb_path)
    table = table_name(chart_id)
    conn.execute(
        f'CREATE TABLE "{table}" '
        "(event_date DATE, country VARCHAR, platform VARCHAR, dau BIGINT, revenue DOUBLE)"
    )
    conn.executemany(f'INSERT INTO "{table}" VALUES (?, ?, ?, ?, ?)', SEED_ROWS)
    conn.close()
    return chart_id


@pytest.fixture
def dash(client):
    return client.post("/dashboards", json={"name": "Tiles"}).json()


def add_tile(client, dash, chart_id, config, name="tile"):
    tab_id = client.get(f"/dashboards/{dash['id']}").json()["tabs"][0]["id"]
    r = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets",
        json={
            "type": "number",
            "source_chart_id": chart_id,
            "name": name,
            "layout": {"x": 0, "y": 0, "w": 3, "h": 2},
            "config": config,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def tile_data(client, dash, widget, **params):
    if "filters" in params and isinstance(params["filters"], dict):
        params["filters"] = json.dumps(params["filters"])
    r = client.get(f"/dashboards/{dash['id']}/widgets/{widget['id']}/data", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_revenue_tile_value_and_deltas(client, seeded_chart, dash, fake_today):
    w = add_tile(client, dash, seeded_chart, {"metric": "revenue", "unit": "$", "decimals": 2})
    body = tile_data(client, dash, w)

    assert body["as_of_date"] == "2026-06-14"
    assert body["metric"] == "revenue"
    assert body["unit"] == "$"
    assert body["value"] == pytest.approx(1105.0)

    prev = body["compares"]["previous_day"]  # vs 6/13: 950
    assert prev["abs"] == pytest.approx(155.0)
    assert prev["pct"] == pytest.approx((1105 / 950 - 1) * 100)  # 16.3158%

    lw = body["compares"]["last_week"]  # vs 6/07: 800
    assert lw["abs"] == pytest.approx(305.0)
    assert lw["pct"] == pytest.approx(38.125)


def test_independent_metric_deduped_not_summed(client, seeded_chart, dash, fake_today):
    """THE dashboards correctness case: dau repeats across platform rows, so a
    naive SUM on 6/14 would give 2800 (US) + 200 (UK) = 3000+... The correct
    dedupe is MAX per (day, country) then sum: 1300 + 100 = 1400."""
    w = add_tile(client, dash, seeded_chart, {"metric": "dau"})
    body = tile_data(client, dash, w)

    assert body["value"] == 1400  # not 2800
    prev = body["compares"]["previous_day"]  # vs 6/13: 1250
    assert prev["abs"] == 150
    assert prev["pct"] == pytest.approx(12.0)
    lw = body["compares"]["last_week"]  # vs 6/07: 1000
    assert lw["abs"] == 400
    assert lw["pct"] == pytest.approx(40.0)


def test_formula_metric_tile(client, seeded_chart, dash, fake_today):
    """rev_per_dau = revenue / dau on the deduped day totals, at FULL PRECISION — same as
    the chart would plot.

    The expectations are written as arithmetic rather than decimal literals on purpose. This
    test used to assert 0.7893, i.e. 1105/1400 rounded to the metric's `decimals`, which is
    precisely the behaviour that made small ratios plot as flat stepped lines. `decimals` is
    applied at render time now, so the API returns the undivided truth and a literal here
    would silently re-pin the old bug.
    """
    w = add_tile(client, dash, seeded_chart, {"metric": "rev_per_dau", "decimals": 4})
    body = tile_data(client, dash, w)

    assert body["value"] == pytest.approx(1105 / 1400)  # 0.78928571…, NOT 0.7893
    prev = body["compares"]["previous_day"]  # vs 950/1250 = 0.76 exactly
    assert prev["abs"] == pytest.approx(1105 / 1400 - 0.76)
    assert prev["pct"] == pytest.approx(((1105 / 1400) / 0.76 - 1) * 100)
    lw = body["compares"]["last_week"]  # vs 800/1000 = 0.8 exactly
    assert lw["abs"] == pytest.approx(1105 / 1400 - 0.8)
    assert lw["pct"] == pytest.approx(((1105 / 1400) / 0.8 - 1) * 100)


def test_tile_widget_filters_and_zero_prev(client, seeded_chart, dash, fake_today):
    """UK-only tile: 6/14 = 25, 6/13 = 0 (a real zero row), 6/07 has no UK data.
    -> pct vs a zero prev is null (undefined) but abs still shows; a missing
    lookback day nulls the whole compare."""
    w = add_tile(client, dash, seeded_chart, {"metric": "revenue", "filters": {"country": ["UK"]}})
    body = tile_data(client, dash, w)

    assert body["value"] == pytest.approx(25.0)
    prev = body["compares"]["previous_day"]
    assert prev["abs"] == pytest.approx(25.0)
    assert prev["pct"] is None  # divide-by-zero -> undefined, not inf
    assert body["compares"]["last_week"] is None  # no UK rows on 6/07


def test_tile_respects_global_filters(client, seeded_chart, dash, fake_today):
    w = add_tile(client, dash, seeded_chart, {"metric": "revenue"})
    body = tile_data(client, dash, w, filters={"country": ["US"]})

    assert body["value"] == pytest.approx(1080.0)  # 700 + 380
    prev = body["compares"]["previous_day"]  # vs US 6/13: 950
    assert prev["abs"] == pytest.approx(130.0)
    assert prev["pct"] == pytest.approx((1080 / 950 - 1) * 100)
    lw = body["compares"]["last_week"]  # vs US 6/07: 800
    assert lw["abs"] == pytest.approx(280.0)
    assert lw["pct"] == pytest.approx(35.0)


def test_tile_empty_intersection_nulls_value(client, seeded_chart, dash, fake_today):
    w = add_tile(client, dash, seeded_chart, {"metric": "revenue", "filters": {"country": ["UK"]}})
    body = tile_data(client, dash, w, filters={"country": ["US"]})
    assert body["value"] is None
    assert body["compares"] == {"previous_day": None, "last_week": None}


def test_tile_as_of_from_to_date_and_missing_lookbacks(client, seeded_chart, dash, fake_today):
    """to_date=6/07 -> as_of 6/07 (inside the offset cap). 6/06 and 5/31 have no
    data, so both compares are null while the value itself is fine."""
    w = add_tile(client, dash, seeded_chart, {"metric": "revenue"})
    body = tile_data(client, dash, w, to_date="2026-06-07")

    assert body["as_of_date"] == "2026-06-07"
    assert body["value"] == pytest.approx(800.0)
    assert body["compares"]["previous_day"] is None
    assert body["compares"]["last_week"] is None


def test_tile_compares_subset(client, seeded_chart, dash, fake_today):
    w = add_tile(client, dash, seeded_chart, {"metric": "revenue", "compares": ["last_week"]})
    body = tile_data(client, dash, w)
    assert list(body["compares"].keys()) == ["last_week"]


def test_tile_offset_caps_as_of(client, seeded_chart, dash, fake_today):
    """Widget offset_days=0 lifts the dashboard's default cap: as_of = today
    (6/16), where there's no data -> value null. The default (offset 2) tile
    lands on 6/14 instead."""
    w = add_tile(client, dash, seeded_chart, {"metric": "revenue", "offset_days": 0})
    body = tile_data(client, dash, w)
    assert body["as_of_date"] == "2026-06-16"
    assert body["value"] is None


# ---------- anchoring: "as of D" against a cohort date, not the activity date ----------

@pytest.fixture
def cohort_chart(client, duckdb_path):
    """A retention-shaped chart: time column is the ACTIVITY date, and install_date is a
    separate dimension the chart pivots on. Deliberately built so the two anchors disagree.

    Seed — two install cohorts, each active on several later days:

      install_date  event_date   installs  returned
      06-10         06-11               100        40
      06-10         06-12               100        30
      06-11         06-12               200       120
      06-11         06-13               200        60

    Anchored on event_date, 06-12 mixes BOTH cohorts: returned 30+120=150 over installs
    100+200=300 => 50%, a number belonging to neither cohort.
    Anchored on install_date, 06-11 is one cohort: 120+60=180 over 200 => 90%.
    """
    r = client.post(
        "/charts", json={"name": "cohort-src", "query": "SELECT 1", "time_column": "event_date"}
    )
    chart_id = r.json()["id"]
    client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [{"name": "install_date", "column_name": "install_date"}],
            "metrics": [
                # installs is the COHORT size, so it must not be summed across activity days
                {"name": "installs", "column_name": "installs",
                 "independent_dimensions": []},
                {"name": "returned", "column_name": "returned"},
                {"name": "retention", "formula": "returned * 100 / installs", "decimals": 2},
            ],
        },
    )
    # the chart itself pivots on the cohort date
    client.put(f"/charts/{chart_id}", json={"x_axis": "install_date"})

    conn = duckdb.connect(duckdb_path)
    t = table_name(chart_id)
    conn.execute(
        f'CREATE TABLE "{t}" (event_date DATE, install_date DATE, installs BIGINT, returned BIGINT)'
    )
    conn.executemany(
        f'INSERT INTO "{t}" VALUES (?, ?, ?, ?)',
        [
            (date(2026, 6, 11), date(2026, 6, 10), 100, 40),
            (date(2026, 6, 12), date(2026, 6, 10), 100, 30),
            (date(2026, 6, 12), date(2026, 6, 11), 200, 120),
            (date(2026, 6, 13), date(2026, 6, 11), 200, 60),
        ],
    )
    conn.close()
    return chart_id


def test_tile_inherits_the_charts_cohort_anchor(client, cohort_chart, dash, fake_today):
    """A tile on a chart that pivots on install_date reads the COHORT, not the activity day.

    Previously number_widget_data keyed rows on chart.time_column with no x_axis, so a tile on
    a retention chart reported against the event date: numerator and denominator aggregated
    over a day that mixes every cohort active on it, which is a rate for nobody.
    """
    w = add_tile(client, dash, cohort_chart, {"metric": "returned", "decimals": 0})
    body = tile_data(client, dash, w, to_date="2026-06-11")

    assert body["anchored_on"] == "install_date"
    # cohort 06-11 returned 120 + 60 across its activity days
    assert body["value"] == pytest.approx(180)


def test_tile_can_force_the_time_column(client, cohort_chart, dash, fake_today):
    """Naming the time column overrides an inherited cohort anchor."""
    w = add_tile(
        client, dash, cohort_chart,
        {"metric": "returned", "decimals": 0, "x_axis": "event_date"},
    )
    body = tile_data(client, dash, w, to_date="2026-06-12")

    assert body["anchored_on"] == "event_date"
    # activity day 06-12 mixes both cohorts: 30 + 120
    assert body["value"] == pytest.approx(150)


def test_cohort_anchor_and_time_anchor_disagree(client, cohort_chart, dash, fake_today):
    """The whole point, stated as one assertion: the two anchors give different answers, so
    which one a tile uses is not cosmetic."""
    w_cohort = add_tile(client, dash, cohort_chart, {"metric": "returned"}, name="c")
    w_time = add_tile(
        client, dash, cohort_chart, {"metric": "returned", "x_axis": "event_date"}, name="t"
    )
    on_11_cohort = tile_data(client, dash, w_cohort, to_date="2026-06-11")["value"]
    on_11_time = tile_data(client, dash, w_time, to_date="2026-06-11")["value"]
    assert on_11_cohort == pytest.approx(180)   # cohort installed 06-11
    assert on_11_time == pytest.approx(40)      # activity on 06-11 (cohort 06-10's D1)
    assert on_11_cohort != on_11_time


def test_tile_anchor_must_exist_on_the_chart(client, cohort_chart, dash, fake_today):
    """A typo'd anchor is a 400 at save time, not a silent fall back to the time column."""
    tab_id = client.get(f"/dashboards/{dash['id']}").json()["tabs"][0]["id"]
    r = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets",
        json={"type": "number", "source_chart_id": cohort_chart, "name": "bad",
              "layout": {"x": 0, "y": 0, "w": 3, "h": 2},
              "config": {"metric": "returned", "x_axis": "nope"}},
    )
    assert r.status_code == 400, r.text
    assert "x_axis" in r.text
