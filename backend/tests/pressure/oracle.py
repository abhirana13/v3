"""Independent reference implementation of the serving semantics.

Deliberately written from scratch with plain dict/list aggregation — it shares
NO code with app.serving and issues no SQL. Its job is to re-derive every
expected number from the raw golden rows so the optimized DuckDB path can be
checked against a second, obviously-correct implementation.

Mirrors app.serving.serve_data's contract:
  - per-metric grain = chart_dims  -  metric.independent_dimensions
  - inner: representative value per (day, grain-tuple)   [dedup of independent dims]
  - outer: SUM over (bucket, effective-tuple), effective = requested - independent
  - pivot: one row per requested (bucket, dim-tuple); each metric looked up by its
    effective key, so independent dims drop out and the value repeats across them
  - formulas evaluated AFTER aggregation (ratio-of-sums); None/zero -> None
  - hide_zero drops rows whose every requested metric is 0/None
"""

from datetime import date, timedelta

from app.formulas import eval_formula


def bucketize(d: date, granularity: str) -> date:
    if granularity == "day":
        return d
    if granularity == "week":  # Monday-start, matches DuckDB date_trunc('week')
        return d - timedelta(days=d.weekday())
    if granularity == "month":
        return d.replace(day=1)
    raise ValueError(granularity)


def _max_ignoring_none(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def oracle_serve(columns, rows, config, request: dict) -> list[dict]:
    """request keys: from_date, to_date, granularity, dimensions (list|None),
    metrics (list|None), filters (dict), hide_zero (bool)."""
    idx = {c: i for i, c in enumerate(columns)}
    tcol = config.time_column
    all_dims = list(config.dims)
    base_by_name = {m.name: m for m in config.base_metrics}
    formula_by_name = {f.name: f for f in config.formulas}

    granularity = request.get("granularity", "day")
    frm = request.get("from_date")
    to = request.get("to_date")
    filters = request.get("filters") or {}

    requested_dims = all_dims if request.get("dimensions") is None else list(request["dimensions"])
    if request.get("metrics") is None:
        requested_metrics = config.all_metric_names
    else:
        requested_metrics = list(request["metrics"])

    # ---- filter raw rows ----
    filt = []
    for r in rows:
        d = r[idx[tcol]]
        if frm and d < frm:
            continue
        if to and d > to:
            continue
        ok = True
        for dname, vals in filters.items():
            if vals and r[idx[dname]] not in vals:
                ok = False
                break
        if ok:
            filt.append(r)

    # ---- which base metrics must be computed ----
    requested_base = [m for m in requested_metrics if m in base_by_name]
    requested_formulas = [m for m in requested_metrics if m in formula_by_name]
    needed_base = set(requested_base)
    for fm in requested_formulas:
        needed_base |= eval_formula_refs(formula_by_name[fm].formula)

    # ---- per base metric: {(bucket, *eff_values): value} ----
    lookups = {}
    for mname in needed_base:
        m = base_by_name[mname]
        indep = set(m.independent)
        grain = [d for d in all_dims if d not in indep]
        eff = [d for d in requested_dims if d not in indep]

        # inner: representative (MAX, ignoring NULL) per (day, grain-tuple)
        inner = {}
        for r in filt:
            day = r[idx[tcol]]
            gkey = tuple(r[idx[g]] for g in grain)
            v = r[idx[m.column_name]]
            key = (day, gkey)
            inner[key] = _max_ignoring_none(inner.get(key), v) if key in inner else v

        # outer: SUM over days-in-bucket and grain-dims not in eff; NULL only if all NULL
        outer = {}  # key -> [sum, saw_value]
        grain_pos = {g: i for i, g in enumerate(grain)}
        for (day, gkey), v in inner.items():
            bucket = bucketize(day, granularity)
            eff_values = tuple(gkey[grain_pos[e]] for e in eff)
            k = (bucket, *eff_values)
            acc = outer.setdefault(k, [0, False])
            if v is not None:
                acc[0] += v
                acc[1] = True
        lookups[mname] = {k: (acc[0] if acc[1] else None) for k, acc in outer.items()}

    # ---- keyset: distinct (bucket, requested dim values) ----
    keyset = set()
    for r in filt:
        bucket = bucketize(r[idx[tcol]], granularity)
        keyset.add((bucket, tuple(r[idx[d]] for d in requested_dims)))

    # ---- build rows ----
    out = []
    for bucket, dvals in keyset:
        row = {tcol: bucket}
        for d, val in zip(requested_dims, dvals):
            row[d] = val  # in golden, dim name == column_name
        base_values = {}
        for mname in needed_base:
            indep = set(base_by_name[mname].independent)
            eff = [d for d in requested_dims if d not in indep]
            eff_values = tuple(dvals[requested_dims.index(e)] for e in eff)
            base_values[mname] = lookups[mname].get((bucket, *eff_values))
        for mname in requested_base:
            row[mname] = base_values[mname]
        for fm in requested_formulas:
            f = formula_by_name[fm]
            val = eval_formula(f.formula, base_values)
            if val is not None:
                val = round(val, f.decimals)
                if f.decimals == 0:
                    val = int(val)
            row[fm] = val
        out.append(row)

    if request.get("hide_zero") and requested_metrics:
        out = [r for r in out if any((r.get(m) or 0) != 0 for m in requested_metrics)]

    return out


def eval_formula_refs(formula: str) -> set:
    """Names referenced by a formula (independent re-parse, mirrors validate)."""
    import ast

    refs = set()
    for node in ast.walk(ast.parse(formula, mode="eval")):
        if isinstance(node, ast.Name):
            refs.add(node.id)
    return refs
