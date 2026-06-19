import json
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.connections.postgres import get_db
from app.crud import charts as crud_charts
from app.schemas import DataRequest, DataResponse
from app.serving import dimension_values, serve_data

router = APIRouter(prefix="/charts", tags=["data"])


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

    parsed_filters: dict[str, list[str]] = {}
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
