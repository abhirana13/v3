"""Serve precomputed rows from DuckDB, correctly handling per-metric grains.

Each metric M has a grain = (chart dims) ∖ M.independent_dimensions. For a
request grouping G, M is aggregated by:

  1. INNER per day: GROUP BY (_day, M.grain) using MAX(metric_col).
     This collapses M's independent dims at the day level — picking a
     representative value (MAX) since by definition M has the same value
     across rows that differ only in its independent dims.
  2. OUTER per bucket: GROUP BY (time_bucket, M.effective) using SUM.
     M.effective = G ∖ M.independent_dimensions. The SUM aggregates over
     days in the bucket and over chart dims not in M.effective.

We run one query per metric (each at its own effective grain) plus a
keyset query for the requested (time, G) tuples, then pivot in Python.
Each metric's value is looked up by its effective key — independent
dims are dropped from the lookup so the same value is repeated across
those dims' values in the output.

Filtering follows the same rule: a filter on one of M's independent
dimensions is NOT applied when computing M. A value that by definition
doesn't depend on a dimension must not change when that dimension is
filtered — only the keyset (which rows appear) reflects the filter.

Note: aggregation across the time bucket is SUM (sum-of-daily). Custom
aggregations like AVG / COUNT-DISTINCT-across-days will come via formula
metrics (Phase 7).
"""

import re

from app.backpop.duckdb_writer import cache_present_columns, table_name
from app.connections import duckdb as duckdb_conn
from app.derived_dims import effective_dimensions
from app.formulas import eval_formula, validate_formula
from app.models import Chart
from app.schemas import DataRequest

_GRANULARITY = {"day", "week", "month"}

# Pivot (x-axis = a dimension) collapses the time axis into a single constant bucket so
# all rows fold together and the chosen dimension becomes the primary grouping key. The
# literal value is irrelevant (it's dropped from pivot output) — it only has to be
# identical in the keyset and per-metric queries so their lookup keys line up.
_CONST_BUCKET = "CAST('1970-01-01' AS DATE)"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _natural_key(value):
    """Human/number-aware sort key: splits a label into text+number chunks so
    'D2-D7' < 'D8-D14' < 'D15-D30' (not alphabetical), and non-numeric labels
    (e.g. platform) sort alphabetically."""
    parts = re.split(r"(\d+)", str(value))
    return [(0, int(p)) if p.isdigit() else (1, p.lower()) for p in parts if p != ""]


def _table_exists(conn, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            [table],
        ).fetchone()
        is not None
    )


def _columns(conn, table: str) -> set:
    """Column names present in the cache table (empty if it doesn't exist)."""
    if not _table_exists(conn, table):
        return set()
    return {r[1] for r in conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()}


def _column_types(conn, table: str) -> dict:
    """{column: declared DuckDB type} for the cache table."""
    if not _table_exists(conn, table):
        return {}
    return {r[1]: str(r[2]).upper() for r in conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()}


def _pivot_bucket(col: str, granularity: str, col_type: str | None) -> str:
    """How to SELECT a pivoted x-axis dimension, bucketed by the chosen granularity.

    Pivoting puts a dimension on the x-axis and collapses time to a constant, which left the
    Day/Week/Month selector with nothing to act on — it was silently inert on exactly the
    charts most likely to need it, a cohort retention chart pivoted on install_date. When the
    pivot dimension is itself a DATE, the granularity belongs to IT: weekly retention is the
    cohorts that installed that week, aggregated together.

    Only for genuine DATE/TIMESTAMP columns. A date held as VARCHAR is left alone rather than
    guessed at: grouping on a failed cast would collapse every row into one NULL bucket, which
    looks like data loss rather than an unsupported pivot.
    """
    if granularity == "day" or not col_type:
        return _q(col)
    if not (col_type.startswith("DATE") or col_type.startswith("TIMESTAMP")):
        return _q(col)
    return f"CAST(date_trunc('{granularity}', {_q(col)}) AS DATE)"


def _build_where(req: DataRequest, time_col: str, dim_by_name: dict, exclude_dims=frozenset()):
    parts: list[str] = []
    params: list = []
    if req.from_date:
        parts.append(f"CAST({_q(time_col)} AS DATE) >= ?")
        params.append(req.from_date)
    if req.to_date:
        parts.append(f"CAST({_q(time_col)} AS DATE) <= ?")
        params.append(req.to_date)
    for dim_name, values in req.filters.items():
        if not values:
            continue
        # A metric independent of a dimension ignores filters on it (caller passes
        # its independent dims as exclude_dims) — see module docstring.
        if dim_name in exclude_dims:
            continue
        col = dim_by_name[dim_name].column_name
        ph = ", ".join(["?"] * len(values))
        parts.append(f"{_q(col)} IN ({ph})")
        params.extend(values)
    return (" AND ".join(parts) if parts else "1=1", params)


def _run_keyset(
    conn, chart: Chart, req: DataRequest, requested_dims: list[str],
    collapse_time: bool = False,
) -> list[tuple]:
    table = table_name(chart.id)
    time_col = chart.time_column
    dim_by_name = {d.name: d for d in effective_dimensions(chart, _columns(conn, table))}

    bucket_expr = (
        _CONST_BUCKET if collapse_time
        else f"CAST(date_trunc('{req.granularity}', CAST({_q(time_col)} AS DATE)) AS DATE)"
    )
    # In pivot mode the FIRST requested dim is the x-axis (serve_data moves it there); when it
    # is a date column the granularity buckets it, since time itself has collapsed.
    types = _column_types(conn, table)
    x_name = requested_dims[0] if (collapse_time and requested_dims) else None
    select = [f"{bucket_expr} AS _t"]
    for d_name in requested_dims:
        col = dim_by_name[d_name].column_name
        select.append(
            _pivot_bucket(col, req.granularity, types.get(col)) if d_name == x_name else _q(col)
        )

    where, params = _build_where(req, time_col, dim_by_name)
    order = ", ".join(str(i + 1) for i in range(len(select)))
    sql = (
        f"SELECT DISTINCT {', '.join(select)} "
        f"FROM {_q(table)} WHERE {where} "
        f"ORDER BY {order}"
    )
    return conn.execute(sql, params).fetchall()


def _run_metric(
    conn,
    chart: Chart,
    req: DataRequest,
    metric,
    requested_dims: list[str],
    collapse_time: bool = False,
) -> dict[tuple, object]:
    """Return {(time_bucket, *eff_values): metric_value} for this metric.

    Thin wrapper over _run_metric_group, kept because a single metric is the clearer thing to
    read and to test against."""
    return _run_metric_group(
        conn, chart, req, [metric], requested_dims, collapse_time=collapse_time
    )[metric.name]


def _run_metric_group(
    conn,
    chart: Chart,
    req: DataRequest,
    metrics: list,
    requested_dims: list[str],
    collapse_time: bool = False,
) -> dict[str, dict[tuple, object]]:
    """Return {metric_name: {(time_bucket, *eff_values): value}} for METRICS THAT SHARE A GRAIN.

    Every metric passed here MUST have the same `independent_dimensions`. That is the whole
    precondition: the grain, the effective dimensions and the WHERE clause are all derived from
    that set, so metrics sharing it produce byte-identical SQL except for which column is wrapped
    in MAX(). Running them as one pass instead of N is worth real time — serving six metrics on
    the ads chart took 11.8s against 0.6s for one, for 14 KB of output, because each metric was a
    separate full pass over the cache.

    Metrics with DIFFERENT independence sets must NOT be batched: they dedup at different grains,
    and collapsing them into one GROUP BY is exactly the double-count this module exists to stop.
    """
    if not metrics:
        return {}
    indep_sets = {tuple(sorted(m.independent_dimensions or [])) for m in metrics}
    if len(indep_sets) != 1:
        raise ValueError(
            "_run_metric_group requires one shared independent_dimensions set, got "
            f"{sorted(indep_sets)}"
        )
    table = table_name(chart.id)
    time_col = chart.time_column
    _eff = effective_dimensions(chart, _columns(conn, table))
    dim_by_name = {d.name: d for d in _eff}
    all_chart_dim_names = [d.name for d in _eff]

    indep = set(metrics[0].independent_dimensions or [])
    grain_dim_names = [d for d in all_chart_dim_names if d not in indep]
    grain_cols = [dim_by_name[d].column_name for d in grain_dim_names]

    eff_dim_names = [d for d in requested_dims if d not in indep]
    eff_cols = [dim_by_name[d].column_name for d in eff_dim_names]

    day_expr = f"CAST({_q(time_col)} AS DATE)"
    bucket_expr = (
        _CONST_BUCKET if collapse_time
        else f"CAST(date_trunc('{req.granularity}', _day) AS DATE)"
    )
    # Independent dims must not filter this metric (see module docstring).
    where, params = _build_where(req, time_col, dim_by_name, exclude_dims=indep)

    # One MAX per metric, positionally aliased so a metric column named like a dimension
    # cannot collide with one.
    grain_exprs = [f"MAX({_q(m.column_name)}) AS _grain{i}" for i, m in enumerate(metrics)]
    inner_select = ["_day"] + [_q(c) for c in grain_cols]
    inner_group = ["_day"] + [_q(c) for c in grain_cols]
    inner = (
        f"SELECT {', '.join(inner_select)}, {', '.join(grain_exprs)} "
        f"FROM (SELECT *, {day_expr} AS _day FROM {_q(table)} WHERE {where}) _base "
        f"GROUP BY {', '.join(inner_group)}"
    )

    # The x-axis dimension is bucketed in the OUTER query only. The inner MAX must stay at the
    # finest grain (raw install_date, not its week) or the dedup would take one maximum across
    # a whole week and silently undercount — the independent-metric rule this module exists for.
    types = _column_types(conn, table)
    x_name = requested_dims[0] if (collapse_time and requested_dims) else None
    x_col = dim_by_name[x_name].column_name if x_name else None
    def _out(col: str) -> str:
        return _pivot_bucket(col, req.granularity, types.get(col)) if col == x_col else _q(col)

    outer_select = [f"{bucket_expr} AS _t"] + [_out(c) for c in eff_cols]
    # In pivot mode the bucket is a constant literal, so group only by the effective dims
    # (may be empty → one total row); a constant literal in SELECT needs no GROUP BY entry.
    group_cols = (
        [_out(c) for c in eff_cols] if collapse_time
        else [bucket_expr] + [_q(c) for c in eff_cols]
    )
    group_clause = f" GROUP BY {', '.join(group_cols)}" if group_cols else ""
    value_exprs = [f"SUM(_grain{i}) AS _value{i}" for i in range(len(metrics))]
    sql = (
        f"SELECT {', '.join(outer_select)}, {', '.join(value_exprs)} "
        f"FROM ({inner}) _inner"
        f"{group_clause}"
    )

    rows = conn.execute(sql, params).fetchall()
    n = len(metrics)
    out: dict[str, dict[tuple, object]] = {m.name: {} for m in metrics}
    for r in rows:
        key = (r[0], *r[1:-n])
        for i, m in enumerate(metrics):
            out[m.name][key] = r[len(r) - n + i]
    return out


def latest_data_date(chart: Chart):
    """Max value of the time column in the chart's DuckDB table (or None)."""
    if not chart.time_column:
        return None
    table = table_name(chart.id)
    conn = duckdb_conn.get_connection(chart.id, read_only=True)
    try:
        if not _table_exists(conn, table):
            return None
        r = conn.execute(
            f"SELECT MAX(CAST({_q(chart.time_column)} AS DATE)) FROM {_q(table)}"
        ).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def dimension_values(chart: Chart, from_date=None, to_date=None, filters: dict | None = None) -> dict:
    """Distinct values per dimension (for filter dropdowns) + the data's date
    extent (for defaulting the range picker). Read straight from DuckDB.

    `filters` NARROWS WHICH VALUES ARE OFFERED. Without it the dropdowns list every value in
    the whole cache, which is wrong as soon as a chart holds several independent populations:
    the experiment console pins one experiment, and the arm chip was still offering `treatment`,
    `variant_a` and `varient1` — arms belonging to other experiments entirely, that select
    nothing. Same for assignment builds.

    A dimension is never filtered BY ITSELF. Narrowing the arm list by the arm already chosen
    would leave the other arms unreachable, so a viewer who picked one could not get back —
    the usual cascading-filter trap. Callers are expected to pass only the cuts that are FIXED
    for the view (the console passes its forced experiment/game/platform), not the viewer's own
    in-progress selections.
    """
    table = table_name(chart.id)
    conn = duckdb_conn.get_connection(chart.id, read_only=True)
    try:
        if not _table_exists(conn, table):
            return {"dimensions": {d.name: [] for d in effective_dimensions(chart, set())}, "date_min": None, "date_max": None}

        dim_by_name = {d.name: d for d in effective_dimensions(chart, _columns(conn, table))}
        tc = chart.time_column

        def _scope(exclude_dim: str | None) -> tuple[str, list]:
            clauses: list[str] = []
            params: list = []
            if tc and from_date:
                clauses.append(f"CAST({_q(tc)} AS DATE) >= ?")
                params.append(from_date)
            if tc and to_date:
                clauses.append(f"CAST({_q(tc)} AS DATE) <= ?")
                params.append(to_date)
            for name, values in (filters or {}).items():
                if name == exclude_dim:
                    continue
                dim = dim_by_name.get(name)
                if dim is None:
                    continue
                if not values:
                    # An explicitly empty selection means "none of this dimension", which
                    # leaves nothing to offer — not "everything", which is what dropping the
                    # clause would quietly mean.
                    clauses.append("1=0")
                    continue
                clauses.append(f"{_q(dim.column_name)} IN ({', '.join('?' * len(values))})")
                params.extend(values)
            return (" AND ".join(clauses) or "1=1"), params

        # Per-dimension value ordering (see Dimension.value_order):
        #  - "metric": descending by the primary base metric's total (biggest first)
        #  - "natural" (default): number-aware label sort (D2-D7 < D8-D14 < D15-D30,
        #    alphabetical for non-numeric like platform)
        order_metric = next(
            (m for m in sorted(chart.metrics, key=lambda x: (x.display_order or 0)) if m.column_name),
            None,
        )

        def _metric_ordered(col: str, where: str, params: list) -> list | None:
            if order_metric is None:
                return None
            try:
                rows = conn.execute(
                    f"SELECT {_q(col)} AS v, "
                    f"SUM(TRY_CAST({_q(order_metric.column_name)} AS DOUBLE)) AS tot "
                    f"FROM {_q(table)} WHERE {where} "
                    f"GROUP BY 1 ORDER BY tot DESC NULLS LAST, v",
                    params,
                ).fetchall()
                return [r[0] for r in rows if r[0] is not None]
            except Exception:
                return None  # bad/missing column type → caller falls back to natural

        def _natural_ordered(col: str, where: str, params: list) -> list:
            rows = conn.execute(
                f"SELECT DISTINCT {_q(col)} AS v FROM {_q(table)} WHERE {where}",
                params,
            ).fetchall()
            return sorted((r[0] for r in rows if r[0] is not None), key=_natural_key)

        dims: dict[str, list] = {}
        for d in dim_by_name.values():
            where, params = _scope(d.name)
            vals = _metric_ordered(d.column_name, where, params) if d.value_order == "metric" else None
            dims[d.name] = vals if vals is not None else _natural_ordered(d.column_name, where, params)

        date_min = date_max = None
        if tc:
            # Scoped by the same fixed cuts (minus the date bounds themselves, which are what
            # this is measuring), so a pinned experiment's picker opens on ITS window rather
            # than on the extent of every experiment in the cache.
            extent_clauses, extent_params = [], []
            for name, values in (filters or {}).items():
                dim = dim_by_name.get(name)
                if dim is None or not values:
                    continue
                extent_clauses.append(f"{_q(dim.column_name)} IN ({', '.join('?' * len(values))})")
                extent_params.extend(values)
            extent_where = " AND ".join(extent_clauses) or "1=1"
            r = conn.execute(
                f"SELECT MIN(CAST({_q(tc)} AS DATE)), MAX(CAST({_q(tc)} AS DATE)) "
                f"FROM {_q(table)} WHERE {extent_where}",
                extent_params,
            ).fetchone()
            date_min, date_max = (r[0], r[1]) if r else (None, None)

        return {"dimensions": dims, "date_min": date_min, "date_max": date_max}
    finally:
        conn.close()


def usable_default_x_axis(chart) -> str | None:
    """The chart's saved x_axis, but only while it still points at an existing dimension.

    Returns None (=> plain time series) when the saved dimension is GONE (e.g. a query edit
    dropped it), so a stale default can't make the chart unopenable. This guard is load-bearing:
    chart 17 shipped with x_axis='installs', a METRIC name, and was silently rendering as a time
    series rather than erroring.

    `included` is deliberately NOT required: excluding a dimension only hides it from the
    chart's filter chips, it stays valid as the x-axis. That's the point for a high-cardinality
    date dimension like install_date — you never want to pick cohorts from a dropdown (the
    date picker drives the range), but you do want to plot against them.
    """
    if not chart.x_axis or chart.x_axis == chart.time_column:
        return None
    from app.derived_dims import DERIVED_NAMES

    if chart.x_axis in DERIVED_NAMES:
        return chart.x_axis
    return chart.x_axis if chart.x_axis in {d.name for d in chart.dimensions} else None


def serve_data(chart: Chart, req: DataRequest) -> dict:
    if not chart.time_column:
        raise ValueError("chart has no time_column configured")
    if req.granularity not in _GRANULARITY:
        raise ValueError(f"invalid granularity '{req.granularity}'")

    # Postgres-mirrored column list: this runs BEFORE the serving connection is opened
    # below, so calling it would be a second, separate DuckDB open per request.
    dim_by_name = {d.name: d for d in effective_dimensions(chart, cache_present_columns(chart))}
    metric_by_name = {m.name: m for m in chart.metrics}

    requested_dims = list(dim_by_name) if req.dimensions is None else req.dimensions
    requested_metrics = list(metric_by_name) if req.metrics is None else req.metrics

    for d in requested_dims:
        if d not in dim_by_name:
            raise ValueError(f"unknown dimension '{d}'")
    for m in requested_metrics:
        if m not in metric_by_name:
            raise ValueError(f"unknown metric '{m}'")
    for d in req.filters:
        if d not in dim_by_name:
            raise ValueError(f"unknown filter dimension '{d}'")

    # x-axis: None or the chart's time column => normal time series. A dimension name =>
    # pivot: that dimension becomes the primary grouping key and time collapses to a filter
    # (the same aggregation + independent-metric dedup runs, just keyed on the dimension
    # instead of the time bucket — see _CONST_BUCKET). The x-axis dim leads requested_dims.
    x_axis = req.x_axis
    if x_axis == chart.time_column or x_axis == "":
        x_axis = None
    if x_axis is not None and x_axis not in dim_by_name:
        raise ValueError(f"unknown x_axis dimension '{x_axis}'")
    collapse_time = x_axis is not None
    if collapse_time:
        requested_dims = [x_axis] + [d for d in requested_dims if d != x_axis]

    # Split requested metrics into base (DuckDB-backed) and formula (derived).
    base_metric_names = {m for m in metric_by_name if not metric_by_name[m].formula}
    requested_formulas = [m for m in requested_metrics if metric_by_name[m].formula]
    requested_base = [m for m in requested_metrics if not metric_by_name[m].formula]

    # A formula needs its referenced base metrics computed even if the caller
    # didn't request them. They're computed but only emitted if also requested.
    formula_refs: dict[str, set[str]] = {}
    needed_base = set(requested_base)
    for fm in requested_formulas:
        refs = validate_formula(metric_by_name[fm].formula, base_metric_names)
        formula_refs[fm] = refs
        needed_base |= refs

    table = table_name(chart.id)
    output_rows: list[dict] = []

    conn = duckdb_conn.get_connection(chart.id, read_only=True)
    try:
        if not _table_exists(conn, table):
            return {
                "chart_id": chart.id,
                "from_date": req.from_date,
                "to_date": req.to_date,
                "granularity": req.granularity,
                "x_axis": x_axis,
                "dimensions": requested_dims,
                "metrics": requested_metrics,
                "rows": [],
                "row_count": 0,
            }

        keyset_rows = _run_keyset(conn, chart, req, requested_dims, collapse_time=collapse_time)

        # ONE QUERY PER GRAIN, not per metric. Metrics are grouped by their
        # independent_dimensions set, because that set is the only thing that changes the SQL —
        # everything else (the inner MAX grain, the effective dims, the WHERE exclusions) is
        # derived from it. Six metrics on the ads chart used to mean six full passes over the
        # cache: 11.8s against 0.6s for one, to return 14 KB. Grouped, that chart needs three
        # passes, Revenue two, and Session Time one.
        #
        # Metrics with DIFFERENT independence sets stay in separate groups on purpose: they
        # dedup at different grains, and merging them into one GROUP BY is the double-count
        # this module exists to prevent.
        by_grain: dict[tuple, list] = {}
        for m_name in needed_base:
            m = metric_by_name[m_name]
            by_grain.setdefault(tuple(sorted(m.independent_dimensions or [])), []).append(m)
        metric_lookups: dict[str, dict] = {}
        for group in by_grain.values():
            metric_lookups.update(
                _run_metric_group(
                    conn, chart, req, group, requested_dims, collapse_time=collapse_time
                )
            )
    finally:
        conn.close()

    def _eff_key(m_name: str, time_bucket, req_dim_values: list) -> tuple:
        indep = set(metric_by_name[m_name].independent_dimensions or [])
        eff_values = tuple(
            req_dim_values[i]
            for i, d_name in enumerate(requested_dims)
            if d_name not in indep
        )
        return (time_bucket, *eff_values)

    for kr in keyset_rows:
        time_bucket = kr[0]
        req_dim_values = list(kr[1:])
        # In pivot mode the x-axis is the chosen dimension (it leads requested_dims); the
        # collapsed time bucket is a meaningless constant, so it's omitted from the row.
        out: dict = {} if collapse_time else {chart.time_column: time_bucket}
        for i, d_name in enumerate(requested_dims):
            out[dim_by_name[d_name].column_name] = req_dim_values[i]

        # Resolve every needed base value at its own effective grain.
        base_values = {
            m_name: metric_lookups[m_name].get(
                _eff_key(m_name, time_bucket, req_dim_values)
            )
            for m_name in needed_base
        }
        for m_name in requested_base:
            out[m_name] = base_values[m_name]
        for m_name in requested_formulas:
            # FULL PRECISION, deliberately. `decimals` is a DISPLAY setting and rounding here
            # destroyed the value for everyone downstream — the API, the CSV export, and every
            # chart. It hit formula metrics only (base metrics were always passed through raw),
            # and the damage scaled inversely with the ratio's size, because `decimals` is an
            # absolute number of places rather than significant figures. Measured on chart 13's
            # `Actions/dau` (decimals=2) over 2026-07-01..20, distinct values surviving:
            #
            #   true ratio < 0.05   67 distinct -> 6     9% kept
            #   true ratio < 0.5   250 distinct -> 50   20% kept
            #   true ratio >= 1  1,980 distinct -> 1,814  92% kept
            #
            # So a rate that lives near 0.02-0.04 (ad revenue per DAU, say) had about three
            # reachable values and plotted as a flat stepped line. Worse, decimals=0 also did
            # int(), so a ratio of 3.7 served as 3.
            #
            # Formatting belongs at the edge: the chart's fmtVal and the widgets' compactNum
            # both already apply the metric's unit and decimals at render time.
            out[m_name] = eval_formula(metric_by_name[m_name].formula, base_values)
        output_rows.append(out)

    if req.hide_zero and requested_metrics:
        output_rows = [
            r
            for r in output_rows
            if any((r.get(m) or 0) != 0 for m in requested_metrics)
        ]

    return {
        "chart_id": chart.id,
        "from_date": req.from_date,
        "to_date": req.to_date,
        "granularity": req.granularity,
        "x_axis": x_axis,
        "dimensions": requested_dims,
        "metrics": requested_metrics,
        "rows": output_rows,
        "row_count": len(output_rows),
    }
