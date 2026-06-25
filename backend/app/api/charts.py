from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.backpop import run_backpop
from app.backpop.duckdb_writer import drop_table
from app.connections.postgres import SessionLocal, get_db
from app.crud import charts as crud_charts
from app.models import BackpopRun
from app.schemas import ChartCreate, ChartOverview, ChartRead, ChartUpdate
from app.serving import latest_data_date

router = APIRouter(prefix="/charts", tags=["charts"])


def _bg_initial_backpop(chart_id: int, days: int) -> None:
    db = SessionLocal()
    try:
        today = date.today()
        from_date = today - timedelta(days=days - 1)
        run_backpop(db, chart_id, from_date=from_date, to_date=today)
    finally:
        db.close()


@router.post("", response_model=ChartRead, status_code=status.HTTP_201_CREATED)
def create_chart(
    payload: ChartCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        chart = crud_charts.create(db, payload)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"chart name '{payload.name}' already exists",
        )
    if payload.initial_backpop_days:
        background_tasks.add_task(
            _bg_initial_backpop, chart.id, payload.initial_backpop_days
        )
    return chart


@router.get("", response_model=list[ChartRead])
def list_charts(db: Session = Depends(get_db)):
    return crud_charts.list_all(db)


@router.get("/overview", response_model=list[ChartOverview])
def charts_overview(db: Session = Depends(get_db)):
    """All charts + their freshness/last-backpop status in one call (home page)."""
    out: list[ChartOverview] = []
    for c in crud_charts.list_all(db):
        last = (
            db.query(BackpopRun)
            .filter(BackpopRun.chart_id == c.id)
            .order_by(BackpopRun.id.desc())
            .first()
        )
        running = (
            db.query(BackpopRun)
            .filter(BackpopRun.chart_id == c.id, BackpopRun.status == "running")
            .count()
            > 0
        )
        out.append(
            ChartOverview(
                id=c.id,
                name=c.name,
                chart_number=c.chart_number,
                certified=c.certified,
                latest_data_date=latest_data_date(c),
                last_backpop_status=last.status if last else None,
                last_backpop_at=last.completed_at if last else None,
                last_backpop_rows=last.row_count if last else None,
                running=running,
            )
        )
    return out


@router.get("/{chart_id}", response_model=ChartRead)
def get_chart(chart_id: int, db: Session = Depends(get_db)):
    chart = crud_charts.get(db, chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="chart not found")
    return chart


@router.put("/{chart_id}", response_model=ChartRead)
def update_chart(chart_id: int, payload: ChartUpdate, db: Session = Depends(get_db)):
    try:
        chart = crud_charts.update(db, chart_id, payload)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="chart name conflict")
    if chart is None:
        raise HTTPException(status_code=404, detail="chart not found")
    return chart


@router.delete("/{chart_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chart(chart_id: int, db: Session = Depends(get_db)):
    if not crud_charts.delete(db, chart_id):
        raise HTTPException(status_code=404, detail="chart not found")
    # Also discard the chart's cached aggregates so no orphan chart_<id>_data
    # table is left behind in DuckDB (metadata cascade only covers Postgres).
    drop_table(chart_id)
