# Backend Pressure-Test Plan

Adversarial correctness spec for the analytics backend (plan phases 1–7). The existing
`backend/tests/` suite is example-based: small hand-built fixtures with hardcoded expected
numbers. This plan adds a **golden dataset + independent reconciliation oracle** so we can verify
the serving math against a second, independently-written implementation across a large matrix of
randomized-but-deterministic requests — catching the edge cases hand-written cases miss.

Guiding principle: **never trust the system to check itself.** The oracle re-derives every expected
number from the raw golden rows using a code path that shares nothing with `app/` (no
`app.serving`, no DuckDB SQL). If both agree across hundreds of request shapes, the math is trusted.

---

## 0. What we are actually trying to break

The backend's correctness risk is concentrated in four places. The harness exists to attack them:

1. **Independent-metric double-counting** (plan §4, CLAUDE.md hard rule). A metric independent of a
   dimension must be **deduped, never summed**, across that dimension. This is the #1 concern.
2. **Granularity roll-up.** day→week→month must be sum-of-daily, while independent metrics stay
   deduped *within* a day before summing *across* days in the bucket.
3. **Formula metrics.** Must be ratio-of-sums (evaluated *after* aggregation), not sum-of-ratios,
   with correct null/zero propagation — including formulas over independent base metrics.
4. **Backpop idempotency.** Append + fill-missing must never double-count rows and must fetch only
   missing days.

Everything else (introspection, templating, filters, hide_zero) is pressured too, but these four
are where a silent wrong number would do real damage.

---

## 1. Golden dataset

A single deterministic synthetic dataset, generated from a fixed seed, at the **finest grain** —
one row per `(event_date × every dimension)`. It is the source of truth: the oracle reads these
raw rows; the system gets the same rows loaded into a DuckDB chart table.

### 1.1 Schema

| column | role | notes |
|---|---|---|
| `event_date` | time | DATE; spans a range chosen to cross week & month boundaries |
| `gid` | dim | high-cardinality (e.g. 8 values) |
| `country` | dim | medium cardinality; **not present on every day** (tests sparse dims) |
| `source` | dim | the classic independence axis (e.g. 4 values) |
| `source_category` | dim | coarser independence axis (e.g. 2 values), correlated to `source` |
| `platform` | dim | low cardinality (iOS/Android) |
| `revenue` | metric | summable over **all** dims; floats incl. fractional cents |
| `sessions` | metric | summable over all dims; ints |
| `dau` | metric | **independent of `source` + `source_category`** — identical across them within a `(date,gid,country,platform)` |
| `installs` | metric | **independent of all dims** — one value per `(date)` repeated everywhere |
| `crashes` | metric | summable; contains **NULLs and zeros** (tests null/zero handling + hide_zero) |

### 1.2 Baked-in invariants (the oracle relies on these being TRUE in the data)

- **`dau` independence:** for fixed `(event_date, gid, country, platform)`, `dau` is the *same value*
  for every `source`/`source_category`. Generator computes `dau` from those four keys only, then
  writes it onto every `source`×`source_category` row.
- **`installs` total-independence:** `installs` depends only on `event_date`; identical on every row
  of that day.
- **`revenue`, `sessions`** depend on the full key → genuinely sum across every cut.
- **`crashes`** is summable but randomly NULL (~15%) and zero (~15%) to exercise null/zero paths.

### 1.3 Stress characteristics deliberately included

- Date range crossing **≥2 ISO-week boundaries** and **≥1 month boundary** (week/month roll-up).
- A **missing day** in the middle (no rows at all) → confirms gaps don't fabricate buckets.
- A **country that appears only in part of the range** → sparse-dimension keyset correctness.
- Metric **NULLs and exact zeros** → hide_zero + null propagation.
- Fractional **revenue** (e.g. `x.xx7`) → float tolerance, decimals rounding.
- Cardinality large enough that a naive `SUM(dau)` is off by a big, obvious factor
  (|source| × |source_category| ≈ 8×) so independence bugs can't hide in rounding.

### 1.4 Determinism

Seeded `random.Random(SEED)` only (the app forbids `Date.now`/random at runtime, but test
*generation* may seed). Re-running the generator must byte-for-byte reproduce the dataset, so
failures are reproducible and the oracle and loader see identical rows.

---

## 2. The reconciliation oracle

`tests/pressure/oracle.py` — a **pure-Python** reference implementation of serving semantics,
deliberately written differently from `app/serving` (plain dict/list aggregation, no SQL, no reuse
of app aggregation code). Given the raw golden rows + chart config + a request, it returns the same
shape `app.serving.serve_data` returns.

It must independently implement:

1. **Filtering** — keep rows whose dim ∈ requested `IN` set; date within `[from,to]`.
2. **Day bucketing** — map `event_date` → bucket start for `day|week|month`. Week = Monday-start to
   match DuckDB `date_trunc('week', …)`; month = first of month. (Pinned by a dedicated test in §3.)
3. **Per-metric grain aggregation** — for each metric:
   - `grain = chart_dims ∖ metric.independent_dimensions`
   - inner: for each `(day, grain-tuple)` take a representative value (the dedup) for the metric
     (all rows in that group are equal by construction for independent metrics; `MAX` = the value).
   - outer: for each `(bucket, effective-tuple)` where `effective = requested_dims ∖ independent`,
     SUM the inner values over days-in-bucket and over grain-dims not in effective.
4. **Pivot / repeat** — emit one row per requested `(bucket, requested-dim-tuple)` keyset; each
   metric looked up by its **effective** key (independent dims dropped → value repeats across them).
5. **Formula eval** — after base metrics resolved per row, evaluate the formula expression with
   Python (`/` by zero or any `None` operand → `None`), round to `decimals`.
6. **hide_zero** — drop rows where every requested metric is `0`/`None`.

The oracle is small, slow, and obviously-correct by inspection — the opposite of the optimized SQL
path. That contrast is the point.

---

## 3. Reconciliation harness

`tests/pressure/` layout:

```
tests/pressure/
  golden.py      # dataset generator + canonical chart config (dims, metrics, independence, formulas)
  oracle.py      # pure-Python reference serve()
  conftest.py    # fixtures: build golden once, load into a tmp DuckDB, create+configure chart via API
  reconcile.py   # request-matrix generator + assert_equal(api_rows, oracle_rows) w/ float tolerance
  test_phase_*.py
```

### 3.1 Loading

The golden rows are written into the chart's DuckDB table via the **real**
`app.backpop.duckdb_writer.write_batch` (not a side channel) so the loader also exercises the
writer's type inference. The chart's dims/metrics/independence/formulas are configured through the
**real** `PUT /charts/{id}/dims-metrics` API.

### 3.2 Request matrix

`reconcile.py` enumerates a deterministic cartesian product and reconciles each against the oracle:

- **group_by** ∈ { ∅ (time only), each single dim, a few 2-dim combos, all dims }
- **metrics** ∈ { all, each single metric, mixed summable+independent, formula-only, formula+bases }
- **granularity** ∈ { day, week, month }
- **filters** ∈ { none, filter a *dependent* dim, filter an *independent* dim, multi-dim filter }
- **date range** ∈ { full, a week-spanning sub-range, a single day, range over the missing day }

Each combination → one assertion that API output equals oracle output (row set equal as a
multiset; metric values within `1e-6` relative for floats, exact for ints). Target: a few hundred
combinations, all sub-second against DuckDB.

### 3.3 Equality

`assert_equal` compares as an **order-independent multiset** of `(bucket, dims…, metrics…)` tuples
(serving guarantees a sort, but the oracle should not have to), with typed comparison: ints exact,
floats within tolerance, `None` == `None`. Mismatches print the smallest differing row.

---

## 4. Per-phase pressure tests

Each phase gets a `test_phase_N_*.py`. Phases 5–7 lean on the golden/oracle harness; phases 1–4 are
mostly targeted adversarial cases (the harness doesn't apply to connectivity/CRUD/introspection).

### Phase 1 — Scaffold + connectivity
- `/health` reports each dependency independently; degraded when one check fails (mocked failure),
  still returns structured JSON (no 500). Redshift check uses read-only `SELECT 1` only.

### Phase 2 — Chart CRUD
- Defaults applied; duplicate name → 409; invalid schedule fields (≤0) → 422; partial update leaves
  untouched fields intact; delete cascades dims/metrics/backpop_runs.
- **Pressure:** unicode/long names at the length boundary; update that flips `cache_strategy` and
  `cur_date_behavior`; create→delete→recreate same name succeeds.

### Phase 3 — Introspection
- OID classification matrix: each numeric OID → metric, each time OID → first=time then dim, string
  & bool → dim. Driven by a table of `(oid, expected_role)`.
- **Pressure:** top-level `WITH` CTE preserved (not subquery-wrapped); trailing `;`, trailing
  `LIMIT N OFFSET M` stripped; template vars (`{CUR_DATE_HIPHEN}`, list vars) substituted *before*
  send; unresolved var → clean `IntrospectionError`; DB error propagated with type+message.
- Config-driven guarantee: a query with an **extra column** re-introspects to a **new dimension**
  with zero code change (assert the new name appears).

### Phase 4 — Backpop (templating, batching, fill-missing, idempotency, run-state)
- **Templating:** date-batch expansion is inclusive; scalar var → quoted; list var → quoted CSV;
  `{CUR_DATE_HIPHEN}` matches configured `date_format`; unknown var raises.
- **Batching:** N-day range with batch_size B → ⌈N/B⌉ batches; boundaries contiguous, no overlap,
  no gap.
- **Fill-missing (append):** seed DuckDB with a window that has interior gaps; assert backpop fetches
  **only** the missing days (one batch per missing day) and leaves present days untouched.
- **Idempotency:** run the same backpop twice → row count stable, no duplicates (the killer test).
- **Run-state:** `backpop_runs` records status/row_count/batches; a failing batch records error +
  partial progress and does not corrupt prior data.
- **Scheduler:** `nightly_backpop_for_yesterday(today)` targets `today-default_backpop_days … today-1`
  for daily charts only.

### Phase 5 — Serving (summable aggregation) — **harness-backed**
- Full §3.2 request matrix restricted to summable metrics (`revenue`, `sessions`, `crashes`).
  Every combination reconciles against the oracle.
- **Pressure invariants** (asserted directly, not just via oracle):
  - SUM over a dropped dim equals the oracle's grouped sum (no leakage).
  - Filtering then grouping == grouping the filtered subset.
  - `hide_zero` removes exactly the all-zero/all-null rows and nothing else.
  - Empty table / out-of-range dates → empty rows, not an error.
  - Unknown dim/metric/filter → `ValueError` (400), never a silent wrong answer.

### Phase 6 — Independent metrics — **harness-backed, the crown jewel**
- Reconcile `dau` (independent of source+source_category) and `installs` (independent of all) across
  the full matrix.
- **Direct invariants:**
  - **Cardinality invariance:** regenerate golden with |source| ∈ {1, 4, 50}; the served `dau` total
    per day is **identical** regardless — proving no multiplication. (A naive sum would scale ~8×–
    100×.)
  - **Filter-on-independent-dim invariance:** filtering `source=A` vs `source=B` yields the **same**
    `dau` (it's the same underlying value); `revenue` differs as expected.
  - **Repeat-across-independent-dim:** grouping by an independent dim repeats the deduped value on
    every dim row (never splits/multiplies it).
  - **Week roll-up of independent metric:** sum-of-daily-deduped within the week, not |source|× that.
  - **Mixed request:** independent + summable metrics in one request each aggregate by their own
    grain simultaneously.

### Phase 7 — Formula metrics — **harness-backed**
- Reconcile `arpu = revenue/dau`, `rev_per_session = revenue/sessions`, and a nested
  `(revenue/dau) * 1.0` across the matrix.
- **Direct invariants:**
  - **Ratio-of-sums ≠ sum-of-ratios:** at week granularity, `arpu` equals `Σrevenue / Σdau` over the
    bucket, *not* the mean of daily ratios. Pinned with a hand-computed value.
  - **Formula over independent base:** `arpu` divides by the **deduped** `dau`, not the
    source-multiplied sum (regression guard for the §6 bug class leaking into formulas).
  - **Null/zero:** `dau=0` → `arpu=None`; any `None` base → `None`.
  - **Safety:** config-time rejection of function calls, attribute access, names, comparisons,
    non-numeric literals; bad syntax; reference to unknown base metric; `column_name`⊕`formula`
    exclusivity.
  - **Decimals:** result rounded to configured places; `decimals=0` → integer.

---

## 5. Execution & acceptance

- Tests live under `backend/tests/pressure/`, run by the same `pytest` invocation
  (`docker compose exec -T backend pytest -q`).
- The golden dataset builds once per session (fixture scope) and loads into a **tmp** DuckDB
  (monkeypatched `duckdb_path`) — never touches the real cache.
- **Acceptance:** full suite green, every matrix combination reconciles, and each "direct invariant"
  above passes. Any single mismatch fails CI with the offending row printed.
- Performance: this is a dedicated adversarial suite (run on demand / in CI, not on every keystroke),
  not the fast unit suite. Single-process wall-clock is ~40–60s. The dominant cost is **DuckDB's
  row-by-row INSERT in the real writer** during per-test fixture loads (we deliberately load through
  the real `write_batch`), plus the ~240-case reconciliation matrix. The golden dataset is kept
  intentionally small (~900 rows) — correctness needs variety, not volume — to keep this bounded.
- **Gotcha:** a backgrounded `docker compose exec … pytest` that is "stopped" leaves the pytest
  process running *inside* the container; several orphans compete for CPU and inflate timings ~Nx.
  If a run is mysteriously slow, check for and kill orphan pytest processes (or restart the backend).

### Build order
1. `golden.py` + `oracle.py` + `conftest.py` + `reconcile.py` (the harness).
2. A single end-to-end reconciliation smoke test to prove harness ⇄ system agree at all.
3. Then `test_phase_5/6/7` (harness-backed), then `test_phase_1/2/3/4` (targeted), since 5–7 are the
   correctness-critical, math-heavy phases.
