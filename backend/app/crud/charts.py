from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Chart
from app.schemas import ChartCreate, ChartUpdate

# Human-friendly, searchable chart numbers. Certified ("blessed") charts get a
# low number from 100+; everything else (drafts) from 1000+. The series a chart
# sits in always reflects its current `certified` flag, so toggling moves it.
CERTIFIED_START = 100
UNCERTIFIED_START = 1000


def _next_number(db: Session, certified: bool) -> int:
    """Next free number in the series matching `certified` (max-in-series + 1)."""
    if certified:
        cur = (
            db.query(func.max(Chart.chart_number))
            .filter(
                Chart.chart_number >= CERTIFIED_START,
                Chart.chart_number < UNCERTIFIED_START,
            )
            .scalar()
        )
        return (cur or CERTIFIED_START - 1) + 1
    cur = (
        db.query(func.max(Chart.chart_number))
        .filter(Chart.chart_number >= UNCERTIFIED_START)
        .scalar()
    )
    return (cur or UNCERTIFIED_START - 1) + 1


def _number_matches_series(number: int | None, certified: bool) -> bool:
    if number is None:
        return False
    if certified:
        return CERTIFIED_START <= number < UNCERTIFIED_START
    return number >= UNCERTIFIED_START


def create(db: Session, data: ChartCreate) -> Chart:
    chart = Chart(**data.model_dump(exclude={"initial_backpop_days"}))
    chart.chart_number = _next_number(db, chart.certified)
    db.add(chart)
    db.commit()
    db.refresh(chart)
    return chart


def get(db: Session, chart_id: int) -> Chart | None:
    return db.get(Chart, chart_id)


def list_all(db: Session) -> list[Chart]:
    return db.query(Chart).order_by(Chart.id).all()


def update(db: Session, chart_id: int, data: ChartUpdate) -> Chart | None:
    chart = db.get(Chart, chart_id)
    if chart is None:
        return None
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(chart, k, v)
    # Keep the number in the series that matches its certification. Also assigns
    # one to legacy charts created before the chart_number column existed.
    if not _number_matches_series(chart.chart_number, chart.certified):
        chart.chart_number = _next_number(db, chart.certified)
    db.commit()
    db.refresh(chart)
    return chart


def delete(db: Session, chart_id: int) -> bool:
    chart = db.get(Chart, chart_id)
    if chart is None:
        return False
    db.delete(chart)
    db.commit()
    return True
