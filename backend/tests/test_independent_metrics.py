"""Phase 6: prove independent-dim metrics are NOT multiply-counted.

The core scenario from the build plan:
  - Chart has dims [source, country] and metric `dau`
  - DAU is marked independent of `source` (its value at any (date, country)
    is the SAME regardless of source — typical for a chart that surfaces
    total daily active users alongside source-specific revenue)
  - Naive SUM(dau) across source would multiply users. The serving layer
    must instead dedup across source (and JOIN back so source rows repeat).
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
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(f'"{c}"' for c in cols)
    conn.executemany(
        f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})',
        rows,
    )
    conn.close()


def _make_chart(client, dims, metrics_config, name="indep-test"):
    """metrics_config: list of dicts {name, column_name, independent_dimensions?}"""
    r = client.post(
        "/charts",
        json={
            "name": name,
            "query": "SELECT 1",
            "time_column": "event_date",
        },
    )
    chart_id = r.json()["id"]
    r = client.put(
        f"/charts/{chart_id}/dims-metrics",
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
    return chart_id


# ---------- The core scenario ----------
# Each (date, country) has 3 source rows with the SAME dau value
# (because dau is the same regardless of source).
#
# date1=2026-06-12:
#   country=US, sources={A,B,C} → dau=100 each (true US DAU = 100)
#   country=UK, sources={A,B,C} → dau=50 each  (true UK DAU = 50)
# date2=2026-06-13:
#   country=US, sources={A,B,C} → dau=120 each
INDEP_DATA = [
    (date(2026, 6, 12), "A", "US", 100),
    (date(2026, 6, 12), "B", "US", 100),
    (date(2026, 6, 12), "C", "US", 100),
    (date(2026, 6, 12), "A", "UK", 50),
    (date(2026, 6, 12), "B", "UK", 50),
    (date(2026, 6, 12), "C", "UK", 50),
    (date(2026, 6, 13), "A", "US", 120),
    (date(2026, 6, 13), "B", "US", 120),
    (date(2026, 6, 13), "C", "US", 120),
]
INDEP_COLUMNS = [
    ("event_date", "DATE"),
    ("source", "VARCHAR"),
    ("country", "VARCHAR"),
    ("dau", "BIGINT"),
]


@pytest.fixture
def chart_with_indep(client, duckdb_path):
    chart_id = _make_chart(
        client,
        dims=["source", "country"],
        metrics_config=[
            {"name": "dau", "column_name": "dau", "independent_dimensions": ["source"]}
        ],
    )
    _seed(duckdb_path, chart_id, INDEP_COLUMNS, INDEP_DATA)
    return chart_id


def test_dau_total_per_day_not_multiplied_across_source(client, chart_with_indep):
    """No dim grouping → DAU should equal true daily DAU (sum across country),
    NOT 3× that (which is what naive SUM-across-source would produce)."""
    r = client.get(f"/charts/{chart_with_indep}/data?group_by=&metrics=dau")
    body = r.json()
    by_date = {row["event_date"]: row for row in body["rows"]}
    # 2026-06-12: true total = 100 (US) + 50 (UK) = 150
    # Naive would give (100+50)*3 = 450
    assert by_date["2026-06-12"]["dau"] == 150
    # 2026-06-13: true total = 120 (US only)
    assert by_date["2026-06-13"]["dau"] == 120


def test_dau_per_country_dedupes_across_source(client, chart_with_indep):
    r = client.get(
        f"/charts/{chart_with_indep}/data?group_by=country&metrics=dau"
    )
    body = r.json()
    rows = {(row["event_date"], row["country"]): row for row in body["rows"]}
    # Each (date, country) gets the TRUE DAU, not 3× it
    assert rows[("2026-06-12", "US")]["dau"] == 100
    assert rows[("2026-06-12", "UK")]["dau"] == 50
    assert rows[("2026-06-13", "US")]["dau"] == 120


def test_dau_repeated_per_source_when_grouping_by_source(client, chart_with_indep):
    """GROUP BY source: each source row shows the same per-(date,country) value."""
    r = client.get(
        f"/charts/{chart_with_indep}/data?group_by=source&group_by=country&metrics=dau"
    )
    body = r.json()
    # 6 rows: 2 dates × 3 sources × (US in both, UK only in date1) — actually
    # only combos present in data appear. For 06-12 there's 3 sources × 2 countries = 6.
    # For 06-13 there's 3 sources × 1 country = 3. Total = 9.
    assert body["row_count"] == 9
    for r_ in body["rows"]:
        if r_["event_date"] == "2026-06-12" and r_["country"] == "US":
            assert r_["dau"] == 100  # NOT 300
        if r_["event_date"] == "2026-06-12" and r_["country"] == "UK":
            assert r_["dau"] == 50
        if r_["event_date"] == "2026-06-13" and r_["country"] == "US":
            assert r_["dau"] == 120


def test_dau_grouped_only_by_source_dedupes_then_sums_across_country(
    client, chart_with_indep
):
    """GROUP BY source only (no country): for each source, DAU is the dedup'd
    value across source (= true value per country), summed across countries.
    For 06-12: US (100) + UK (50) = 150, repeated for each source."""
    r = client.get(f"/charts/{chart_with_indep}/data?group_by=source&metrics=dau")
    body = r.json()
    by_key = {(r["event_date"], r["source"]): r for r in body["rows"]}
    for src in ("A", "B", "C"):
        assert by_key[("2026-06-12", src)]["dau"] == 150
        assert by_key[("2026-06-13", src)]["dau"] == 120


def test_summable_metric_unaffected_by_independent_dim_handling(
    client, duckdb_path
):
    """Revenue with no independent dims should still SUM correctly across all
    non-grouped dims. Sanity check that the new path doesn't break Phase 5."""
    chart_id = _make_chart(
        client,
        dims=["source", "country"],
        metrics_config=[{"name": "revenue", "column_name": "revenue"}],
        name="summable-check",
    )
    _seed(
        duckdb_path,
        chart_id,
        [
            ("event_date", "DATE"),
            ("source", "VARCHAR"),
            ("country", "VARCHAR"),
            ("revenue", "DOUBLE"),
        ],
        [
            (date(2026, 6, 12), "A", "US", 10.0),
            (date(2026, 6, 12), "B", "US", 20.0),
            (date(2026, 6, 12), "A", "UK", 5.0),
            (date(2026, 6, 12), "B", "UK", 7.0),
        ],
    )
    # No grouping → total revenue
    r = client.get(f"/charts/{chart_id}/data?group_by=&metrics=revenue")
    assert r.json()["rows"][0]["revenue"] == pytest.approx(42.0)
    # Group by source → sum across country
    r = client.get(f"/charts/{chart_id}/data?group_by=source&metrics=revenue")
    by_src = {row["source"]: row for row in r.json()["rows"]}
    assert by_src["A"]["revenue"] == pytest.approx(15.0)  # 10 + 5
    assert by_src["B"]["revenue"] == pytest.approx(27.0)  # 20 + 7


def test_metric_independent_of_all_dims_returns_single_value_per_time(
    client, duckdb_path
):
    """Metric independent of EVERY dim: deduped across all dims, just per-time."""
    chart_id = _make_chart(
        client,
        dims=["source", "country"],
        metrics_config=[
            {
                "name": "total_dau",
                "column_name": "total_dau",
                "independent_dimensions": ["source", "country"],
            }
        ],
        name="indep-of-all",
    )
    # 4 rows for one date with the SAME total_dau value (1000)
    _seed(
        duckdb_path,
        chart_id,
        [
            ("event_date", "DATE"),
            ("source", "VARCHAR"),
            ("country", "VARCHAR"),
            ("total_dau", "BIGINT"),
        ],
        [
            (date(2026, 6, 12), "A", "US", 1000),
            (date(2026, 6, 12), "B", "US", 1000),
            (date(2026, 6, 12), "A", "UK", 1000),
            (date(2026, 6, 12), "B", "UK", 1000),
        ],
    )
    r = client.get(f"/charts/{chart_id}/data?group_by=source&group_by=country")
    body = r.json()
    # 4 keyset rows, each with total_dau=1000 (not 4000, not 2000)
    assert body["row_count"] == 4
    for row in body["rows"]:
        assert row["total_dau"] == 1000


def test_mixed_metrics_independent_and_summable(client, duckdb_path):
    """One chart, two metrics: dau (independent of source) + revenue (summable).
    Each should aggregate correctly under the same request."""
    chart_id = _make_chart(
        client,
        dims=["source", "country"],
        metrics_config=[
            {
                "name": "dau",
                "column_name": "dau",
                "independent_dimensions": ["source"],
            },
            {"name": "revenue", "column_name": "revenue"},
        ],
        name="mixed-test",
    )
    # 06-12 US has 3 sources, each with dau=100 (true US DAU), source-specific revenue
    _seed(
        duckdb_path,
        chart_id,
        [
            ("event_date", "DATE"),
            ("source", "VARCHAR"),
            ("country", "VARCHAR"),
            ("dau", "BIGINT"),
            ("revenue", "DOUBLE"),
        ],
        [
            (date(2026, 6, 12), "A", "US", 100, 30.0),
            (date(2026, 6, 12), "B", "US", 100, 40.0),
            (date(2026, 6, 12), "C", "US", 100, 50.0),
        ],
    )
    # No grouping → DAU=100, revenue = 30+40+50 = 120
    r = client.get(f"/charts/{chart_id}/data?group_by=")
    row = r.json()["rows"][0]
    assert row["dau"] == 100
    assert row["revenue"] == pytest.approx(120.0)


def test_independent_metric_at_week_granularity(client, duckdb_path):
    """At week granularity, daily independent-metric values SUM across days
    in the bucket (sum-of-daily) but stay deduped within each day."""
    chart_id = _make_chart(
        client,
        dims=["source"],
        metrics_config=[
            {
                "name": "dau",
                "column_name": "dau",
                "independent_dimensions": ["source"],
            }
        ],
        name="indep-week",
    )
    # 3 days, 2 sources each, same dau per (day, source)
    _seed(
        duckdb_path,
        chart_id,
        [("event_date", "DATE"), ("source", "VARCHAR"), ("dau", "BIGINT")],
        [
            (date(2026, 6, 9), "A", 100),  # Mon
            (date(2026, 6, 9), "B", 100),
            (date(2026, 6, 10), "A", 110),  # Tue
            (date(2026, 6, 10), "B", 110),
            (date(2026, 6, 11), "A", 120),  # Wed
            (date(2026, 6, 11), "B", 120),
        ],
    )
    # All 3 days fall in the same week. No dim grouping.
    r = client.get(
        f"/charts/{chart_id}/data?group_by=&granularity=week&metrics=dau"
    )
    body = r.json()
    # Expected: per-day DAU deduped across source (100, 110, 120), summed across days = 330
    # NOT 100*2 + 110*2 + 120*2 = 660 (naive sum)
    assert body["row_count"] == 1
    assert body["rows"][0]["dau"] == 330


def test_filter_on_independent_dimension_does_not_change_metric(client, duckdb_path):
    """A filter on a metric's independent dimension must NOT change the metric,
    even when some segments lack the filtered value.

    Regression: filtering applied to every metric's WHERE, so filtering the
    independent dim dropped segments that had no row for the filtered value,
    silently shrinking a value that by definition doesn't depend on that dim.
    """
    import json
    from urllib.parse import quote

    chart_id = _make_chart(
        client,
        dims=["source", "country"],
        metrics_config=[
            {"name": "dau", "column_name": "dau", "independent_dimensions": ["source"]}
        ],
        name="indep-filter",
    )
    # dau is constant across source within a (date, country), BUT UK has only
    # source A (no B row) — so a source=B filter used to drop UK entirely.
    _seed(
        duckdb_path,
        chart_id,
        [("event_date", "DATE"), ("source", "VARCHAR"), ("country", "VARCHAR"), ("dau", "BIGINT")],
        [
            (date(2026, 6, 12), "A", "US", 100),
            (date(2026, 6, 12), "B", "US", 100),
            (date(2026, 6, 12), "A", "UK", 50),
        ],
    )

    def dau(filt=None):
        url = f"/charts/{chart_id}/data?group_by=&metrics=dau"
        if filt is not None:
            url += "&filters=" + quote(json.dumps(filt))
        return client.get(url).json()["rows"][0]["dau"]

    # True daily DAU = US(100) + UK(50) = 150.
    assert dau() == 150
    # Filtering the INDEPENDENT dim (source) must leave dau at 150 — even B,
    # which UK lacks (old behavior returned 100 by dropping UK).
    assert dau({"source": ["A"]}) == 150
    assert dau({"source": ["B"]}) == 150
    # But filtering a NON-independent dim (country) MUST still restrict dau.
    assert dau({"country": ["US"]}) == 100
    assert dau({"country": ["UK"]}) == 50


# ---------------------------------------------------------------------------------------
# Batching metrics by grain. serve_data used to run ONE DuckDB query per metric; six metrics
# on the production ads chart took 11.8s against 0.6s for one, to return 14 KB. Metrics are
# now grouped by their independent_dimensions set, since that set is the only thing that
# changes the generated SQL. These pin the two halves of that: batching must not change a
# value, and metrics at DIFFERENT grains must never share a query.
# ---------------------------------------------------------------------------------------

def test_batching_by_grain_gives_the_same_values_as_one_metric_at_a_time(client, duckdb_path):
    """Three grains in one chart. Requesting all five metrics together must give exactly what
    requesting each alone gives — otherwise the batching has merged grains."""
    chart_id = _make_chart(
        client,
        dims=["source", "country"],
        metrics_config=[
            # grain A: independent of both dims
            {"name": "mau", "column_name": "mau", "independent_dimensions": ["source", "country"]},
            # grain B: independent of source only
            {"name": "dau", "column_name": "dau", "independent_dimensions": ["source"]},
            {"name": "sessions", "column_name": "sessions", "independent_dimensions": ["source"]},
            # grain C: fully summable
            {"name": "revenue", "column_name": "revenue"},
            {"name": "clicks", "column_name": "clicks"},
        ],
        name="batch-grain-test",
    )
    _seed(
        duckdb_path, chart_id,
        [("event_date", "DATE"), ("source", "VARCHAR"), ("country", "VARCHAR"),
         ("mau", "BIGINT"), ("dau", "BIGINT"), ("sessions", "BIGINT"),
         ("revenue", "DOUBLE"), ("clicks", "BIGINT")],
        [
            (date(2026, 6, 12), "A", "US", 900, 100, 220, 30.0, 7),
            (date(2026, 6, 12), "B", "US", 900, 100, 220, 40.0, 9),
            (date(2026, 6, 12), "C", "US", 900, 100, 220, 50.0, 11),
            (date(2026, 6, 12), "A", "UK", 900, 60, 130, 12.0, 3),
            (date(2026, 6, 12), "B", "UK", 900, 60, 130, 15.0, 4),
        ],
    )
    names = ["mau", "dau", "sessions", "revenue", "clicks"]

    for group_by in ("", "country", "source", "country&group_by=source"):
        qs = f"group_by={group_by}"
        together = client.get(
            f"/charts/{chart_id}/data?{qs}&" + "&".join(f"metrics={n}" for n in names)
        ).json()["rows"]
        alone = {
            n: client.get(f"/charts/{chart_id}/data?{qs}&metrics={n}").json()["rows"]
            for n in names
        }
        assert len(together) == len(alone["dau"]), f"row count differs for group_by={group_by!r}"
        for i, row in enumerate(together):
            for n in names:
                assert row[n] == pytest.approx(alone[n][i][n]), (
                    f"{n} differs at row {i} for group_by={group_by!r}: "
                    f"batched {row[n]} vs alone {alone[n][i][n]}"
                )


def test_batching_refuses_to_mix_two_grains_in_one_query(client, duckdb_path):
    """The guard that makes the optimisation safe: merging different grains into one GROUP BY
    is precisely the double count this module exists to prevent, so it must raise rather than
    quietly produce a number."""
    from app.models import Metric
    from app.serving import _run_metric_group

    a = Metric(name="dau", column_name="dau", independent_dimensions=["source"])
    b = Metric(name="revenue", column_name="revenue", independent_dimensions=[])
    with pytest.raises(ValueError, match="one shared independent_dimensions set"):
        _run_metric_group(None, None, None, [a, b], ["source"])
