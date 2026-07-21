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
revenue plain-summed; rev_per_dau = revenue/dau rounded to 4 decimals):

  date   dau                revenue  rev_per_dau
  6/07   1000               800      0.8000
  6/13   1200+50   = 1250   950      0.7600
  6/14   1300+100  = 1400   1105     0.7893   (1105/1400 = 0.78928…)

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
    """rev_per_dau = revenue / dau, evaluated on the deduped day totals and
    rounded to the metric's 4 decimals — same as the chart would plot."""
    w = add_tile(client, dash, seeded_chart, {"metric": "rev_per_dau", "decimals": 4})
    body = tile_data(client, dash, w)

    assert body["value"] == pytest.approx(0.7893)  # 1105/1400 rounded
    prev = body["compares"]["previous_day"]  # vs 0.7600
    assert prev["abs"] == pytest.approx(0.0293)
    assert prev["pct"] == pytest.approx((0.7893 / 0.76 - 1) * 100)
    lw = body["compares"]["last_week"]  # vs 0.8000
    assert lw["abs"] == pytest.approx(-0.0107)
    assert lw["pct"] == pytest.approx((0.7893 / 0.8 - 1) * 100)


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
