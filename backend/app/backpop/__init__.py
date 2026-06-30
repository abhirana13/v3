import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.backpop import duckdb_writer
from app.config import settings
from app.connections import redshift as redshift_conn
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


def _execute_redshift(sql: str) -> tuple[list[tuple], list[str]]:
    with redshift_conn.connect() as conn:
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
) -> BackpopRun:
    """Queue a manual backpop for the worker to execute, returning the 'queued' run at
    once (so the HTTP request doesn't block on the work — fixes the long-backfill timeout).
    The caller polls the run; ``drain_backpop_queue`` (worker) executes it."""
    return _create_run(
        db, chart_id, from_date, to_date, batch_size, status="queued", force=force
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
        _run_batches(db, run, chart, force=run.force)
        processed += 1
    return processed


def _run_batches(
    db: Session, run: BackpopRun, chart: Chart, force: bool = False
) -> BackpopRun:
    """Execute the batches for an already-created run, checking for cancellation
    between batches. Each batch is committed as it lands, so a cancel/failure keeps
    the rows already written. ``force`` re-pulls and overwrites every day in range."""
    # If the query/variables changed since the cache was built, the cache is stale —
    # drop it so this run rebuilds from scratch (also picking up column changes).
    current_hash = query_hash(chart)
    if chart.cache_query_hash is not None and chart.cache_query_hash != current_hash:
        duckdb_writer.drop_table(chart.id)

    today = datetime.now(timezone.utc).date()
    refresh_cutoff = _refresh_cutoff(today)
    batches = _compute_batches(
        chart, run.from_date, run.to_date, run.batch_size, refresh_cutoff, force=force
    )
    static_vars = dict(chart.variables or {})

    total_rows = 0
    batches_done = 0
    cancelled = False
    try:
        for batch in batches:
            # cancel flag is set by the API (another process); re-read committed state —
            # after the prior batch's commit this is a fresh SELECT, so it sees the flag
            if db.query(BackpopRun.cancel_requested).filter(BackpopRun.id == run.id).scalar():
                cancelled = True
                break
            sql = substitute(chart.query, static_vars, batch)
            rows, cols = _execute_redshift(sql)
            batch_cache = _batch_cache_strategy(chart, batch, refresh_cutoff, force=force)
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
) -> BackpopRun:
    """Create the run and execute its batches. Synchronous: the run is committed as
    'running' up front (so polling sees it) and progresses per batch; a concurrent
    cancel request can stop it between batches. ``force`` re-pulls and overwrites every
    day in range (for restatements), ignoring the fill-missing skip."""
    run = _create_run(db, chart_id, from_date, to_date, batch_size)
    return _run_batches(db, run, db.get(Chart, chart_id), force=force)
