"""Phase 7 pressure: formula metrics — ratio-of-sums, independence, safety.

(Null/zero propagation, decimals, base⊕formula exclusivity are in
tests/test_formulas.py and tests/test_formula_serving.py.)"""

from collections import defaultdict
from datetime import date

import pytest

from .oracle import bucketize
from .reconcile import api_serve


def test_arpu_week_is_ratio_of_sums_not_mean_of_daily_ratios(golden_chart, client):
    cid, columns, rows, config = golden_chart
    week_base = api_serve(client, cid, group_by=[], metrics=["revenue", "dau"],
                          granularity="week", filters={}, frm=None, to=None)
    week_arpu = {r["event_date"]: r["arpu"] for r in
                 api_serve(client, cid, group_by=[], metrics=["arpu"],
                           granularity="week", filters={}, frm=None, to=None)}
    # arpu == Σrevenue / Σdau over the bucket
    for r in week_base:
        expected = round(r["revenue"] / r["dau"], 4)
        assert abs(week_arpu[r["event_date"]] - expected) <= 1.5e-4

    # ...and that is demonstrably NOT the mean of the daily ratios
    day = api_serve(client, cid, group_by=[], metrics=["arpu"], granularity="day",
                    filters={}, frm=None, to=None)
    daily = defaultdict(list)
    for r in day:
        daily[bucketize(date.fromisoformat(r["event_date"]), "week").isoformat()].append(r["arpu"])
    assert any(
        abs(sum(v) / len(v) - week_arpu[wk]) > 1e-3
        for wk, v in daily.items()
    ), "ratio-of-sums coincided with mean-of-ratios everywhere (suspicious)"


def test_arpu_divides_by_deduped_dau_when_grouped_by_independent_dim(golden_chart, client):
    """Regression guard: the §6 independence bug must not leak into formulas.
    arpu per source = (per-source revenue) / (deduped daily dau)."""
    cid, columns, rows, config = golden_chart
    by_src = api_serve(client, cid, group_by=["source"], metrics=["revenue", "dau", "arpu"],
                       granularity="day", filters={}, frm=None, to=None)
    for r in by_src:
        assert abs(r["arpu"] - round(r["revenue"] / r["dau"], 4)) <= 1.5e-4


@pytest.mark.parametrize("expr", [
    "abs(revenue)",          # function call
    "revenue.real",          # attribute access
    "revenue if dau else 1",  # conditional expression
    "[revenue]",             # list literal
    "revenue > dau",         # comparison
    "revenue & dau",         # bitwise op (not in arithmetic whitelist)
    "mau / dau",             # unknown base metric
    "revenue + 'x'",         # string literal
])
def test_unsafe_or_invalid_formula_rejected_at_config_time(client, expr):
    r = client.post("/charts", json={"name": f"bad-{abs(hash(expr))}", "query": "SELECT 1",
                                     "time_column": "event_date"})
    cid = r.json()["id"]
    r = client.put(f"/charts/{cid}/dims-metrics", json={
        "time_column": "event_date",
        "dimensions": [],
        "metrics": [
            {"name": "revenue", "column_name": "revenue"},
            {"name": "dau", "column_name": "dau"},
            {"name": "bad", "formula": expr},
        ],
    })
    assert r.status_code == 422, f"expected rejection for formula {expr!r}, got {r.status_code}"
