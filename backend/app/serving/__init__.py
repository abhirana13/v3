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

from app.backpop.duckdb_writer import cache_columns, table_name
from app.connections import duckdb as duckdb_conn
from app.derived_dims import effective_dimensions
from app.formulas import eval_formula, validate_formula
from app.models import Chart
from app.schemas import DataRequest

_GRANULARITY = {"day", "week", "month"}


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


def _run_keyset(conn, chart: Chart, req: DataRequest, requested_dims: list[str]) -> list[tuple]:
    table = table_name(chart.id)
    time_col = chart.time_column
    dim_by_name = {d.name: d for d in effective_dimensions(chart, _columns(conn, table))}

    bucket_expr = (
        f"CAST(date_trunc('{req.granularity}', CAST({_q(time_col)} AS DATE)) AS DATE)"
    )
    select = [f"{bucket_expr} AS _t"]
    for d_name in requested_dims:
        select.append(_q(dim_by_name[d_name].column_name))

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
) -> dict[tuple, object]:
    """Return {(time_bucket, *eff_values): metric_value} for this metric."""
    table = table_name(chart.id)
    time_col = chart.time_column
    _eff = effective_dimensions(chart, _columns(conn, table))
    dim_by_name = {d.name: d for d in _eff}
    all_chart_dim_names = [d.name for d in _eff]

    indep = set(metric.independent_dimensions or [])
    grain_dim_names = [d for d in all_chart_dim_names if d not in indep]
    grain_cols = [dim_by_name[d].column_name for d in grain_dim_names]

    eff_dim_names = [d for d in requested_dims if d not in indep]
    eff_cols = [dim_by_name[d].column_name for d in eff_dim_names]

    day_expr = f"CAST({_q(time_col)} AS DATE)"
    bucket_expr = f"CAST(date_trunc('{req.granularity}', _day) AS DATE)"
    # Independent dims must not filter this metric (see module docstring).
    where, params = _build_where(req, time_col, dim_by_name, exclude_dims=indep)

    inner_select = ["_day"] + [_q(c) for c in grain_cols]
    inner_group = ["_day"] + [_q(c) for c in grain_cols]
    inner = (
        f"SELECT {', '.join(inner_select)}, "
        f"MAX({_q(metric.column_name)}) AS _grain "
        f"FROM (SELECT *, {day_expr} AS _day FROM {_q(table)} WHERE {where}) _base "
        f"GROUP BY {', '.join(inner_group)}"
    )

    outer_select = [f"{bucket_expr} AS _t"] + [_q(c) for c in eff_cols]
    outer_group = [bucket_expr] + [_q(c) for c in eff_cols]
    sql = (
        f"SELECT {', '.join(outer_select)}, SUM(_grain) AS _value "
        f"FROM ({inner}) _inner "
        f"GROUP BY {', '.join(outer_group)}"
    )

    rows = conn.execute(sql, params).fetchall()
    return {(r[0], *r[1:-1]): r[-1] for r in rows}


def latest_data_date(chart: Chart):
    """Max value of the time column in the chart's DuckDB table (or None)."""
    if not chart.time_column:
        return None
    table = table_name(chart.id)
    conn = duckdb_conn.get_connection()
    try:
        if not _table_exists(conn, table):
            return None
        r = conn.execute(
            f"SELECT MAX(CAST({_q(chart.time_column)} AS DATE)) FROM {_q(table)}"
        ).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def dimension_values(chart: Chart, from_date=None, to_date=None) -> dict:
    """Distinct values per dimension (for filter dropdowns) + the data's date
    extent (for defaulting the range picker). Read straight from DuckDB."""
    table = table_name(chart.id)
    conn = duckdb_conn.get_connection()
    try:
        if not _table_exists(conn, table):
            return {"dimensions": {d.name: [] for d in effective_dimensions(chart, set())}, "date_min": None, "date_max": None}

        where, params = "1=1", []
        tc = chart.time_column
        if tc and (from_date or to_date):
            clauses = []
            if from_date:
                clauses.append(f"CAST({_q(tc)} AS DATE) >= ?")
                params.append(from_date)
            if to_date:
                clauses.append(f"CAST({_q(tc)} AS DATE) <= ?")
                params.append(to_date)
            where = " AND ".join(clauses)

        # Per-dimension value ordering (see Dimension.value_order):
        #  - "metric": descending by the primary base metric's total (biggest first)
        #  - "natural" (default): number-aware label sort (D2-D7 < D8-D14 < D15-D30,
        #    alphabetical for non-numeric like platform)
        order_metric = next(
            (m for m in sorted(chart.metrics, key=lambda x: (x.display_order or 0)) if m.column_name),
            None,
        )

        def _metric_ordered(col: str) -> list | None:
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

        def _natural_ordered(col: str) -> list:
            rows = conn.execute(
                f"SELECT DISTINCT {_q(col)} AS v FROM {_q(table)} WHERE {where}",
                params,
            ).fetchall()
            return sorted((r[0] for r in rows if r[0] is not None), key=_natural_key)

        dims: dict[str, list] = {}
        for d in effective_dimensions(chart, _columns(conn, table)):
            vals = _metric_ordered(d.column_name) if d.value_order == "metric" else None
            dims[d.name] = vals if vals is not None else _natural_ordered(d.column_name)

        date_min = date_max = None
        if tc:
            r = conn.execute(
                f"SELECT MIN(CAST({_q(tc)} AS DATE)), MAX(CAST({_q(tc)} AS DATE)) "
                f"FROM {_q(table)}"
            ).fetchone()
            date_min, date_max = (r[0], r[1]) if r else (None, None)

        return {"dimensions": dims, "date_min": date_min, "date_max": date_max}
    finally:
        conn.close()


def serve_data(chart: Chart, req: DataRequest) -> dict:
    if not chart.time_column:
        raise ValueError("chart has no time_column configured")
    if req.granularity not in _GRANULARITY:
        raise ValueError(f"invalid granularity '{req.granularity}'")

    dim_by_name = {d.name: d for d in effective_dimensions(chart, cache_columns(chart.id))}
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

    conn = duckdb_conn.get_connection()
    try:
        if not _table_exists(conn, table):
            return {
                "chart_id": chart.id,
                "from_date": req.from_date,
                "to_date": req.to_date,
                "granularity": req.granularity,
                "dimensions": requested_dims,
                "metrics": requested_metrics,
                "rows": [],
                "row_count": 0,
            }

        keyset_rows = _run_keyset(conn, chart, req, requested_dims)
        metric_lookups: dict[str, dict] = {}
        for m_name in needed_base:
            metric_lookups[m_name] = _run_metric(
                conn, chart, req, metric_by_name[m_name], requested_dims
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
        out: dict = {chart.time_column: time_bucket}
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
            metric = metric_by_name[m_name]
            val = eval_formula(metric.formula, base_values)
            if val is not None:
                val = round(val, metric.decimals)
                if metric.decimals == 0:
                    val = int(val)
            out[m_name] = val
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
        "dimensions": requested_dims,
        "metrics": requested_metrics,
        "rows": output_rows,
        "row_count": len(output_rows),
    }
