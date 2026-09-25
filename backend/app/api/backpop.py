from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from datetime import datetime, timezone

from app.backpop import column_diff, duckdb_writer, enqueue_run, query_hash, request_cancel
from app.connections.postgres import get_db
from app.crud import charts as crud_charts
from app.models import BackpopRun
from app.introspection import IntrospectionError, query_columns
from app.schemas import (
    BackpopRequest,
    BackpopRunRead,
    CacheCompatOut,
    CacheCompatRequest,
    FreshnessRead,
)
from app.serving import latest_data_date

router = APIRouter(prefix="/charts", tags=["backpop"])


@router.post("/{chart_id}/backpopulate", response_model=BackpopRunRead)
def trigger_backpop(
    chart_id: int,
    payload: BackpopRequest | None = None,
    db: Session = Depends(get_db),
):
    """Queue a backpop and return immediately — the worker executes it (so a long
    backfill never blocks/times out the request). Poll the returned run for progress."""
    if crud_charts.get(db, chart_id) is None:
        raise HTTPException(status_code=404, detail="chart not found")
    payload = payload or BackpopRequest()
    return enqueue_run(
        db,
        chart_id=chart_id,
        from_date=payload.from_date,
        to_date=payload.to_date,
        batch_size=payload.batch_size,
        force=payload.force,
        keep_cache=payload.keep_cache,
    )


@router.post("/{chart_id}/backpop-runs/{run_id}/cancel", response_model=BackpopRunRead)
def cancel_backpop(chart_id: int, run_id: int, db: Session = Depends(get_db)):
    """Cancel a backpop. A queued run is marked cancelled so the worker skips it; a
    running run is flagged and its loop stops at the next batch boundary (rows already
    written are kept)."""
    run = db.get(BackpopRun, run_id)
    if run is None or run.chart_id != chart_id:
        raise HTTPException(status_code=404, detail="backpop run not found")
    if run.status == "queued":
        run.status = "cancelled"
        run.cancel_requested = True
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
    elif run.status == "running":
        request_cancel(db, run_id)
        db.refresh(run)
    return run


@router.get("/{chart_id}/freshness", response_model=FreshnessRead)
def get_freshness(chart_id: int, db: Session = Depends(get_db)):
    chart = crud_charts.get(db, chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="chart not found")
    last_run = (
        db.query(BackpopRun)
        .filter(BackpopRun.chart_id == chart_id)
        .order_by(BackpopRun.id.desc())
        .first()
    )
    running = (
        db.query(BackpopRun)
        .filter(BackpopRun.chart_id == chart_id, BackpopRun.status == "running")
        .count()
        > 0
    )
    return FreshnessRead(
        # Prefer the value the worker mirrored into Postgres (no DuckDB lock to contend
        # with while a backpop is running); read the cache only if it was never mirrored.
        latest_data_date=(
            chart.cache_latest_date
            if chart.cache_latest_date is not None
            else latest_data_date(chart)
        ),
        running=running,
        last_run=last_run,
        # cache_query_hash is null for a chart that has never been backpopped — not a change,
        # just nothing to compare against yet.
        query_changed=(
            chart.cache_query_hash is not None and chart.cache_query_hash != query_hash(chart)
        ),
    )


@router.get("/{chart_id}/backpop-runs", response_model=list[BackpopRunRead])
def list_backpop_runs(chart_id: int, db: Session = Depends(get_db)):
    if crud_charts.get(db, chart_id) is None:
        raise HTTPException(status_code=404, detail="chart not found")
    return (
        db.query(BackpopRun)
        .filter(BackpopRun.chart_id == chart_id)
        .order_by(BackpopRun.id.desc())
        .all()
    )


@router.post("/{chart_id}/cache-compat", response_model=CacheCompatOut)
def cache_compat(
    chart_id: int,
    payload: CacheCompatRequest | None = None,
    db: Session = Depends(get_db),
):
    """Can this query be written into the chart's existing cache?

    Exists so the person editing a query is not the one who has to work out whether their
    edit was structural. Adding a game to an IN list cannot change the output columns, and
    the tool can establish that for itself with a LIMIT 0 round trip — the same one the
    "Generate Dims And Metrics" button already makes — instead of asking the editor to
    vouch for it and warning them when they do.

    Advisory, not authoritative: it runs against the draft query, and the real guard still
    runs inside the backpop against the columns actually returned. The two share
    column_diff(), so they cannot give different answers about the same query.
    """
    chart = crud_charts.get(db, chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="chart not found")

    payload = payload or CacheCompatRequest()
    keep_supported = bool(chart.time_column)
    keep_reason = None if keep_supported else (
        "this chart has no time column, so days cannot be replaced individually — "
        "re-reading a range would append duplicate rows"
    )

    if not duckdb_writer.cache_columns(chart.id):
        # No cache means nothing is at stake — every path rebuilds from scratch anyway.
        return CacheCompatOut(has_cache=False, columns_match=True,
                              keep_supported=keep_supported, keep_blocked_reason=keep_reason)

    query = payload.query if payload.query is not None else chart.query
    variables = payload.variables if payload.variables is not None else dict(chart.variables or {})
    try:
        cols = query_columns(query, static_vars=variables, database=chart.database)
    except IntrospectionError as e:
        # Never block the dialog on this. An unreachable Redshift or a half-typed query is a
        # reason to fall back to asking the editor, not a reason to refuse to show them a
        # backpop dialog at all.
        return CacheCompatOut(
            has_cache=True, columns_match=False, checked=False, message=str(e),
            keep_supported=keep_supported, keep_blocked_reason=keep_reason,
        )

    added, removed = column_diff(chart, cols)
    return CacheCompatOut(
        has_cache=True,
        columns_match=not added and not removed,
        added=added,
        removed=removed,
        keep_supported=keep_supported,
        keep_blocked_reason=keep_reason,
    )
