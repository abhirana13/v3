# Build Plan: Self-Hosted SQL → Chart Analytics Dashboard

A custom replacement for Metabase, modeled on the query-to-chart tool you used before.
You write SQL, the system precomputes ("backpopulates") aggregates on a schedule, and the
frontend lets you slice by dimensions, pick metrics, and build formula metrics — instantly.

**Decisions locked in for v1**

- Stack: **Python (FastAPI) backend + React frontend**
- Data model: **backpopulation / precompute** (batch queries → fast local store → frontend slices it)
- Scope: **single user / internal, no auth in v1**
- Deploy: **local / single server via Docker Compose**

---

## 1. How it connects to Redshift (your question)

You don't need any special software or paid middleware. Redshift is PostgreSQL wire-compatible,
so the backend just needs:

1. **A driver/library** — Amazon's `redshift_connector` (recommended) or `psycopg2` /
   SQLAlchemy with the `redshift+redshift_connector` dialect.
2. **Network access** — the machine running the backend must be able to reach the cluster.
   In practice one of:
   - Add the server's IP to the Redshift cluster's **VPC security group** (inbound 5439), or
   - Run the backend **inside the same AWS VPC**, or
   - Use an **SSH bastion / tunnel** if the cluster is private.
3. **Read-only credentials** — create a Redshift user with `SELECT` only, stored in a `.env`
   file (never in code).

That's the whole connection story. The cluster endpoint, port (5439), DB name, user, and
password go in environment variables; the backend opens a pooled, read-only connection.

---

## 2. The core idea (why it's fast)

Metabase is slow because it runs your SQL against Redshift live, every time you look at a chart.
Your old tool was fast because it **precomputed**:

1. Your query returns rows at the **finest grain** — one row per `(time × every dimension)` with
   the metric values.
2. A scheduler runs the query in **date batches** and stores those rows in a fast **local store**.
3. When you open a chart and pick filters/groupings, the frontend asks the backend to
   **re-aggregate the already-stored rows** (sum/dedupe) — milliseconds, no Redshift hit.

So Redshift is touched only on the backpopulation schedule, not on every page view.

---

## 3. Architecture overview

```
                ┌──────────────────────────────────────────────┐
                │                  Frontend (React)             │
                │  Query editor · Chart view · Metric settings  │
                └───────────────┬──────────────────────────────┘
                                │ REST / JSON
                ┌───────────────▼──────────────────────────────┐
                │              Backend API (FastAPI)            │
                │  - Chart/metric config CRUD                   │
                │  - Query templating + schema introspection    │
                │  - Serving API (slice/aggregate cached data)  │
                └───────┬───────────────────────┬──────────────┘
                        │                        │
            ┌───────────▼─────────┐   ┌──────────▼───────────┐
            │  Metadata DB         │   │  Cache / aggregate    │
            │  (Postgres)          │   │  store (DuckDB)       │
            │  chart configs,      │   │  precomputed rows     │
            │  metrics, formulas   │   │  per chart            │
            └──────────────────────┘   └──────────▲───────────┘
                                                   │ writes
                        ┌──────────────────────────┴───────────┐
                        │   Backpopulation worker (scheduler)   │
                        │   batched queries → Redshift → cache  │
                        └──────────────────┬────────────────────┘
                                           │ read-only SELECT
                                  ┌────────▼─────────┐
                                  │     Redshift     │
                                  └──────────────────┘
```

**Why these stores:**

- **Postgres** for *metadata* (chart definitions, metric configs, formulas, schedule state) —
  relational, reliable, easy to back up.
- **DuckDB** for the *precomputed aggregate data* — an embedded columnar engine that aggregates
  (`GROUP BY`, `SUM`) over millions of rows in milliseconds. This is what makes the UI feel
  instant. One DuckDB file (or one table per chart). For a single-server v1 it's ideal; if you
  later need many concurrent writers you can swap it for ClickHouse without changing the API shape.

---

## 4. Data / metric model (the tricky part)

This is where most of the design effort goes, and it's what made your old tool good.

### Dimensions ("cuts") and metrics
After you write a query and hit **Generate Dims and Metrics**, the backend runs the query with a
small sample (or `LIMIT 0` to read column types) and classifies columns:

- **Dimensions** = the cut columns (`gid`, `cli`, `country`, `source`, `country_bucket`, …) + the
  time column.
- **Metrics** = the numeric measures (`revenue`, `dau`, …).

You confirm/adjust the classification, set the **time column** and **date format**.

### Independent metrics — the key concept
A metric can be **independent of a dimension**. Example: `dau` (daily active users) is the same
number regardless of how you split by `source`. If you naively `SUM(dau)` across `source`, you'd
**multiply-count** users.

The model handles this by giving every metric its own **grain** — the set of dimensions it is
actually defined over:

- `revenue` is defined over *all* dimensions → it sums freely across any cut.
- `dau` is **independent of `source` / `source_category`** → its grain excludes those. When a view
  groups by `source`, `dau` is **not** summed across source; it's shown deduplicated (the total),
  repeated per source row, or rolled up — per your config.

Implementation: during backpopulation, store each metric pre-aggregated **at its own grain** (a
separate small table per metric keyed by `time + the dimensions it depends on`). At serve time,
for a requested grouping `G`:

- For each metric, aggregate from its grain table, summing only over `(metric grain ∖ G)`.
- For dimensions in `G` that the metric is independent of, **don't sum** — repeat/total instead.

This is exactly the "make a metric independent of a cut" feature, done correctly.

### Formula metrics (BODMAS on metrics)
Derived metrics like `Revenue per DAU = [revenue] / [dau]` are **computed at serve time**, after
base metrics are aggregated to the current view — never precomputed (because a ratio of sums ≠ sum
of ratios). Store the formula as an expression string, parse it safely into an AST (e.g. a small
expression evaluator — no `eval()`), and evaluate per time bucket. Supports `+ - * /` and
parentheses over metric references, plus per-metric Y-axis (primary/secondary), decimal places,
and unit formatting.

### Query templating + batching
Your query uses template variables like `{LOC_GID_RANGE}`, `{TIER1_COUNTRIES}`,
`{CUR_DATE_HIPHEN}`. The system needs a substitution layer:

- **Static variables** — named lists/ranges with default values, editable in the UI.
- **Date variables** — `{CUR_DATE_HIPHEN}` etc. are filled by the backpopulation engine **per
  batch**, so a "last 120 days" range gets split into N-day batches (your "Backpopulation Batch
  Size") and each batch substitutes its own date window.

---

## 5. Backpopulation engine

A scheduled worker that keeps the cache fresh.

- **Scheduler**: APScheduler (in-process, simplest) or Celery + beat (if you later want a
  separate worker process / retries at scale). Start with APScheduler.
- **Per chart config** (matches your screenshots): `refresh_interval` (Daily), `default
  backpopulation days` (e.g. 15), `backpopulation batch size` (e.g. 75 / 30), `default date
  range`, `{CUR_DATE_HIPHEN} behaviour` (batched query for N days), `chart cache` strategy
  (append vs replace on backpopulation).
- **Flow**:
  1. Determine the date range to fill (default backpop days + any missing days).
  2. Split into batches of `batch_size` days.
  3. For each batch: substitute variables → run read-only against Redshift → write rows into the
     chart's DuckDB table (and the per-metric grain tables).
  4. On the refresh schedule, re-run recent days and **append or replace** per the cache strategy.
- **State tracking**: a `backpop_runs` table in Postgres records which date ranges are filled,
  status, row counts, errors — so reruns are idempotent and you can see freshness in the UI.

---

## 6. Backend (FastAPI) — modules

- `connections/` — Redshift read-only pool; DuckDB handle; Postgres (SQLAlchemy) session.
- `templating/` — variable registry + safe substitution + date-batch expansion.
- `introspection/` — run query sample → infer columns → propose dims/metrics ("Generate Dims and
  Metrics").
- `backpop/` — scheduler, batch runner, cache writer, run-state.
- `serving/` — the hot path: given `chart_id`, selected dimension filters, grouping, metric list,
  date range, granularity → query DuckDB, apply independent-metric logic, compute formula metrics,
  return time-series JSON.
- `crud/` — charts, metrics, formulas, variables.
- `api/` — REST endpoints (see below).

**Key endpoints (v1)**

```
POST /charts                      create chart (name, source, query, schedule cfg)
POST /charts/{id}/introspect      run sample → return proposed dims/metrics
PUT  /charts/{id}/dims-metrics    save dim/metric config (incl. independent fields)
POST /charts/{id}/metrics         add/edit metric (formula, y-axis, decimals, unit)
POST /charts/{id}/backpopulate    trigger backpop (range, batch size)
GET  /charts/{id}/data            serve sliced/aggregated series  ← the hot path
GET  /charts                      list charts
```

## 7. Frontend (React) — screens

Charting library: **Apache ECharts** (matches the dense time-series look and export menu in your
screenshots; handles primary/secondary Y-axes well). SQL editor: **Monaco** or **CodeMirror 6**
with SQL highlighting.

1. **Query Editor** — chart name, source, refresh interval, backpop days, batch size, SQL editor,
   variable editor, **Generate Dims and Metrics** button, dims/metrics table, time column + date
   format. (Mirrors your "Dataset / Edit Chart" screen.)
2. **Metric Settings modal** — metric name, **independent fields** multiselect, **formula**
   input, Y-axis (primary/secondary), decimal places, unit. (Mirrors your "Metric Setting"
   dialog.)
3. **Chart View** — dimension **filter dropdowns / chips across the top** (gid, cli, country,
   source_category, source, country_bucket) each as a multiselect; **metric checklist on the
   right**; date-range picker + granularity (day/week/month); "hide zero values"; export. Add a
   **formula-metric builder** to create new metrics from existing ones inline.
4. **Charts list / home** — saved charts, freshness/last-backpop status.

(Annotations — experiments/releases overlays from your screenshots — are a clean **v2** addition:
an `annotations` table + an overlay on the time axis.)

---

## 8. Project layout & local run

```
analytics-dash/
  backend/
    app/ (connections, templating, introspection, backpop, serving, crud, api)
    pyproject.toml
  frontend/
    src/ (pages, components, charts, api-client)
    package.json
  docker-compose.yml      # postgres + backend + worker + frontend
  .env.example            # REDSHIFT_*, POSTGRES_*, etc.
  README.md
```

`docker compose up` brings up Postgres, the API, the backpop worker, and the frontend. Redshift
creds come from `.env`. No cloud account needed to develop — only network reachability to the
cluster when you actually backpopulate.

---

## 9. Suggested build phases (hand these to Claude Code one at a time)

**Build and fully verify the entire backend BEFORE writing any frontend.** Each backend phase is
proven with automated tests + manual API calls (curl / FastAPI's `/docs` Swagger UI). Only once the
backend serves correct sliced/aggregated JSON do we start the frontend. The frontend is built to be
**mutable and modular** — screenshots are placement reference only, and we expect to redesign it
later, so keep components decoupled from the API shape via a thin api-client layer.

**Backend first (test each before moving on):**

1. **Scaffold + connectivity** — repo, Docker Compose, FastAPI hello, confirm a read-only
   `SELECT 1` against Redshift and a DuckDB write/read. *Verify:* health endpoint green.
2. **Chart CRUD** — create/save a chart with query + schedule config (API only). *Verify:* tests +
   Swagger.
3. **Introspection** — "Generate Dims and Metrics" from a query sample; save dim/metric config.
   *Verify:* feed a real query, confirm correct dim/metric split.
4. **Backpopulation engine** — templating + date batching + scheduler; fill DuckDB; run-state table.
   *Verify:* backpop a real chart, inspect stored rows.
5. **Serving API** — `/data` endpoint with grouping/filter aggregation returning time-series JSON.
   *Verify:* curl with different filters, confirm sums are correct.
6. **Independent metrics** — per-metric grain tables + correct serve-time aggregation/dedupe.
   *Verify:* confirm `dau` is NOT multiply-counted when slicing by `source`.
7. **Formula metrics** — expression parser, serve-time evaluation, decimals/unit. *Verify:*
   `revenue/dau` matches hand calculation.

→ **Backend complete and trusted. Now build the frontend (mutable, v1 scope only):**

8. **Frontend shell + chart view** — React/Vite, api-client layer, ECharts line chart, dimension
   dropdowns on top, metric checklist on right, date-range + granularity. (Screenshots = layout
   reference.)
9. **Query editor + metric settings UI** — editor screen, "Generate Dims and Metrics", metric
   settings modal (independent fields, formula, Y-axis, decimals, unit), formula builder.
10. **Polish** — freshness indicators, hide-zero, export, charts list.

**Explicitly OUT of v1** (do later, do not build now): auth/login, multi-user, access tags,
annotations overlay, sharing, secondary-axis fine-tuning beyond basic. Keep the frontend code
structured so these slot in without rework.

---

## 10. Tech choices summary

| Concern | Choice | Why |
|---|---|---|
| Backend | FastAPI (Python) | Best Redshift + data tooling; async serving |
| Redshift driver | `redshift_connector` | Official, wire-compatible, read-only pooling |
| Metadata store | Postgres | Reliable relational config storage |
| Aggregate cache | DuckDB | Embedded columnar; millisecond slice/dice |
| Scheduler | APScheduler (→ Celery if needed) | Simple in-process batching for v1 |
| Frontend | React + Vite | Standard, fast dev |
| Charts | Apache ECharts | Matches your UI; dual Y-axes, export |
| SQL editor | Monaco / CodeMirror 6 | Syntax highlighting |
| Expr parser | small AST evaluator (no `eval`) | Safe BODMAS formula metrics |
| Deploy | Docker Compose | One-command local/single-server run |

---

### First message to give Claude Code
> "Scaffold the repo from Phase 1 of this plan: Docker Compose with Postgres + FastAPI backend +
> React/Vite frontend + a backpop worker service. Add a read-only Redshift connection module using
> `redshift_connector` driven by `.env`, and a health endpoint that runs `SELECT 1` against
> Redshift and a write/read round-trip against DuckDB. Don't build features yet."
