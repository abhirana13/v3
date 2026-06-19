from sqlalchemy.orm import Session

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

    for idx, d in enumerate(payload.dimensions):
        chart.dimensions.append(
            Dimension(
                name=d.name,
                column_name=d.column_name,
                kind=d.kind,
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
