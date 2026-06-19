"""Request-matrix generator + API caller + reconciliation assertion."""

import json
from datetime import date

from . import golden
from .oracle import oracle_serve


def api_serve(client, chart_id, *, group_by, metrics, granularity, filters, frm, to, hide_zero=False):
    q = [("granularity", granularity)]
    if hide_zero:
        q.append(("hide_zero", "true"))
    if frm:
        q.append(("from_date", frm.isoformat()))
    if to:
        q.append(("to_date", to.isoformat()))
    if group_by is not None:
        if len(group_by) == 0:
            q.append(("group_by", ""))  # explicit time-only (API filters empties -> [])
        else:
            q.extend(("group_by", g) for g in group_by)
    if metrics is not None:
        q.extend(("metrics", m) for m in metrics)
    if filters:
        q.append(("filters", json.dumps(filters)))
    r = client.get(f"/charts/{chart_id}/data", params=q)
    assert r.status_code == 200, f"{r.status_code}: {r.text}"
    return r.json()["rows"]


def _key(row, tcol, dim_names):
    t = row[tcol]
    t = t if isinstance(t, str) else t.isoformat()
    return (t, tuple(str(row[d]) for d in dim_names))


def _num_eq(a, b, *, abs_tol=None, tol=1e-6):
    if a is None or b is None:
        return a is None and b is None
    if abs_tol is not None:
        return abs(a - b) <= abs_tol
    return abs(a - b) <= tol * max(1.0, abs(b))


def assert_equal(api_rows, oracle_rows, tcol, dim_names, metric_names, label="", formula_decimals=None):
    # Formula metrics are rounded by both sides; FP sum-order noise (~1e-13) in
    # the base values can straddle a rounding boundary and flip the last decimal.
    # Allow one last-place unit for formulas; base metrics stay tight (1e-6).
    formula_decimals = formula_decimals or {}
    a = sorted(api_rows, key=lambda r: _key(r, tcol, dim_names))
    o = sorted(oracle_rows, key=lambda r: _key(r, tcol, dim_names))
    assert len(a) == len(o), f"{label}: row_count api={len(a)} oracle={len(o)}"
    for ar, orow in zip(a, o):
        ak, ok = _key(ar, tcol, dim_names), _key(orow, tcol, dim_names)
        assert ak == ok, f"{label}: keyset mismatch api={ak} oracle={ok}"
        for m in metric_names:
            abs_tol = 1.5 * 10 ** -formula_decimals[m] if m in formula_decimals else None
            assert _num_eq(ar.get(m), orow.get(m), abs_tol=abs_tol), (
                f"{label}: metric '{m}' at {ak}: api={ar.get(m)} oracle={orow.get(m)}"
            )


def reconcile_case(client, chart_id, columns, rows, config, case):
    gb = case["group_by"]
    ms = case["metrics"]
    api_rows = api_serve(
        client, chart_id,
        group_by=gb, metrics=ms, granularity=case["granularity"],
        filters=case.get("filters") or {}, frm=case.get("frm"), to=case.get("to"),
        hide_zero=case.get("hide_zero", False),
    )
    oracle_rows = oracle_serve(columns, rows, config, {
        "dimensions": gb, "metrics": ms, "granularity": case["granularity"],
        "filters": case.get("filters") or {},
        "from_date": case.get("frm"), "to_date": case.get("to"),
        "hide_zero": case.get("hide_zero", False),
    })
    dim_names = config.dims if gb is None else gb
    metric_names = config.all_metric_names if ms is None else ms
    formula_decimals = {f.name: f.decimals for f in config.formulas if f.name in metric_names}
    assert_equal(
        api_rows, oracle_rows, config.time_column, dim_names, metric_names,
        label=str(case), formula_decimals=formula_decimals,
    )


# ---- the matrix ----
FULL_START, FULL_END = golden.date_bounds()

GROUP_BYS = [None, [], ["source"], ["country"], ["platform"], ["source", "country"], ["gid", "platform"], list(golden.DIMS)]
METRIC_SETS = [None, ["revenue"], ["dau"], ["installs"], ["revenue", "dau"], ["arpu"], ["arpu", "revenue", "dau"], ["crashes"], ["rev_per_session", "sessions", "revenue"]]
GRANS = ["day", "week", "month"]
FILTERS = [{}, {"source": ["A"]}, {"country": ["US", "UK"]}, {"source": ["A", "B"], "platform": ["ios"]}]
RANGES = [
    (FULL_START, FULL_END),                    # full
    (date(2026, 6, 1), date(2026, 6, 7)),      # one week
    (FULL_START, FULL_START),                  # single day
    (date(2026, 6, 2), date(2026, 6, 4)),      # spans the missing day (6/3)
]


def _case(gb, ms, gran, filt, rng):
    return {"group_by": gb, "metrics": ms, "granularity": gran, "filters": filt, "frm": rng[0], "to": rng[1]}


def matrix_cases():
    """~244 deterministic combinations covering group/metric, granularity/range, filters."""
    cases = []
    # Block A: group_by x metric coverage (day, full range, no filter)
    for gb in GROUP_BYS:
        for ms in METRIC_SETS:
            cases.append(_case(gb, ms, "day", {}, (FULL_START, FULL_END)))
    # Block B: granularity x range (subset of group/metric)
    for gran in GRANS:
        for rng in RANGES:
            for gb in (None, ["source"], ["country"]):
                for ms in (None, ["dau"], ["arpu"]):
                    cases.append(_case(gb, ms, gran, {}, rng))
    # Block C: filters x group/metric (day, full range)
    for filt in FILTERS:
        for gb in (None, ["source"], ["country"], ["source", "country"]):
            for ms in (None, ["dau"], ["arpu"], ["revenue"]):
                cases.append(_case(gb, ms, "day", filt, (FULL_START, FULL_END)))
    return cases
