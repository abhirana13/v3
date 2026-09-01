"""Widget serving — composition over the existing chart cache, zero new query logic.

A chart widget resolves to ONE serve_data() call on its source chart:
  - metrics / group_by come from the widget's config
  - filters are the merge of the dashboard's global bar state and the widget's own
    config filters (see merge_filters for the exact cascade rules)
  - the date window is the dashboard's global range, with the widget's offset
    capping the END date only (offset_mode 'only_on_end_date')

Because it IS serve_data, a widget inherits the independent-metric dedupe and
formula evaluation — a widget always shows the same numbers as its source chart.
"""

from datetime import date, timedelta

from app.backpop.duckdb_writer import cache_present_columns
from app.dashboards.models import Dashboard, Widget
from app.dashboards.schemas import ChartWidgetConfig, NumberWidgetConfig
from app.derived_dims import effective_dimensions
from app.models import Chart
from app.schemas import DataRequest
from app.serving import _natural_key, dimension_values, serve_data, usable_default_x_axis

# compare name -> how many days before the as-of date its reference value sits
# (last_week = same weekday, 7 days prior — the WoW convention)
_COMPARE_LOOKBACK = {"previous_day": 1, "last_week": 7}


def _today() -> date:
    return date.today()  # separated for deterministic tests


def merge_filters(chart: Chart, global_filters: dict, widget_filters: dict) -> tuple[dict, bool]:
    """Merge the dashboard's global filter state with a widget's own filters.

    Rules (the global cascade):
      - a global filter applies only if the source chart actually has that
        dimension (declared or currently-effective derived) — mixed-chart
        dashboards keep working, the filter just doesn't bind here;
      - the widget's own filters narrow further;
      - same dimension in both → intersection of the value sets (a widget can
        never WIDEN past the global selection).

    Returns (merged, empty_selection): empty_selection=True means an intersection
    came up empty — the correct result is NO rows (serve_data would silently drop
    an empty filter list, which would wrongly widen back to everything).
    """
    servable = {d.name for d in effective_dimensions(chart, cache_present_columns(chart))}

    empty_selection = False

    merged: dict[str, list] = {}
    for dim, values in (global_filters or {}).items():
        if dim not in servable:
            continue
        if not values:
            # An EXPLICITLY empty global selection means "none of this dimension's values",
            # i.e. no rows — the filter bar's None button. Previously any falsy list was just
            # skipped, which silently widened the selection back to everything, so None was
            # indistinguishable from All. The frontend only ever sends an empty list when the
            # user has actually cleared the chip (an untouched chip omits the key entirely).
            empty_selection = True
            continue
        merged[dim] = list(values)

    for dim, values in (widget_filters or {}).items():
        if not values:
            continue
        if dim in merged:
            allowed = set(merged[dim])
            merged[dim] = [v for v in values if v in allowed]
            if not merged[dim]:
                empty_selection = True
        else:
            merged[dim] = list(values)
    return merged, empty_selection


def resolve_window(
    dashboard: Dashboard,
    offset_days: int | None,
    from_date: date | None,
    to_date: date | None,
    global_offset: int | None = None,
) -> tuple[date, date]:
    """The widget's effective [from, to]. The offset (widget's own, else the
    dashboard default) caps the END date at today − offset ('only_on_end_date'
    mode: a requested from_date passes through untouched). With no dates given,
    the window is the dashboard's default_date_range_days ending at the cap."""
    # Precedence: the widget's own offset, then the viewer's global control, then the
    # dashboard's stored default. `global_offset` is what the dashboard's recency dropdown sends;
    # it REPLACES the stored default rather than capping on top of it, so the control can loosen
    # the window as well as tighten it. A widget with its own explicit offset still wins — that
    # is a deliberate per-widget setting, not a default.
    offset = (
        offset_days
        if offset_days is not None
        else global_offset
        if global_offset is not None
        else dashboard.default_end_offset_days
    )
    cap = _today() - timedelta(days=offset)
    to_eff = min(to_date, cap) if to_date else cap
    from_eff = from_date or (to_eff - timedelta(days=dashboard.default_date_range_days - 1))
    return from_eff, to_eff


def _dim_columns(chart: Chart, dim_names: list[str]) -> list[str]:
    """Row keys are COLUMN names while group_by uses dimension NAMES — hand the
    frontend the mapping so it never has to guess row-key order."""
    by_name = {d.name: d.column_name for d in effective_dimensions(chart, cache_present_columns(chart))}
    return [by_name.get(d, d) for d in dim_names]


def _dim_value_order(chart: Chart, dim_names: list[str]) -> list[str]:
    """Each grouped dimension's `value_order`, aligned with _dim_columns.

    The widget renderer needs this to order its series the way the chart page does. Without it
    a widget legend fell back to the order rows happened to arrive in — the backend's
    (time bucket, dim) ordering — so it read "D2-D7, D8-D14, D0, D1, D360+" while the same
    chart on its own page read D0, D1, D2-D7 ... That was the bug the chart page fixed in
    07ad67f; the widget renderer never got the information it needed to do the same.
    """
    by_name = {d.name: getattr(d, "value_order", "natural") for d in effective_dimensions(chart, cache_present_columns(chart))}
    return [by_name.get(d, "natural") for d in dim_names]


def _metric_format(chart: Chart, metric_names: list[str]) -> dict:
    """Each requested metric's display shape: {name: {"unit": .., "decimals": ..}}.

    A widget tooltip had no way to format its own values — it fell back to ECharts' default, so
    a revenue widget showed a raw float with no '$' while the same metric on the chart page was
    formatted from these exact fields. Keyed by name rather than positional so it cannot drift
    out of step with `metrics`.
    """
    by_name = {m.name: m for m in chart.metrics}
    out = {}
    for n in metric_names:
        m = by_name.get(n)
        if m is not None:
            out[n] = {"unit": m.unit, "decimals": m.decimals}
    return out


def _empty_response(chart: Chart, widget: Widget, from_date, to_date, granularity, dims, metrics, x_axis=None):
    # x_axis is reported even with no rows: the frontend builds the widget's "open the source
    # chart" link from this response, and omitting it made an empty widget link to a plain time
    # series even when the chart pivots — a link that quietly disagrees with the widget beside it.
    x_axis_col = _dim_columns(chart, [x_axis])[0] if x_axis else None
    return {
        "widget_id": widget.id,
        "chart_id": chart.id,
        "time_column": chart.time_column,
        "dimension_columns": _dim_columns(chart, dims),
        "dimension_value_order": _dim_value_order(chart, dims),
        "x_axis": x_axis,
        "x_axis_column": x_axis_col,
        "filters_effective": {},
        "metric_format": _metric_format(chart, metrics),
        "from_date": from_date,
        "to_date": to_date,
        "granularity": granularity,
        "dimensions": dims,
        "metrics": metrics,
        "rows": [],
        "row_count": 0,
    }


def chart_widget_data(
    dashboard: Dashboard,
    widget: Widget,
    chart: Chart,
    from_date: date | None,
    to_date: date | None,
    granularity: str,
    global_filters: dict,
    extra_group_by=(),
    global_offset: int | None = None,
) -> dict:
    cfg = ChartWidgetConfig.model_validate(widget.config)
    from_eff, to_eff = resolve_window(dashboard, cfg.offset_days, from_date, to_date, global_offset)
    merged, empty_selection = merge_filters(chart, global_filters, cfg.filters)
    metric_names = [m.name for m in cfg.metrics]

    # Global "split" cuts (dashboard filter-bar chips the viewer unchecked) add to
    # this widget's own group_by — but only dimensions the source chart actually has
    # (others simply don't apply here, same rule as filters). Deduped, widget's first.
    servable = {d.name for d in effective_dimensions(chart, cache_present_columns(chart))}
    group_by = list(cfg.group_by)
    for d in extra_group_by:
        if d in servable and d not in group_by:
            group_by.append(d)

    # A widget with no x_axis of its own (or the legacy "time" placeholder) INHERITS the chart's
    # saved axis, so a pivoted chart stays pivoted in a dashboard. This was hardcoded to a plain
    # time series: the same chart returned cohort buckets on its own page and dates in a widget.
    #
    # Resolved BEFORE the empty-selection return so the empty response reports the same axis a
    # populated one would; the deep link is built from this response either way.
    x_axis = (
        usable_default_x_axis(chart)
        if cfg.x_axis is None or cfg.x_axis == "time"
        else cfg.x_axis
    )

    if empty_selection:
        return _empty_response(
            chart, widget, from_eff, to_eff, granularity, group_by, metric_names, x_axis
        )

    req = DataRequest(
        from_date=from_eff,
        to_date=to_eff,
        granularity=granularity,
        x_axis=x_axis,
        dimensions=group_by,
        metrics=metric_names,
        filters=merged,
    )
    result = serve_data(chart, req)
    result["widget_id"] = widget.id
    result["time_column"] = chart.time_column
    # Derive from the EFFECTIVE dims serve_data grouped by, not from the widget's group_by:
    # when pivoting, serving prepends the x_axis dimension itself, so a widget with an empty
    # group_by still gets rows keyed on that dimension. Using group_by here reported no
    # dimension columns at all and the renderer had nothing to key rows on.
    eff_dims = result.get("dimensions") or []
    result["dimension_columns"] = _dim_columns(chart, eff_dims)
    result["dimension_value_order"] = _dim_value_order(chart, eff_dims)
    # The COLUMN the x-axis lives in (None => rows are keyed on time_column), so the frontend
    # never has to map dimension name -> column itself.
    result["x_axis_column"] = (
        _dim_columns(chart, [result["x_axis"]])[0] if result.get("x_axis") else None
    )
    result["metric_format"] = _metric_format(chart, metric_names)
    # The filters actually applied, after the global/widget cascade. Handed back so the
    # "open the source chart" link can reproduce EXACTLY these cuts — re-deriving the merge in
    # the frontend would duplicate merge_filters' intersection rule and drift from it.
    result["filters_effective"] = merged
    return result


def number_widget_data(
    dashboard: Dashboard,
    widget: Widget,
    chart: Chart,
    to_date: date | None,
    global_filters: dict,
    global_offset: int | None = None,
) -> dict:
    """value at the as-of date + deltas vs previous day and last week (same
    weekday, 7 days prior).

    All three values come from ONE serve_data() call over [as_of − 7, as_of] with
    time-only grouping — so the tile inherits the chart's independent-metric
    dedupe and formula evaluation, and always equals the number the source chart
    would plot for that day. Deltas: abs = v − v_prev, pct = (v/v_prev − 1)·100;
    null when either side is missing, pct also null when v_prev is 0."""
    cfg = NumberWidgetConfig.model_validate(widget.config)
    _, as_of = resolve_window(dashboard, cfg.offset_days, None, to_date, global_offset)
    # The cohort lag is SUBTRACTED from the resolved date, not folded into resolve_window's
    # min(). Folding it in is what made offset_days useless as a horizon: a cap can only pull
    # back from today, so with a picked end already in the past it did nothing.
    as_of = as_of - timedelta(days=cfg.anchor_lag_days or 0)
    merged, empty_selection = merge_filters(chart, global_filters, cfg.filters)

    out = {
        "widget_id": widget.id,
        "chart_id": chart.id,
        "metric": cfg.metric,
        "as_of_date": as_of,
        "value": None,
        "compares": {c: None for c in cfg.compares},
        # config echoed so the tile can format without a second fetch
        "unit": cfg.unit,
        "decimals": cfg.decimals,
        "target": cfg.target,
    }
    if empty_selection:
        return out

    # ---- WHAT `as_of` IS MATCHED AGAINST ----------------------------------------------------
    # Inherited from the chart's saved x_axis unless the widget overrides it (same rule as chart
    # widgets). None => the time column, i.e. the original behaviour.
    #
    # This is the fix for a cohort tile reading against the wrong date. A chart viewed against
    # install_date still produced a tile computed against event_date, because the tile keyed
    # rows on chart.time_column: numerator and denominator were aggregated over an event_date
    # containing every install cohort active that day, so the ratio belonged to nobody.
    anchor = (
        usable_default_x_axis(chart)
        if cfg.x_axis is None or cfg.x_axis == "time"
        else (None if cfg.x_axis == chart.time_column else cfg.x_axis)
    )

    lookback = max(_COMPARE_LOOKBACK.values())
    wanted = [as_of - timedelta(days=d) for d in range(0, lookback + 1)]

    if anchor is None:
        req = DataRequest(
            from_date=as_of - timedelta(days=lookback),
            to_date=as_of,
            granularity="day",
            dimensions=[],
            metrics=[cfg.metric],
            filters=merged,
        )
        key_col = chart.time_column
    else:
        # Anchored on a dimension. Two things change together:
        #
        #  * the ANCHOR values are pinned to the dates we need (as_of and its two comparison
        #    dates) as an explicit filter — serve_data's from/to always bounds the TIME column,
        #    so it cannot narrow a pivot dimension;
        #  * the TIME window is dropped entirely. This is the part that is easy to get wrong: a
        #    cohort's activity lands on event_dates AFTER its install date, so ANY window ending
        #    at as_of clips all of it and the tile reads None. The anchor filter is the
        #    constraint here; time is not. It stays cheap because that filter pins the scan to
        #    at most eight cohort dates.
        req = DataRequest(
            from_date=None,
            to_date=None,
            granularity="day",
            x_axis=anchor,
            dimensions=[],
            metrics=[cfg.metric],
            filters={**merged, anchor: [d.isoformat() for d in wanted]},
        )
        key_col = _dim_columns(chart, [anchor])[0]

    result = serve_data(chart, req)

    # The key may come back as a date or as a string depending on how the column was cached, so
    # normalise both sides to an ISO prefix rather than trusting the type.
    def _iso(v) -> str:
        return str(v)[:10] if v is not None else ""

    by_date = {_iso(row.get(key_col)): row.get(cfg.metric) for row in result["rows"]}
    out["anchored_on"] = anchor or chart.time_column

    value = by_date.get(_iso(as_of))
    out["value"] = value

    def _delta(ref_date: date) -> dict | None:
        prev = by_date.get(_iso(ref_date))
        if value is None or prev is None:
            return None
        return {
            "abs": value - prev,
            "pct": ((value / prev) - 1) * 100 if prev else None,
        }

    out["compares"] = {
        c: _delta(as_of - timedelta(days=_COMPARE_LOOKBACK[c])) for c in cfg.compares
    }
    return out


def filter_values(dashboard: Dashboard) -> dict:
    """Options for the global filter-bar chips: for each configured filter
    dimension, the distinct cached values UNIONED across every distinct source
    chart on the dashboard that has that dimension (charts without it simply
    don't contribute — same rule as the cascade itself).

    ORDER follows each dimension's own `value_order`. This used to collect into a set and then
    re-sort by _natural_key unconditionally, which discarded a 'metric' ordering entirely and
    made every dropdown alphabetical. It also disagreed with the dashboard's own EDIT mode,
    which builds its options from per-chart dimension_values and so kept `value_order` — so the
    same dropdown listed countries by volume while editing and alphabetically while viewing.
    See the note at the return for how the two orderings union differently.
    """
    wanted = [f.dimension for f in dashboard.filters]
    if not wanted:
        return {"values": {}}

    charts: dict[int, Chart] = {}
    for tab in dashboard.tabs:
        for w in tab.widgets:
            charts[w.source_chart_id] = w.source_chart

    merged: dict[str, list] = {d: [] for d in wanted}
    seen: dict[str, set] = {d: set() for d in wanted}
    order_of: dict[str, str] = {}
    for chart in charts.values():
        chart_vals = dimension_values(chart)["dimensions"]
        vo = {
            d.name: getattr(d, "value_order", "natural")
            for d in effective_dimensions(chart, cache_present_columns(chart))
        }
        for d in wanted:
            if d not in chart_vals:
                continue
            order_of.setdefault(d, vo.get(d, "natural"))
            for v in chart_vals[d]:
                if v not in seen[d]:
                    seen[d].add(v)
                    merged[d].append(v)

    # 'metric' order can only be honoured by keeping arrival order — the values come out of
    # dimension_values() biggest-first per chart, and there is no way to re-derive a combined
    # ranking here without re-querying every cache. 'natural' is re-sorted over the whole union
    # instead: sorting is well defined globally, and first-seen would otherwise give the odd
    # "chart 1's values in order, then chart 2's leftovers" for a plain alphabetical dimension.
    return {
        "values": {
            d: merged[d] if order_of.get(d) == "metric" else sorted(merged[d], key=_natural_key)
            for d in wanted
        }
    }
