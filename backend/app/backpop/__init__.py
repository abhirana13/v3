import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.backpop import duckdb_writer
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


def _compute_batches(
    chart: Chart, from_date: date, to_date: date, batch_size: int
) -> list[DateBatch]:
    """Batch shape is driven by the chart's ``cur_date_behavior``:

    - ``"daily"`` — one batch per calendar day; ``{CUR_DATE_HIPHEN}`` resolves to
      that day. With ``cache_strategy == "append"`` and a ``time_column`` we
      fill-missing (skip days already cached); otherwise every day is (re)fetched.
      Pair with ``WHERE d = '{CUR_DATE_HIPHEN}'``.
    - ``"batched"`` — contiguous ``batch_size``-day windows over the whole range.
      ``{CUR_DATE_HIPHEN}`` is only the window's *last* day, so the query must span
      the window with ``BETWEEN '{START_DATE}' AND '{END_DATE}'``.
    """
    if chart.cur_date_behavior == "daily":
        days = _dates_in_range(from_date, to_date)
        if chart.cache_strategy == "append" and chart.time_column:
            present = duckdb_writer.present_dates(
                chart.id, chart.time_column, from_date, to_date
            )
            days = [d for d in days if d not in present]
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


def run_backpop(
    db: Session,
    chart_id: int,
    from_date: date | None = None,
    to_date: date | None = None,
    batch_size: int | None = None,
) -> BackpopRun:
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
        chart_id=chart_id,
        from_date=from_date,
        to_date=to_date,
        batch_size=batch_size,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # If the query/variables changed since the cache was built, the cache is stale —
    # drop it so this run rebuilds from scratch (also picking up any column changes)
    # instead of skipping already-cached dates via the append fill-missing path.
    current_hash = query_hash(chart)
    if chart.cache_query_hash is not None and chart.cache_query_hash != current_hash:
        duckdb_writer.drop_table(chart_id)

    batches = _compute_batches(chart, from_date, to_date, batch_size)
    static_vars = dict(chart.variables or {})
    # Batched windows must overwrite their own date range, otherwise re-running a
    # range under cache_strategy="append" would duplicate rows. Daily mode keeps
    # the chart's configured strategy (append => fill-missing dedup; the per-day
    # batch only ever holds not-yet-present days, so a delete would be a no-op).
    effective_cache = (
        "replace" if chart.cur_date_behavior == "batched" else chart.cache_strategy
    )

    total_rows = 0
    batches_done = 0
    try:
        for batch in batches:
            sql = substitute(chart.query, static_vars, batch)
            rows, cols = _execute_redshift(sql)
            duckdb_writer.write_batch(
                chart_id=chart_id,
                columns=cols,
                rows=rows,
                batch=batch,
                cache_strategy=effective_cache,
                time_column=chart.time_column,
            )
            total_rows += len(rows)
            batches_done += 1
            run.row_count = total_rows
            run.batches_completed = batches_done
            db.commit()
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
