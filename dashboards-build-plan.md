# Build Plan: Dashboards (v3 feature)

A dashboard is a **grid of widgets that reference existing charts** — number tiles (value + WoW /
vs-previous-day) and trend charts — grouped into tabs, with global filters and a global date range,
laid out in a **resizable, draggable grid**.

**v1 scope (locked):** Chart + Number widgets · multi-tab · resizable grid · global filters.
Table/Text widgets, sharing, and per-user layouts are later.

## The key idea: dashboards are composition, not new data
A widget does **not** run SQL or touch Redshift. It points at a **source chart** (e.g. `chart_579`)
and reads that chart's already-backpopulated DuckDB cache through the existing serving layer, applying
the widget's own metric selection / filters / grouping. So dashboards add **only metadata + serving
composition** — no new backpop, no Redshift load, and they render as fast as a single chart. This is
what makes the feature cheap and safe to add on top of what you already have.

---

## 1. Data model (Postgres metadata)

```
Dashboard
  id, name, number (searchable, like chart_number), enabled (bool)
  default_date_range_days (int), default_end_offset_days (int)
  created_at, updated_at
  (later: created_by)

DashboardTab            # "Main", "ROAS Deepdive", "Engagement Deepdive"
  id, dashboard_id (FK), name, display_order

DashboardFilter         # the global filter bar (gid, cli, country, install_type)
  id, dashboard_id (FK), dimension (str), default_values (JSON), display_order
  # cascades into every widget on every tab; widgets may narrow further

Widget
  id, tab_id (FK), source_chart_id (FK -> charts.id)
  type            # 'chart' | 'number'
  name            # "Daily Revenue (May Target: $24K)"
  layout (JSON)   # { x, y, w, h }  for the resizable grid
  config (JSON)   # type-specific, below
  display_order
```

**`config` for a `chart` widget**
```json
{
  "viz": "line",                      // line | bar
  "metrics": [{"name": "revenue", "y_axis": "primary"}],   // up to 5
  "filters": {"cli": ["Android"], "country": ["US"]},      // widget-level, on top of global
  "group_by": [],                     // up to 5 dims
  "offset_days": 2, "offset_mode": "only_on_end_date",
  "x_axis": "time",
  "y_axis": {"primary": {}, "secondary": {}},
  "target": 24000                     // optional; draws a target line / shows in title
}
```

**`config` for a `number` widget**
```json
{
  "metric": "revenue",
  "filters": {"cli": ["Android"]},
  "decimals": 2, "unit": "$",
  "compares": ["previous_day", "last_week"],   // which deltas to show
  "target": 24000                              // optional
}
```

## 2. Serving (reuse the chart cache)

- **Chart widget** → reuse the existing `/data` serving: given `source_chart_id`, merged filters
  (global ∪ widget), selected metrics, group-by, date range (global) and offset (widget), return the
  time series. Zero new query logic.
- **Number widget** → a small new serving function over the same cached series:
  - `value` = the metric at the effective as-of date (`end_date − offset`)
  - `previous_day` = `{abs: value − value(as_of − 1d), pct}`
  - `last_week` = `{abs: value − value(as_of − 7d), pct}`  (WoW = same weekday, 7 days prior)
  - respects the widget's filters and independent-metric / formula logic (it's the same series the
    chart would show).
- **Global cascade:** the dashboard's `DashboardFilter` selections + global date range are merged
  into each widget's request; a widget's own `filters` narrow further (never widen past the source
  chart's data).

**Endpoints**
```
POST/GET/PUT/DELETE  /dashboards[/{id}]
     GET             /dashboards/{id}            # full tree: tabs + widgets + layout + filters
     POST/PUT/DELETE  .../tabs[/{tab_id}]         # add / rename / reorder / delete tabs
     POST/PUT/DELETE  .../widgets[/{widget_id}]   # incl. layout (x,y,w,h) on save
     GET             /widgets/{id}/data          # resolves source chart + config + global -> data
     PUT             /tabs/{tab_id}/layout        # bulk-save positions/sizes after drag/resize
```

## 3. Frontend (resizable grid)

Use **react-grid-layout** for the widget grid — it gives drag-to-move, resize handles, and
responsive breakpoints out of the box; persist each widget's `{x,y,w,h}`. Charts reuse the existing
ECharts component; number tiles are a light custom component. Keep everything behind the `api-client`
layer and as **dumb components** (data in via props, actions out via callbacks) so it drops into
Claude Code cleanly.

Screens (design prompts in `dashboards-design-prompts.md`):
1. **Dashboard list** — browse/search dashboards, enabled state, open/replicate.
2. **Dashboard view (read)** — tabs, global filter bar, date range, resizable grid of chart + number
   widgets, per-widget filter/expand/info.
3. **Dashboard edit mode** — Add Widget (Chart/Number), drag/reposition/resize, per-widget gear,
   tab management, Save/Discard.
4. **Edit Widget modal** — Basic (source chart, name, viz), Metrics (+axis, up to 5), Dimensions
   (Filter By / Group By up to 5), Other Settings (offset, x-axis, y-axis ranges, target).

## 4. Build phases (backend first, then mutable frontend — per project rules)

1. **Model + CRUD** — dashboards, tabs, widgets, layout, global filters. Tests + Swagger.
2. **Chart-widget serving** — reuse `/data` with merged global+widget filters. Test the merge.
3. **Number-widget serving** — value + previous_day + last_week deltas. *Verify the WoW/prev-day math
   against hand calculations* — this is the correctness-sensitive bit.
4. **Global cascade** — filters + date range flow to all widgets; widget overrides. Tests.
5. **Frontend — dashboard view** (read): react-grid-layout, ChartWidget, NumberWidget, tabs, filter bar.
6. **Frontend — edit mode**: add/drag/resize/save, tab management.
7. **Frontend — Edit Widget modal**.
8. **Polish** — target lines/labels, expand-to-fullscreen, info tooltips, dashboard list, replicate.

## 5. Notes / guardrails
- **No new Redshift/backpop work** — widgets read cached chart data only; keep it that way.
- A widget can only show metrics/dimensions its **source chart** actually has — validate on save so a
  widget can't reference a metric the chart doesn't produce.
- **Number-widget correctness:** deltas are ratio-of-values at the two dates; reuse the chart's
  formula/independent-metric resolution so a number tile always matches the same number on its chart.
- Multi-user: reuse oauth2-proxy auth; dashboards are shared objects (add `created_by` with the
  broader audit work later). No new concurrency concerns — serving is read-only over the cache.
