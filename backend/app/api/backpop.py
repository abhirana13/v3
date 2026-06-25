from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.backpop import request_cancel, run_backpop
from app.connections.postgres import get_db
from app.crud import charts as crud_charts
from app.models import BackpopRun
from app.schemas import BackpopRequest, BackpopRunRead, FreshnessRead
from app.serving import latest_data_date

router = APIRouter(prefix="/charts", tags=["backpop"])


@router.post("/{chart_id}/backpopulate", response_model=BackpopRunRead)
def trigger_backpop(
    chart_id: int,
    payload: BackpopRequest | None = None,
    db: Session = Depends(get_db),
):
    if crud_charts.get(db, chart_id) is None:
        raise HTTPException(status_code=404, detail="chart not found")
    payload = payload or BackpopRequest()
    run = run_backpop(
        db,
        chart_id=chart_id,
        from_date=payload.from_date,
        to_date=payload.to_date,
        batch_size=payload.batch_size,
        force=payload.force,
    )
    return run


@router.post("/{chart_id}/backpop-runs/{run_id}/cancel", response_model=BackpopRunRead)
def cancel_backpop(chart_id: int, run_id: int, db: Session = Depends(get_db)):
    """Request cancellation of a running backpop. The run's loop stops at the next
    batch boundary and is marked 'cancelled'; rows already written are kept."""
    run = db.get(BackpopRun, run_id)
    if run is None or run.chart_id != chart_id:
        raise HTTPException(status_code=404, detail="backpop run not found")
    if run.status == "running":
        request_cancel(run_id)
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
        latest_data_date=latest_data_date(chart),
        running=running,
        last_run=last_run,
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
