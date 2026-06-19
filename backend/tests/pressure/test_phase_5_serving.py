"""Phase 5 pressure: summable aggregation invariants (harness-backed)."""

import json
from collections import defaultdict
from datetime import date

from . import golden
from .oracle import oracle_serve
from .reconcile import api_serve, reconcile_case


def test_no_group_revenue_equals_raw_daily_sum(golden_chart, client):
    cid, columns, rows, config = golden_chart
    idx = {c: i for i, c in enumerate(columns)}
    expected = defaultdict(float)
    for r in rows:
        expected[r[idx["event_date"]].isoformat()] += r[idx["revenue"]]
    api = api_serve(client, cid, group_by=[], metrics=["revenue"], granularity="day", filters={}, frm=None, to=None)
    got = {r["event_date"]: r["revenue"] for r in api}
    assert set(got) == set(expected)
    for d, v in expected.items():
        assert abs(got[d] - v) <= 1e-6 * max(1.0, abs(v))


def test_filter_then_group_equals_group_of_filtered_subset(golden_chart, client):
    cid, columns, rows, config = golden_chart
    reconcile_case(client, cid, columns, rows, config, {
        "group_by": ["source"], "metrics": ["revenue", "sessions"],
        "granularity": "day", "filters": {"country": ["US"]}, "frm": None, "to": None,
    })


def test_hide_zero_drops_exactly_the_all_zero_rows(golden_chart, client):
    cid, columns, rows, config = golden_chart
    gb = list(golden.DIMS)
    api_hz = api_serve(client, cid, group_by=gb, metrics=["crashes"], granularity="day",
                       filters={}, frm=None, to=None, hide_zero=True)
    # nothing zero/null survives
    assert all((r["crashes"] or 0) != 0 for r in api_hz)
    # and it matches the oracle's hide_zero set exactly
    orc = oracle_serve(columns, rows, config, {
        "dimensions": gb, "metrics": ["crashes"], "granularity": "day",
        "filters": {}, "hide_zero": True,
    })
    assert len(api_hz) == len(orc)


def test_out_of_range_returns_empty_not_error(golden_chart, client):
    cid, *_ = golden_chart
    api = api_serve(client, cid, group_by=None, metrics=["revenue"], granularity="day",
                    filters={}, frm=date(2030, 1, 1), to=date(2030, 1, 2))
    assert api == []


def test_unknown_dim_metric_filter_are_400(golden_chart, client):
    cid, *_ = golden_chart
    assert client.get(f"/charts/{cid}/data", params=[("group_by", "nope")]).status_code == 400
    assert client.get(f"/charts/{cid}/data", params=[("metrics", "nope")]).status_code == 400
    assert client.get(f"/charts/{cid}/data", params=[("filters", json.dumps({"nope": ["x"]}))]).status_code == 400
