from sqlalchemy.orm import Session

from app.derived_dims import DERIVED_NAMES
from app.models import Chart, Dimension, Metric
from app.schemas import DimsMetricsIn


def replace(db: Session, chart_id: int, payload: DimsMetricsIn) -> Chart | None:
    chart = db.get(Chart, chart_id)
    if chart is None:
        return None

    if payload.time_column is not None:
        chart.time_column = payload.time_column
    if payload.date_format is not None:
        chart.date_format = payload.date_format

    chart.dimensions.clear()
    chart.metrics.clear()
    db.flush()

    # never persist a backend-derived dimension (e.g. country_tier) as a real one
    saved_dims = [d for d in payload.dimensions if d.name not in DERIVED_NAMES]
    for idx, d in enumerate(saved_dims):
        chart.dimensions.append(
            Dimension(
                name=d.name,
                column_name=d.column_name,
                kind=d.kind,
                value_order=d.value_order,
                display_order=idx,
            )
        )
    for idx, m in enumerate(payload.metrics):
        chart.metrics.append(
            Metric(
                name=m.name,
                column_name=m.column_name,
                independent_dimensions=list(m.independent_dimensions),
                formula=m.formula,
                y_axis=m.y_axis,
                decimals=m.decimals,
                unit=m.unit,
                display_order=idx,
            )
        )

    db.commit()
    db.refresh(chart)
    return chart


def get(db: Session, chart_id: int) -> Chart | None:
    return db.get(Chart, chart_id)
