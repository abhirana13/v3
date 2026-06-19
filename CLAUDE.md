# CLAUDE.md — Analytics Dashboard project

Read `analytics-dashboard-build-plan.md` in this repo before doing anything. It is the source of
truth for architecture and scope.

## What we're building
A self-hosted SQL → chart analytics tool over Amazon Redshift. You write SQL, the system
precomputes ("backpopulates") aggregates on a schedule into a fast local store (DuckDB), and the
frontend slices/aggregates that store instantly. Replacing Metabase.

## Hard rules for this project
- **Backend first.** Build and FULLY VERIFY the entire backend (plan phases 1–7) before writing
  ANY frontend code. Prove each phase with automated tests + manual checks (curl or the FastAPI
  `/docs` Swagger UI) before moving to the next.
- **v1 scope only.** Build only what's in plan sections 1–7 (backend) and 8–10 (frontend). Do NOT
  build: auth/login, multi-user, access tags, annotations, sharing. Keep code structured so these
  can be added later without rework.
- **Frontend is mutable.** It will be redesigned later. The screenshots are *placement/layout
  reference only*, not pixel specs. Keep UI components decoupled from the API via a thin
  `api-client` layer so the backend never has to change when the UI does.
- **Redshift is read-only.** All Redshift access uses read-only credentials and `SELECT` only.
  Never write to Redshift. Never hardcode credentials — everything via `.env`.
- **Don't double-count.** The independent-metric logic (plan §4) is the most important correctness
  concern: a metric independent of a dimension must be deduped, never summed, across that
  dimension. Write tests proving e.g. `dau` is not multiplied when slicing by `source`.

## Stack
Python/FastAPI backend · DuckDB (aggregate cache) · Postgres (metadata) · APScheduler (backpop) ·
React+Vite frontend · Apache ECharts · `redshift_connector` driver · Docker Compose for local run.

## Workflow expectations
- Work one plan phase at a time. After each phase, stop and report what to test.
- Write tests as you go; a phase isn't done until its verification step passes.
- Keep secrets in `.env` (provide `.env.example`); never commit real credentials.
