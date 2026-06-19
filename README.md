# Analytics Dashboard

A self-hosted **SQL → chart** analytics tool over Amazon Redshift, built as a lightweight
[Metabase](https://www.metabase.com/) replacement focused on dense time-series charts.

You write a SQL query **once**. The system **backpopulates** (precomputes) its aggregates on a
schedule into a fast embedded store (DuckDB). The frontend then slices, filters, and re-aggregates
that local store **instantly** — no round-trip to Redshift on every interaction. Redshift is hit
only during backpopulation; everything a viewer does is served from the local cache.

```
Redshift (read-only)  ──backpop: templated, date-batched SELECTs──►  DuckDB (aggregate cache)
        ▲                                                                      │
   query template                                                  fast slice / re-aggregate
   + schedule  (Postgres metadata)                                             ▼
                                                    FastAPI  GET /charts/{id}/data  ──►  React + ECharts
```

This README is written for a **developer gaining context** on the codebase. It covers the product,
the backend in depth, the frontend in depth, the technology used, and known limitations.

---

## Table of contents

1. [The core idea](#the-core-idea)
2. [Technology stack](#technology-stack)
3. [Quick start](#quick-start)
4. [Backend (in detail)](#backend-in-detail)
   - [Process model](#process-model)
   - [Module map](#module-map-backendapp)
   - [Data model](#data-model)
   - [Flow 1 — backpopulation](#flow-1--backpopulation-writing-the-cache)
   - [Flow 2 — serving](#flow-2--serving-reading-the-cache)
   - [Independent metrics (the key correctness concern)](#independent-metrics-the-key-correctness-concern)
   - [Query templating & run modes](#query-templating--run-modes)
   - [Schema bootstrap](#schema-bootstrap)
   - [API endpoints](#api-endpoints)
   - [Tests](#tests)
5. [Frontend (in detail)](#frontend-in-detail)
   - [Views & routing](#views--routing)
   - [The api-client layer](#the-api-client-layer-the-only-decoupling-seam)
   - [Chart page data flow](#chart-page-data-flow)
   - [Config page](#config-page)
   - [State management](#state-management)
6. [Configuration (`.env`)](#configuration-env)
7. [Limitations & known constraints](#limitations--known-constraints)
8. [Scope (v1)](#scope-v1)

---

## The core idea

Most BI tools re-run your query against the warehouse every time someone changes a filter. That's
slow and expensive on Redshift. This tool inverts that:

1. **Define** a chart: a templated SQL query plus its dimensions (what you can slice by) and metrics
   (what you measure).
2. **Backpopulate**: a scheduled job runs the query against Redshift over a date range — broken into
   batches — and stores the raw aggregate rows in a per-chart DuckDB table.
3. **Serve**: when a viewer opens the chart, the API reads only from DuckDB, applies their chosen
   date range / granularity / dimension filters, and re-aggregates on the fly. This is the **hot
   path** and it never touches Redshift.

The single hardest correctness problem is **not double-counting independent metrics** when slicing
(e.g. a daily-active-users metric must not be multiplied when you split by `source`). That logic is
described in detail [below](#independent-metrics-the-key-correctness-concern).

---

## Technology stack

| Layer | Technology | Notes |
|---|---|---|
| **Backend API** | Python 3.11+, **FastAPI**, Uvicorn | Async-capable web framework; auto Swagger docs at `/docs`. |
| **Metadata store** | **Postgres 16**, SQLAlchemy 2.0 ORM | Stores chart config, dims/metrics, backpop run history. |
| **Aggregate cache** | **DuckDB 1.0** (embedded) | One file (`/data/aggregates.duckdb`); one table per chart. |
| **Warehouse** | **Amazon Redshift** (read-only) | Accessed via `redshift_connector`; `SELECT` only. |
| **Scheduler** | **APScheduler 3.10** | Nightly backpopulation in a separate worker process. |
| **Frontend** | **React 18** + **Vite 5** + **TypeScript** (strict) | SPA, plain hooks (no Redux/Zustand). |
| **Charts** | **Apache ECharts 5** (canvas) | Time-series, bar, area, categorical, dual-axis. |
| **Styling** | **Tailwind CSS 3** | Utility-first. |
| **Local run** | **Docker Compose** | postgres + backend + worker + frontend. |

---

## Quick start

```bash
cp .env.example .env          # then fill in REDSHIFT_* with read-only credentials
docker compose up             # postgres + backend + backpop worker + frontend
```

- Frontend: <http://localhost:5173>
- Backend API + Swagger docs: <http://localhost:8001/docs>
- Health (Redshift / DuckDB / Postgres): <http://localhost:8001/health>

> The backend listens on container port `8000`, published to host `8001`. The worker shares the
> same image but runs `python -m app.worker` instead of Uvicorn.

You don't need a cloud account to develop the UI or serve cached data — only network reachability to
the Redshift cluster when you actually **backpopulate** or **introspect** a query. If `/health`
shows Redshift `error`, the backend can't reach the cluster (check it's running and your network /
security group / VPN allows port 5439); serving from existing cache still works.

---

## Backend (in detail)

Source root: `backend/app`.

### Process model

Two containers run the same image:

- **`backend`** — the FastAPI app (`uvicorn app.main:app`). Serves all HTTP endpoints. On startup its
  lifespan hook calls `db.ensure_schema()` to create/migrate the Postgres metadata schema.
- **`worker`** — `python -m app.worker`. Runs `ensure_schema()`, reaps any stale `running` backpop
  runs, then starts an **APScheduler** cron job (default **03:00 UTC**, configurable) that
  backpopulates yesterday's data for every chart whose `refresh_interval` is `daily`.

Both connect to the same Postgres and the same DuckDB file (a shared `duckdb_data` volume).

### Module map (`backend/app`)

```
app/
  main.py            FastAPI app + lifespan (ensure_schema on boot) + GET /health
  worker.py          Scheduler entrypoint (ensure_schema, reap stale runs, start APScheduler)
  config.py          pydantic-settings: Redshift/Postgres/DuckDB config from env
  db.py              ensure_schema() — idempotent create_all + hand-rolled ALTER migrations
  models.py          SQLAlchemy ORM: Chart, Dimension, Metric, BackpopRun
  schemas.py         Pydantic request/response models + validation

  api/               FastAPI routers
    charts.py          CRUD + /charts/overview (home page: charts + freshness + last run)
    dims_metrics.py    POST /introspect, GET/PUT /dims-metrics
    backpop.py         POST /backpopulate, GET /freshness, GET /backpop-runs
    data.py            GET /data (the hot path), GET /dim-values

  connections/       External I/O
    postgres.py        SQLAlchemy engine, SessionLocal, get_db dependency
    redshift.py        read-only redshift_connector wrapper; health = SELECT 1
    duckdb.py          file-backed DuckDB connection

  templating/        substitute() placeholder filling + expand_date_range() batch windows
  introspection/     introspect_query(): run query with LIMIT 0, classify columns by type OID
  backpop/
    __init__.py        run_backpop() orchestrator: compute batches, query, write, track status
    duckdb_writer.py   write_batch(), present_dates() — create/append/replace per-chart tables
    scheduler.py       APScheduler nightly job
  serving/__init__.py  serve_data() + the dedupe-correct aggregation (read path)
  formulas/__init__.py validate_formula() (AST whitelist) + eval_formula() (safe arithmetic)
  crud/
    charts.py          create/get/list/update/delete + chart-number allocation
    dims_metrics.py    atomic replace of a chart's dimensions + metrics
```

### Data model

**Postgres (metadata)** — four tables, defined in `models.py`:

- **`charts`** — the chart definition. Key columns:
  - `id`, `name` (unique), `query` (the SQL template), `source` (`"redshift"`).
  - `chart_number` (unique) + `certified` (bool) — see [numbering](#chart-numbering).
  - `cur_date_behavior` (`"daily"` | `"batched"`) — the run mode.
  - `cache_strategy` (`"append"` | `"replace"`).
  - `time_column`, `date_format`, `variables` (JSON dict of static template vars).
  - Schedule knobs: `refresh_interval`, `default_backpop_days`, `backpop_batch_size`,
    `default_date_range_days`.
- **`dimensions`** — `chart_id`, `name`, `column_name`, `kind` (`"regular"` | `"time"`),
  `display_order`.
- **`metrics`** — `chart_id`, `name`, `column_name` (null for formula metrics),
  **`independent_dimensions`** (JSON list — the dedupe declaration), `formula` (null for base
  metrics), `y_axis`, `decimals`, `unit`, `display_order`.
- **`backpop_runs`** — one row per backpopulation: `from_date`, `to_date`, `batch_size`, `status`
  (`running` | `success` | `failed`), `row_count`, `batches_completed`, `error_message`, timestamps.

**DuckDB (aggregate cache)** — one table per chart, named **`chart_<id>_data`** (e.g.
`chart_42_data`). Columns mirror the query's result set; column types are **inferred from the first
non-NULL value** seen on the first write and fixed thereafter.

### Flow 1 — backpopulation (writing the cache)

Entry: `POST /charts/{id}/backpopulate` → `api/backpop.py::trigger_backpop` → `backpop.run_backpop()`.

1. **Compute batches** (`_compute_batches`), branching on `cur_date_behavior`:
   - **`daily`** — one batch per calendar day in `[from_date, to_date]`. If `cache_strategy ==
     "append"` and a `time_column` is set, `duckdb_writer.present_dates()` is consulted to **skip
     days already cached** (fill-missing).
   - **`batched`** — contiguous N-day windows via `templating.expand_date_range(...,
     batch_size_days)`. The effective cache strategy is forced to **`replace`** so re-runs are
     idempotent (no duplication).
2. **Substitute the template** per batch with `templating.substitute(query, chart.variables, batch)`
   — fills `{START_DATE}`, `{END_DATE}`, `{CUR_DATE_HIPHEN}`, etc.
3. **Query Redshift** read-only and fetch rows + column names.
4. **Write to DuckDB** (`duckdb_writer.write_batch`): create `chart_<id>_data` if missing (inferring
   types); on `replace` + existing table, delete the batch's date range first; then insert the rows.
5. **Track status**: a `BackpopRun` row is created (`running`), updated with `row_count` /
   `batches_completed`, and finalized as `success` or `failed` (with `error_message`).

### Flow 2 — serving (reading the cache)

Entry: `GET /charts/{id}/data` → `api/data.py` → `serving.serve_data(chart, DataRequest)`. **Never
touches Redshift.**

1. **Classify metrics** into *base* (backed by a DuckDB column) and *formula* (derived). Formula
   metrics declare which base metrics they reference (validated via `formulas.validate_formula`).
2. **Keyset** (`_run_keyset`): `SELECT DISTINCT (time_bucket, …requested dimensions)` where
   `time_bucket = date_trunc(granularity, time_column)` and the date range + dimension-value filters
   are applied. This is the set of output rows.
3. **Per-base-metric aggregation** (`_run_metric`) — a **two-phase GROUP BY** that implements the
   independent-metric dedupe (next section).
4. **Pivot + formula eval in Python**: for each keyset row, look up each base metric's value and
   evaluate formula metrics (`formulas.eval_formula`, which returns `None` on divide-by-zero).

### Independent metrics (the key correctness concern)

> A metric **independent of a dimension** has the same value regardless of that dimension's value.
> It must be **deduplicated, never summed**, across that dimension — otherwise it is multiply-counted.

Example: `dau` (daily active users) does not depend on `source`. If you split a chart by `source`
and naïvely `SUM(dau)`, you'd count the same users once per source. The fix is declared per metric
via `metrics.independent_dimensions` and enforced in `serving._run_metric` as a two-phase aggregation:

- **Inner (per-day grain)** — `GROUP BY (_day, …chart dims *minus* the independent dims)` taking
  `MAX(metric)`. Because the metric is identical across its independent dims by definition, `MAX`
  simply collapses them to a single representative value per day.
- **Outer (per time bucket)** — `GROUP BY (time_bucket, …requested dims *minus* the independent
  dims)` taking `SUM(...)` to roll the deduped daily values up to the requested granularity.

When the result is pivoted back into rows that *include* the independent dimension, the same value
simply **repeats** across those rows — which is the correct semantic. This is covered by dedicated
tests (`tests/test_independent_metrics.py`, `tests/pressure/test_phase_6_independent.py`).

### Query templating & run modes

Write **date-agnostic** SQL with `{PLACEHOLDER}` tokens; the backpop engine fills them per batch
(`templating.substitute`). Unresolved tokens raise an error rather than running a broken query.

| Placeholder | Expands to |
|---|---|
| `{START_DATE}` | batch window start, `YYYY-MM-DD` |
| `{END_DATE}` | batch window end (inclusive), `YYYY-MM-DD` |
| `{CUR_DATE_HIPHEN}` | alias for `{END_DATE}` — the batch's date in `YYYY-MM-DD` |
| `{CUR_DATE_UNDERSCORE}` | the batch's date in `YYYY_MM_DD` |
| `{YOUR_VAR}` | static value from the chart's `variables` (scalars → string; lists → `'a', 'b', 'c'`) |

The **run mode** (`cur_date_behavior`) is chosen to match how the query is written:

- **Daily** — one query *per day*. Pair with `WHERE event_date = '{CUR_DATE_HIPHEN}'`.
- **Batched** — one query *per N-day window*. Pair with
  `WHERE event_date BETWEEN '{START_DATE}' AND '{END_DATE}'`.

### Schema bootstrap

There is **no Alembic** in v1. `db.ensure_schema()` is called on every backend and worker startup
and is **idempotent**: it runs `Base.metadata.create_all()` (creates missing tables) and then applies
explicit `ALTER`/`CREATE INDEX` statements for columns that `create_all` won't add to existing tables
(`certified`, `chart_number` + unique index with a backfill, `variables`, nullable `column_name` for
formula metrics). Tests use a fresh in-memory SQLite per run, so model columns appear automatically
there.

### API endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Redshift / DuckDB / Postgres status |
| `POST` | `/charts` | Create a chart (optionally kick off an initial backpop in the background) |
| `GET` | `/charts` · `/charts/overview` · `/charts/{id}` | list · list-with-freshness/last-run · read |
| `PUT` `DELETE` | `/charts/{id}` | update (re-numbers if `certified` changed) · delete |
| `POST` | `/charts/{id}/introspect` | Run the query with `LIMIT 0`, propose dims/metrics by column type |
| `GET` `PUT` | `/charts/{id}/dims-metrics` | read · atomic replace of dim/metric config |
| `POST` | `/charts/{id}/backpopulate` | Run backpopulation (**synchronous**; optional range/batch) |
| `GET` | `/charts/{id}/freshness` · `/charts/{id}/backpop-runs` | data recency + running flag · run history |
| `GET` | `/charts/{id}/data` | **the hot path** — sliced/aggregated time series |
| `GET` | `/charts/{id}/dim-values` | distinct dimension values + date extent (for filter dropdowns) |

### Tests

`pytest` under `backend/tests`:

- **14 functional suites** (`test_charts`, `test_backpop`, `test_serving`, `test_dims_metrics`,
  `test_formulas`, `test_formula_serving`, `test_independent_metrics`, `test_templating`,
  `test_introspection`, `test_dim_values`, `test_freshness`, `test_initial_backpop`,
  `test_scheduler`, `test_health`).
- **A `tests/pressure/` suite** mirroring backend plan phases 1–7 (health, CRUD, introspection,
  backpop, serving, independent-dimension dedupe, formulas), plus an oracle/reconcile harness
  (`oracle.py`, `reconcile.py`, `golden.py`) that checks served output against an independent
  reference computation.

```bash
docker compose exec backend python -m pytest tests/ -q   # backend unit + pressure suites
docker compose exec frontend npx tsc --noEmit             # frontend type check
```

---

## Frontend (in detail)

Source root: `frontend/src`. A single-page app in plain React (hooks only — no global store). It is
**deliberately decoupled** from the backend through one thin layer so the UI can be redesigned
without backend changes.

```
src/
  api/          client.ts (the ONLY place that knows the backend URL shape) + types.ts
  charts/       TimeSeriesChart.tsx (ECharts), HoverCard.tsx
  components/   primitives.tsx (icons, dropdowns, fields), DateRangePicker.tsx, types.ts (UI shapes)
  pages/
    home/       HomePage.tsx                 — charts list + search + freshness/status
    chart/      ChartView.tsx                — presentational shell (header, panels, canvas)
                ChartViewContainer.tsx       — data fetch + state orchestrator
                ChartPicker, DimensionFilterBar, MetricsPanel, MetricSettingsModal,
                MetricOrderModal, ChartToolsRail, DataTablePanel, Switch,
                ExportMenu, FreshnessChip, transforms.ts, exportChart.ts
    config/     ConfigView.tsx               — presentational shell (form sections)
                ConfigContainer.tsx          — config logic
                QueryBox, VariablesEditor, DimsMetricsTable, IndependentPicker,
                BackpopulateModal, BackpopHistory, DeleteConfirm, fields.tsx
  App.tsx       view routing (home / chart / config)
  main.tsx      ReactDOM mount
  index.css     Tailwind directives
```

The codebase is split into many **small, single-responsibility files** on purpose: it keeps any one
file cheap to read and edit (including for AI agents) without risking unrelated breakage.

### Views & routing

`App.tsx` holds a small view union and reads the URL on load:

```ts
type View = { name: 'home' } | { name: 'chart' } | { name: 'config'; target: number | 'new' }
```

- **No params** → **home** (the charts list — the default landing).
- **`?chart=<id>`** → opens that chart directly (deep link).
- **`?config=<id>`** / **`?config=new`** → the config page. Editing/creating **opens config in a new
  browser tab** (`window.open(...)`), so you can configure a chart without losing your place in the
  chart view.

Navigation: Home → Chart (click a row), Chart → Home (🏠 button in the header), Chart/Home → Config
(new tab).

### The api-client layer (the only decoupling seam)

`src/api/client.ts` is a thin `fetch` wrapper hitting `/api/*`, which Vite proxies to the backend.
**No component talks to the network directly** — containers call the client and map responses into
presentational props (the UI shapes live in `components/types.ts`, separate from the API shapes in
`api/types.ts`). Methods include:

`listCharts` · `chartsOverview` (home) · `getChart` · `createChart` · `updateChart` · `deleteChart`
· `introspect` · `getDimsMetrics` / `putDimsMetrics` · `getDimValues` · `getData` (hot path) ·
`backpopulate` · `backpopRuns` · `freshness`.

### Chart page data flow

`ChartViewContainer` is the orchestrator:

1. **On chart change**, fetch `getDimsMetrics` + `getDimValues` to populate dimension state
   (values, selected, split flags) and metric state.
2. **On any query change** (metrics, granularity, date range, filters, hide-zero, splits, or a manual
   `dataReloadKey` bump), call `getData(...)` and pivot the response into `UISeries[]` + `ChartRow[]`.
3. **Render `TimeSeriesChart`** (ECharts canvas) with a custom React `HoverCard` showing per-series
   values and DoD/WoW/MoM deltas.

**Split-by-dimension** turns one metric into many series (one per dimension-value combination). View-only
transforms live in `transforms.ts`: `applyPercentage` (100%-stacked), `applyMovingAverage`,
`buildCategorical` (put a dimension on the X-axis). Series keys are built by joining the metric and
its dimension-value combo with a **`` (0x01) separator** — a control character chosen because
it can't collide with real metric names or dimension values. **The same separator byte is used in
both `ChartViewContainer` (key building) and `transforms.ts` (key splitting); they must stay in
sync.** A cardinality cap (~20 series) prevents a high-cardinality split from exploding the chart.

The header also surfaces a **freshness chip** ("Data through `<date>`", or a red "backpop failed"
flag, or "Backpopulating…") and an **Export** menu — PNG (from the ECharts canvas via
`getDataURL`) or CSV (the currently-plotted series).

### Config page

`ConfigContainer` + `ConfigView` let a developer fully define a chart:

- **Title, source, and a Certified toggle.** Certifying changes the chart's number band — see below.
- **Query Variables** editor (the static `{VAR}` template values).
- **Cache & backpopulation settings**: default date range, refresh interval, the **run mode**
  (`cur_date_behavior` Daily/Batched), backpop days, and batch size. A warning appears if the query's
  placeholders don't match the chosen run mode.
- **SQL query box** with an **Introspect** action that proposes dimensions/metrics from the query's
  column types, editable in a **dims/metrics table** (including marking a metric as independent of a
  dimension via `IndependentPicker`).
- **Backpopulate modal** (pick a date range, run it) and **backpop history** (recent runs, with the
  rest collapsed).
- **Delete** (confirm dialog).

#### Chart numbering

Every chart gets a searchable number. **Certified** charts are numbered from **100+**, **drafts**
from **1000+**. In the config UI, toggling *Certified* shows a **preview** of the new number with an
amber "(unsaved)" badge — but the number is **only re-allocated by the backend on save**, never on
the toggle itself.

### State management

Plain React hooks — `useState` / `useEffect` / `useMemo`, no Redux/Zustand. Re-fetching is driven by
effect dependencies plus an explicit **`dataReloadKey`** counter that's bumped after a backpopulation
to force a fresh `getData`. Stale-response races are guarded with a fetch-token ref and an "alive"
flag in effect cleanups.

---

## Configuration (`.env`)

| Group | Vars |
|---|---|
| Redshift (read-only) | `REDSHIFT_HOST`, `REDSHIFT_PORT`, `REDSHIFT_DATABASE`, `REDSHIFT_USER`, `REDSHIFT_PASSWORD` |
| Metadata Postgres | `POSTGRES_HOST` / `PORT` / `DB` / `USER` / `PASSWORD` (run by compose) |
| Aggregate cache | `DUCKDB_PATH` (default `/data/aggregates.duckdb`) |
| Scheduler (optional) | `SCHEDULER_HOUR_UTC`, `SCHEDULER_MINUTE_UTC` (default 03:00 UTC) |

Never commit real credentials — `.env` is git-ignored; `.env.example` documents the keys.

---

## Limitations & known constraints

These are real, current constraints worth knowing before extending the system:

- **Single-user, no auth.** v1 has no login, users, roles, sharing, or permissions. **Anyone who can
  reach the app can create, edit, delete, and run queries.** Deploy it behind your own network
  controls. (Auth/multi-user is explicitly out of scope but the code is structured to add it later.)
- **Manual backpopulation is synchronous.** `POST /charts/{id}/backpopulate` runs the whole job
  inside the HTTP request and only returns when it finishes. A large date range can block the request
  and hit client/proxy timeouts. (The *scheduled* nightly backpop runs in the worker process, not the
  request path; initial backpop on chart creation is backgrounded.) Moving manual runs to a job
  queue is a natural future improvement.
- **Deleting a chart orphans its DuckDB table.** `DELETE /charts/{id}` removes the Postgres metadata
  (cascading to dims/metrics/runs) but **does not drop `chart_<id>_data` from DuckDB**, so the cache
  file grows over time. Cleanup is currently manual.
- **DuckDB is embedded and single-file.** It's perfect for a single self-hosted instance but is **not
  a horizontally-scalable server** — heavy concurrent writes (e.g. many simultaneous backpops) can
  contend on the one file, and you can't run multiple backend replicas against the same DuckDB safely.
- **Column types are inferred once, from the first non-NULL value.** If an early batch is empty or
  unrepresentative, a column can get an unexpected type; the DuckDB table schema is fixed on first
  write and isn't migrated automatically afterward.
- **Independent-metric correctness is user-declared.** The dedupe logic is exactly right *given* the
  `independent_dimensions` declaration — but the system can't infer independence. If a metric is truly
  independent of a dimension and you forget to mark it, it **will** be double-counted when split by
  that dimension.
- **No in-app SQL sandboxing.** Safety relies on Redshift **read-only credentials** (SELECT-only) and
  templated substitution — there's no query parser/allow-list inside the app. Use genuinely
  read-only creds.
- **Hand-rolled schema migrations.** No Alembic; column changes mean editing `db.ensure_schema()`
  carefully (idempotent ALTERs). Fine for v1, worth replacing with real migrations before the schema
  churns a lot.
- **Formula metrics are arithmetic-only.** `formulas` allows `+ - * / % **`, numeric literals, and
  references to base metrics — no functions, attributes, or comprehensions. Division/modulo by zero
  yields `None` (renders as a gap).
- **Frontend is intentionally mutable.** The UI is decoupled via the api-client and will be
  redesigned; treat layout/styling as provisional, not a spec.

---

## Scope (v1)

**In:** SQL editor + introspection, scheduled & manual backpopulation, sliced/aggregated serving,
independent + formula metrics, the chart view (filters, metrics panel, options, %/moving-average,
fullscreen, export), the config page, a charts list/home with freshness, and chart certification +
numbering.

**Out (deferred, but kept structurally easy to add):** auth/login, multi-user, access tags,
annotation overlays, and full sharing/permissions.
