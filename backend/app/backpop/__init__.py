import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.backpop import duckdb_writer
from app.config import settings
from app.connections import redshift as redshift_conn
from app.derived_dims import DERIVED_NAMES
from app.models import BackpopRun, Chart
from app.templating import DateBatch, expand_date_range, substitute


def query_hash(chart: Chart) -> str:
    """Stable hash of what determines a chart's cached output — its SQL template
    plus static variables. A change means the cache must be rebuilt, not appended."""
    payload = json.dumps(
        {"query": chart.query or "", "variables": chart.variables or {}},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _execute_redshift(sql: str, database: str | None = None) -> tuple[list[tuple], list[str]]:
    with redshift_conn.connect(database=database) as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        cols = [c[0] for c in (cursor.description or [])]
        rows = cursor.fetchall()
    return rows, cols


def _dates_in_range(from_date: date, to_date: date) -> list[date]:
    return [from_date + timedelta(days=i) for i in range((to_date - from_date).days + 1)]


def _refresh_cutoff(today: date) -> date:
    """First day of the trailing refresh window (inclusive). Days on/after this are
    always re-pulled; older days keep fill-missing. Window size from settings (>=1)."""
    return today - timedelta(days=max(1, settings.backpop_refresh_window_days) - 1)


def _batch_cache_strategy(
    chart: Chart, batch: DateBatch, refresh_cutoff: date, force: bool = False
) -> str:
    """Per-batch write strategy. A forced run replaces every day in range (overwrites
    even old cached days — needs a time_column to delete by). Batched windows always
    replace their range. In daily mode, days inside the trailing refresh window replace
    (overwrite late-arriving data); older days keep the chart's strategy."""
    if force and chart.time_column:
        return "replace"
    if chart.cur_date_behavior == "batched":
        return "replace"
    if chart.time_column and batch.start_date >= refresh_cutoff:
        return "replace"
    return chart.cache_strategy


def _compute_batches(
    chart: Chart, from_date: date, to_date: date, batch_size: int, refresh_cutoff: date,
    force: bool = False,
) -> list[DateBatch]:
    """Batch shape is driven by the chart's ``cur_date_behavior``:

    - ``"daily"`` — one batch per calendar day; ``{CUR_DATE_HIPHEN}`` resolves to
      that day. With ``cache_strategy == "append"`` and a ``time_column`` we
      fill-missing (skip days already cached) — EXCEPT days inside the trailing
      refresh window (on/after ``refresh_cutoff``), which are always re-pulled so
      late-arriving data is caught. A ``force`` run re-pulls EVERY day in range
      (no skip). Pair with ``WHERE d = '{CUR_DATE_HIPHEN}'``.
    - ``"batched"`` — contiguous ``batch_size``-day windows over the whole range.
      ``{CUR_DATE_HIPHEN}`` is only the window's *last* day, so the query must span
      the window with ``BETWEEN '{START_DATE}' AND '{END_DATE}'``.
    """
    if chart.cur_date_behavior == "daily":
        days = _dates_in_range(from_date, to_date)
        if not force and chart.cache_strategy == "append" and chart.time_column:
            present = duckdb_writer.present_dates(
                chart.id, chart.time_column, from_date, to_date
            )
            # skip only older cached days; the refresh window is always re-pulled
            days = [d for d in days if d >= refresh_cutoff or d not in present]
        return [DateBatch(start_date=d, end_date=d) for d in days]
    return expand_date_range(from_date, to_date, batch_size)


def reap_stale_runs(db: Session, max_age_minutes: int = 120) -> int:
    """Mark backpop runs stuck in 'running' past max_age as failed.

    A run only stays 'running' if its process died mid-flight (e.g. the worker
    was killed). Reaping keeps freshness honest. Age is compared in Python to
    avoid tz-aware/naive SQL comparison issues across Postgres and SQLite.
    """
    running = db.query(BackpopRun).filter(BackpopRun.status == "running").all()
    now = datetime.now(timezone.utc)
    reaped = 0
    for r in running:
        started = r.started_at
        if started is None:
            continue
        started = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
        if now - started > timedelta(minutes=max_age_minutes):
            r.status = "failed"
            r.error_message = r.error_message or "stale: run did not complete (process likely terminated)"
            r.completed_at = now
            reaped += 1
    if reaped:
        db.commit()
    return reaped


def request_cancel(db: Session, run_id: int) -> None:
    """Flag a run for cancellation. The flag lives in the DB (not an in-process set) so it
    reaches a run executing in a *different* process — manual runs now execute in the worker,
    and prod serves the API from multiple uvicorn workers. The batch loop re-reads the flag
    between batches and stops, keeping whatever it already wrote (each batch is committed)."""
    db.query(BackpopRun).filter(BackpopRun.id == run_id).update({"cancel_requested": True})
    db.commit()


def _create_run(
    db: Session,
    chart_id: int,
    from_date: date | None = None,
    to_date: date | None = None,
    batch_size: int | None = None,
    status: str = "running",
    force: bool = False,
    keep_cache: bool = False,
) -> BackpopRun:
    """Create + commit a BackpopRun (so it's visible to the history at once) with the
    resolved range/batch size. ``status='queued'`` enqueues it for the worker to execute;
    ``'running'`` is for a run executed inline (nightly/tests). Raises if chart is missing."""
    chart = db.get(Chart, chart_id)
    if chart is None:
        raise ValueError(f"chart {chart_id} not found")
    reap_stale_runs(db)  # clean up any runs orphaned by a prior crash/kill
    today = datetime.now(timezone.utc).date()
    if to_date is None:
        to_date = today
    if from_date is None:
        from_date = to_date - timedelta(days=chart.default_backpop_days - 1)
    if batch_size is None:
        batch_size = chart.backpop_batch_size
    run = BackpopRun(
        chart_id=chart_id, from_date=from_date, to_date=to_date,
        batch_size=batch_size, status=status, force=force,
        keep_cache=keep_cache,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def enqueue_run(
    db: Session,
    chart_id: int,
    from_date: date | None = None,
    to_date: date | None = None,
    batch_size: int | None = None,
    force: bool = False,
    keep_cache: bool = False,
) -> BackpopRun:
    """Queue a manual backpop for the worker to execute, returning the 'queued' run at
    once (so the HTTP request doesn't block on the work — fixes the long-backfill timeout).
    The caller polls the run; ``drain_backpop_queue`` (worker) executes it."""
    return _create_run(
        db, chart_id, from_date, to_date, batch_size, status="queued", force=force,
        keep_cache=keep_cache,
    )


def claim_next_queued(db: Session) -> BackpopRun | None:
    """Atomically claim the oldest queued run (queued -> running) and return it, or None if
    the queue is empty. ``started_at`` is stamped at claim time so the stale-reaper measures
    from actual start, not enqueue time. FOR UPDATE SKIP LOCKED is a no-op on SQLite (tests)."""
    run = (
        db.query(BackpopRun)
        .filter(BackpopRun.status == "queued")
        .order_by(BackpopRun.id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if run is None:
        return None
    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def drain_backpop_queue(db: Session) -> int:
    """Claim + execute queued runs one at a time until the queue is empty; returns the count
    processed. One drainer => DuckDB writes are serialized and never contend with each other.
    Used by the worker's poll job (and by tests, which call it inline after enqueuing)."""
    processed = 0
    while True:
        run = claim_next_queued(db)
        if run is None:
            break
        chart = db.get(Chart, run.chart_id)
        if chart is None:
            run.status = "failed"
            run.error_message = "chart was deleted before the run started"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            continue
        _run_batches(db, run, chart, force=run.force, keep_cache=bool(run.keep_cache))
        processed += 1
    return processed


class ColumnMismatch(Exception):
    """The current query's result columns don't line up with the existing cache table."""


def column_diff(chart: Chart, cols: list[str]) -> tuple[list[str], list[str]]:
    """(added, removed) between a query's output columns and the chart's cache table.

    ([], []) means the cache can accept rows from this query. Derived columns are excluded:
    the backend writes those itself via ALTER TABLE (materialize_derived), so the query never
    supplies them and their presence is expected rather than a difference.

    One definition, used by both the pre-flight check the backpop dialog runs and the guard
    inside the run itself — so the dialog cannot promise something the run then refuses.
    """
    cached = duckdb_writer.cache_columns(chart.id)
    if not cached:
        return [], []  # nothing cached yet, so nothing to be incompatible with
    expected = cached - DERIVED_NAMES
    got = set(cols)
    return sorted(got - expected), sorted(expected - got)


def incompatible_columns(chart: Chart, cols: list[str]) -> str | None:
    """Why the cached table cannot accept rows from the current query, or None if it can.

    Only consulted for a keep-the-cache run, where the table is NOT being rebuilt and so has
    to keep accepting inserts shaped the way it already is.

    Derived columns are excluded from the comparison because the backend writes them itself
    (materialize_derived adds e.g. country_tier with ALTER TABLE); the query never supplies
    them, so their presence in the table is expected rather than a mismatch.

    Both directions matter, and the second is the quiet one:
      * a column the query GAINED isn't in the table, and the INSERT names columns explicitly,
        so it fails outright — loudly, but only after some days were already deleted.
      * a column the query LOST still exists in the table, so the INSERT succeeds and every
        newly written row carries NULL where the old rows hold a value. Nothing errors; the
        chart just quietly grows a hole.
    """
    added, removed = column_diff(chart, cols)
    if not added and not removed:
        return None
    parts = []
    if added:
        parts.append(f"new column(s) {', '.join(added)}")
    if removed:
        parts.append(f"missing column(s) {', '.join(removed)}")
    return (
        f"the query's result columns no longer match the cache ({'; '.join(parts)}). "
        "Keeping the existing data is only safe when the edit changes which ROWS come back, "
        "not which columns. Re-run without 'keep existing data' to rebuild the cache."
    )


def _run_batches(
    db: Session, run: BackpopRun, chart: Chart, force: bool = False,
    keep_cache: bool = False,
) -> BackpopRun:
    """Execute the batches for an already-created run, checking for cancellation
    between batches. Each batch is committed as it lands, so a cancel/failure keeps
    the rows already written. ``force`` re-pulls and overwrites every day in range."""
    # If the query/variables changed since the cache was built, the cache is stale —
    # drop it so this run rebuilds from scratch (also picking up column changes).
    #
    # ...UNLESS this run asked to keep it. That is the "I added a game to the IN list"
    # case: the edit changes which ROWS come back, not what the existing ones mean, so
    # dropping everything and rebuilding only the requested window is pure loss — it is
    # exactly how a chart ends up with three days of history instead of nine months.
    # The caller is asserting the edit is additive; incompatible_columns() below is the
    # part of that assertion the backend can actually check.
    # Keeping the cache means re-reading a range, and re-reading a range means DELETING those
    # days before re-inserting them. That delete is `WHERE CAST(time_column AS DATE) BETWEEN
    # ...`, so without a time column there is nothing to delete by and every re-read would
    # append a second copy of the same rows — silent double counting, which is the one failure
    # this project treats as worse than not running at all. Refuse instead.
    #
    # (`force` has the same hole and is left alone here: changing an existing flag's behaviour
    # is a separate decision. It is reported, not fixed, by this change.)
    if keep_cache and not chart.time_column:
        run.status = "failed"
        run.error_message = (
            "keep existing data needs a time column: without one the cache cannot be cleared "
            "per day, so re-reading a range would append duplicate rows. Set the chart's time "
            "column, or rebuild without keeping the cache."
        )
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
        return run

    current_hash = query_hash(chart)
    query_changed = chart.cache_query_hash is not None and chart.cache_query_hash != current_hash
    if query_changed and not keep_cache:
        duckdb_writer.drop_table(chart.id)

    from_eff, to_eff = run.from_date, run.to_date

    # Auto-heal a poisoned cache: if a declared metric column is stored as a non-numeric
    # type (VARCHAR — from bad first-batch inference), aggregations break. Drop and rebuild
    # over the FULL cached range (∪ the requested range) so no history is lost and types
    # re-infer cleanly (metric columns are forced numeric on create). Self-repairs on the
    # next backpop — including the nightly — so no chart needs a manual rebuild.
    metric_cols = {m.column_name for m in chart.metrics if m.column_name}
    poisoned = duckdb_writer.poisoned_metric_columns(chart.id, metric_cols) if metric_cols else []
    if poisoned:
        if chart.time_column:
            emin, emax = duckdb_writer.data_extent(chart.id, chart.time_column)
            if emin is not None:
                from_eff = min(from_eff, emin)
            if emax is not None:
                to_eff = max(to_eff, emax)
        duckdb_writer.drop_table(chart.id)
        print(
            f"[backpop] chart {chart.id}: healing poisoned cache "
            f"(non-numeric metric columns: {', '.join(poisoned)}); "
            f"rebuilding {from_eff}..{to_eff}",
            flush=True,
        )

    today = datetime.now(timezone.utc).date()
    refresh_cutoff = _refresh_cutoff(today)
    # Keeping the cache has to RE-PULL the requested window, not fill-missing it. The days the
    # new game needs adding to are precisely the days already cached, and fill-missing skips
    # exactly those — so without this the run would report success having changed nothing,
    # which is a worse answer than the data loss it replaced.
    #
    # Deliberately not folded into `force`: that flag also licenses clearing a day whose
    # re-fetch comes back empty, and a mode whose entire purpose is "do not lose my data"
    # must not wipe history on a transient empty read. See wipes_on_empty below, which still
    # keys off `force` alone.
    repull = force or keep_cache
    batches = _compute_batches(
        chart, from_eff, to_eff, run.batch_size, refresh_cutoff, force=repull
    )
    static_vars = dict(chart.variables or {})

    total_rows = 0
    batches_done = 0
    cancelled = False
    checked_columns = False
    try:
        for batch in batches:
            # cancel flag is set by the API (another process); re-read committed state —
            # after the prior batch's commit this is a fresh SELECT, so it sees the flag
            if db.query(BackpopRun.cancel_requested).filter(BackpopRun.id == run.id).scalar():
                cancelled = True
                break
            sql = substitute(chart.query, static_vars, batch)
            rows, cols = _execute_redshift(sql, database=chart.database)
            # Checked once, on the first batch that reports columns, and BEFORE any write —
            # write_batch deletes the batch's day before inserting, so discovering the
            # mismatch one batch later would mean discovering it having already destroyed a
            # day of the history this run promised to preserve.
            if keep_cache and cols and not checked_columns:
                problem = incompatible_columns(chart, cols)
                if problem:
                    raise ColumnMismatch(problem)
                checked_columns = True
            batch_cache = _batch_cache_strategy(chart, batch, refresh_cutoff, force=repull)
            # don't let an empty re-fetch wipe an already-cached refresh-window day
            # (transient blip / data not in yet) — keep what's there until real rows
            # come back. A forced run is an explicit "match Redshift for this range",
            # so it IS allowed to clear a day that now returns no rows. Batched windows
            # keep their existing replace-on-empty behavior.
            wipes_on_empty = (
                not force
                and batch_cache == "replace"
                and chart.cur_date_behavior == "daily"
                and not rows
            )
            if not wipes_on_empty:
                duckdb_writer.write_batch(
                    chart_id=chart.id, columns=cols, rows=rows, batch=batch,
                    cache_strategy=batch_cache, time_column=chart.time_column,
                    # base-metric columns must be typed numeric even if all-NULL in
                    # the first batch (sparse metrics), else SUM() breaks later
                    numeric_columns={m.column_name for m in chart.metrics if m.column_name},
                )
            total_rows += len(rows)
            batches_done += 1
            run.row_count = total_rows
            run.batches_completed = batches_done
            db.commit()
        if cancelled:
            run.status = "cancelled"
            run.error_message = f"cancelled after {batches_done} batch(es); rows already written are kept"
        else:
            duckdb_writer.materialize_derived(chart)  # backend-derived dim columns
            run.status = "success"
            chart.cache_query_hash = current_hash  # cache now reflects the current query
    except Exception as e:
        run.status = "failed"
        run.error_message = f"{type(e).__name__}: {e}"
    finally:
        # Mirror the cache's newest date into Postgres so the home page can report freshness
        # without opening DuckDB (which would contend with this very writer). Done here in
        # the worker — the process that already owns the write lock — and attempted even on
        # failure/cancel, since batches that did land are real data. Never fail the run over it.
        if batches_done and chart.time_column:
            try:
                _, emax = duckdb_writer.data_extent(chart.id, chart.time_column)
                if emax is not None:
                    chart.cache_latest_date = emax
            except Exception as e:
                print(f"[backpop] chart {chart.id}: freshness mirror skipped ({e})", flush=True)
        # Same idea for the cache's column list, which the config page and dashboard
        # dimension resolution need on every request (see cache_present_columns). Mirrored
        # AFTER materialize_derived above so the derived columns it just wrote are included.
        if batches_done:
            try:
                chart.cache_columns = sorted(duckdb_writer.cache_columns(chart.id))
            except Exception as e:
                print(f"[backpop] chart {chart.id}: column mirror skipped ({e})", flush=True)
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
    return run


def run_backpop(
    db: Session,
    chart_id: int,
    from_date: date | None = None,
    to_date: date | None = None,
    batch_size: int | None = None,
    force: bool = False,
    keep_cache: bool = False,
) -> BackpopRun:
    """Create the run and execute its batches. Synchronous: the run is committed as
    'running' up front (so polling sees it) and progresses per batch; a concurrent
    cancel request can stop it between batches. ``force`` re-pulls and overwrites every
    day in range (for restatements), ignoring the fill-missing skip."""
    run = _create_run(db, chart_id, from_date, to_date, batch_size, keep_cache=keep_cache)
    return _run_batches(db, run, db.get(Chart, chart_id), force=force, keep_cache=keep_cache)
