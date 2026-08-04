import json
from datetime import date
from typing import Literal

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.connections.postgres import get_db
from app.crud import charts as crud_charts
from app.schemas import DataRequest, DataResponse
from app.serving import dimension_values, serve_data

router = APIRouter(prefix="/charts", tags=["data"])


def _usable_default_x_axis(chart) -> str | None:
    """The chart's saved x_axis, but only while it still points at an existing dimension.

    Returns None (=> plain time series) when the saved dimension is GONE (e.g. a query edit
    dropped it), so a stale default can't make the chart unopenable.

    `included` is deliberately NOT required: excluding a dimension only hides it from the
    chart's filter chips, it stays valid as the x-axis. That's the point for a high-cardinality
    date dimension like install_date — you never want to pick cohorts from a dropdown (the
    date picker drives the range), but you do want to plot against them.
    """
    if not chart.x_axis or chart.x_axis == chart.time_column:
        return None
    from app.derived_dims import DERIVED_NAMES

    if chart.x_axis in DERIVED_NAMES:
        return chart.x_axis
    return chart.x_axis if chart.x_axis in {d.name for d in chart.dimensions} else None


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
    return dimension_values(chart, from_date, to_date)


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
            x_axis=x_axis if x_axis is not None else _usable_default_x_axis(chart),
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
        # e.g. a metric column cached as VARCHAR (aggregation binder error) — the
        # cache table's types are stale/poisoned. Surface an actionable 400 with a
        # rebuild hint instead of a blank 500.
        raise HTTPException(
            status_code=400,
            detail=f"cache type error (rebuild this chart's data to fix): {e}",
        )
