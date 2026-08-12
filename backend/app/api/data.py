import json
from datetime import date
from typing import Literal

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.connections.duckdb import is_lock_error
from app.connections.postgres import get_db
from app.crud import charts as crud_charts
from app.schemas import DataRequest, DataResponse
from app.serving import dimension_values, serve_data, usable_default_x_axis

router = APIRouter(prefix="/charts", tags=["data"])

# A backpop holds DuckDB's single write lock for seconds at a time, and the lock covers the
# whole file — so any chart's backpop can briefly lock out reads for every chart. 503 +
# Retry-After says "transient, come back", which is what a client should act on; the old
# behaviour surfaced this as a 500 (or, worse, as a "rebuild your cache" 400).
_BUSY = "the aggregate cache is being written by a backpopulation right now — retry in a few seconds"


def _busy() -> HTTPException:
    return HTTPException(status_code=503, detail=_BUSY, headers={"Retry-After": "5"})


@router.get("/{chart_id}/dim-values")
def get_dim_values(
    chart_id: int,
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    chart = crud_charts.get(db, chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="chart not found")
    try:
        return dimension_values(chart, from_date, to_date)
    except duckdb.Error as e:
        if is_lock_error(e):
            raise _busy()
        raise


@router.get("/{chart_id}/data", response_model=DataResponse)
def get_chart_data(
    chart_id: int,
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    granularity: Literal["day", "week", "month"] = Query(default="day"),
    x_axis: str | None = Query(
        default=None,
        description="dimension to plot on the x-axis (pivots off time); omit to use the "
        "chart's configured default, or pass the time column name to force a time series",
    ),
    group_by: list[str] | None = Query(default=None),
    metrics: list[str] | None = Query(default=None),
    filters: str = Query(
        default="",
        description='JSON object {dim_name: [values...]}, URL-encoded',
    ),
    hide_zero: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    chart = crud_charts.get(db, chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="chart not found")

    parsed_filters: dict[str, list[str | int | float | bool]] = {}
    if filters:
        try:
            parsed = json.loads(filters)
            if not isinstance(parsed, dict):
                raise ValueError("must be a JSON object mapping dim name to list of values")
            parsed_filters = parsed
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"invalid filters: {e}")

    dims_in = None if group_by is None else [d for d in group_by if d]
    metrics_in = None if metrics is None else [m for m in metrics if m]

    try:
        req = DataRequest(
            from_date=from_date,
            to_date=to_date,
            granularity=granularity,
            # Explicit param wins; otherwise fall back to the chart's configured default.
            # The default is only applied while it still names an INCLUDED dimension: a
            # saved x_axis whose dimension was later excluded (or dropped by a query edit)
            # must degrade to a plain time series, not 400 the chart into being unopenable.
            # An explicitly-passed unknown dimension still errors — that's a caller bug.
            x_axis=x_axis if x_axis is not None else usable_default_x_axis(chart),
            dimensions=dims_in,
            metrics=metrics_in,
            filters=parsed_filters,
            hide_zero=hide_zero,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        return serve_data(chart, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except duckdb.Error as e:
        # A lock conflict is a backpop writing, not a broken cache — it must NOT reach the
        # "rebuild this chart" message below, which would have the user destroy a healthy
        # cache to fix a condition that clears itself in seconds.
        if is_lock_error(e):
            raise _busy()
        # e.g. a metric column cached as VARCHAR (aggregation binder error) — the
        # cache table's types are stale/poisoned. Surface an actionable 400 with a
        # rebuild hint instead of a blank 500.
        raise HTTPException(
            status_code=400,
            detail=f"cache type error (rebuild this chart's data to fix): {e}",
        )
