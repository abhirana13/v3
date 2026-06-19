"""Phase 6 pressure: independent-metric correctness — the crown jewel.

The bug we are hunting: SUM-ing a metric across a dimension it is independent of,
which multiply-counts it. These tests prove it never happens."""

from collections import defaultdict

from . import golden
from .helpers import configure_chart, load_rows
from .oracle import bucketize
from .reconcile import api_serve, reconcile_case


def _daily(api, key):
    return {r["event_date"]: r[key] for r in api}


def test_dau_total_is_invariant_to_source_cardinality(client, pressure_duckdb):
    """The whole point: served `dau`/`installs` totals per day must be IDENTICAL
    whether there are 1, 4, or 12 sources — because they don't depend on source.
    A naive SUM would scale ~N× (12× here would be glaring)."""
    totals = []
    for n in (1, 4, 12):
        srcs = [f"s{i}" for i in range(n)]
        columns, rows = golden.generate(sources=srcs)
        cid = configure_chart(client, golden.CONFIG, name=f"card-{n}")
        load_rows(cid, columns, rows, golden.CONFIG.time_column)
        api = api_serve(client, cid, group_by=[], metrics=["dau", "installs"],
                        granularity="day", filters={}, frm=None, to=None)
        totals.append({r["event_date"]: (r["dau"], r["installs"]) for r in api})
    assert totals[0] == totals[1] == totals[2]
    # sanity: there IS data, and a naive sum would have differed
    assert any(v != (0, 0) for v in totals[0].values())


def test_filtering_an_independent_dim_does_not_change_dau(golden_chart, client):
    cid, columns, rows, config = golden_chart
    a = _daily(api_serve(client, cid, group_by=[], metrics=["dau"], granularity="day",
                         filters={"source": ["A"]}, frm=None, to=None), "dau")
    b = _daily(api_serve(client, cid, group_by=[], metrics=["dau"], granularity="day",
                         filters={"source": ["B"]}, frm=None, to=None), "dau")
    assert a == b  # dau is the same value regardless of which source we keep
    # revenue DOES depend on source -> differs
    ra = _daily(api_serve(client, cid, group_by=[], metrics=["revenue"], granularity="day",
                          filters={"source": ["A"]}, frm=None, to=None), "revenue")
    rb = _daily(api_serve(client, cid, group_by=[], metrics=["revenue"], granularity="day",
                          filters={"source": ["B"]}, frm=None, to=None), "revenue")
    assert ra != rb


def test_grouping_by_independent_dim_repeats_the_deduped_value(golden_chart, client):
    cid, columns, rows, config = golden_chart
    nogroup = _daily(api_serve(client, cid, group_by=[], metrics=["dau"], granularity="day",
                               filters={}, frm=None, to=None), "dau")
    bysrc = api_serve(client, cid, group_by=["source"], metrics=["dau"], granularity="day",
                      filters={}, frm=None, to=None)
    for r in bysrc:
        assert r["dau"] == nogroup[r["event_date"]]  # repeated, never split/multiplied


def test_independent_metric_week_is_sum_of_daily_dedup(golden_chart, client):
    cid, columns, rows, config = golden_chart
    day = api_serve(client, cid, group_by=[], metrics=["dau"], granularity="day",
                    filters={}, frm=None, to=None)
    week = api_serve(client, cid, group_by=[], metrics=["dau"], granularity="week",
                     filters={}, frm=None, to=None)
    from datetime import date as _d
    expected = defaultdict(int)
    for r in day:
        wk = bucketize(_d.fromisoformat(r["event_date"]), "week").isoformat()
        expected[wk] += r["dau"]
    assert _daily(week, "dau") == dict(expected)


def test_mixed_independent_and_summable_in_one_request(golden_chart, client):
    cid, columns, rows, config = golden_chart
    reconcile_case(client, cid, columns, rows, config, {
        "group_by": ["source", "country"], "metrics": ["dau", "revenue", "installs"],
        "granularity": "day", "filters": {}, "frm": None, "to": None,
    })
