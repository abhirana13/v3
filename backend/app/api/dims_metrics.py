from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.connections.postgres import get_db
from app.crud import charts as crud_charts
from app.crud import dims_metrics as crud_dm
from app.introspection import IntrospectionError, introspect_query
from app.schemas import DimsMetricsIn, DimsMetricsOut, IntrospectionResult

router = APIRouter(prefix="/charts", tags=["dims-metrics"])


@router.post("/{chart_id}/introspect", response_model=IntrospectionResult)
def introspect_chart(chart_id: int, db: Session = Depends(get_db)):
    chart = crud_charts.get(db, chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="chart not found")
    try:
        return introspect_query(chart.query, static_vars=dict(chart.variables or {}))
    except IntrospectionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"introspection failed: {e}",
        )


@router.get("/{chart_id}/dims-metrics", response_model=DimsMetricsOut)
def get_dims_metrics(chart_id: int, db: Session = Depends(get_db)):
    chart = crud_dm.get(db, chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="chart not found")
    return DimsMetricsOut(
        time_column=chart.time_column,
        date_format=chart.date_format,
        dimensions=chart.dimensions,
        metrics=chart.metrics,
    )


@router.put("/{chart_id}/dims-metrics", response_model=DimsMetricsOut)
def put_dims_metrics(
    chart_id: int, payload: DimsMetricsIn, db: Session = Depends(get_db)
):
    chart = crud_dm.replace(db, chart_id, payload)
    if chart is None:
        raise HTTPException(status_code=404, detail="chart not found")
    return DimsMetricsOut(
        time_column=chart.time_column,
        date_format=chart.date_format,
        dimensions=chart.dimensions,
        metrics=chart.metrics,
    )
