import json
from datetime import date
from types import SimpleNamespace
from typing import Literal

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.connections.postgres import get_db
from app.crud import charts as crud_charts
from app.dashboards import crud, serving
from app.dashboards.schemas import (
    ChartWidgetConfig,
    DashboardCreate,
    DashboardFiltersReplace,
    DashboardFull,
    DashboardOverview,
    DashboardRead,
    DashboardUpdate,
    LayoutItemIn,
    NumberWidgetConfig,
    TabCreate,
    TabRead,
    TabUpdate,
    WidgetCreate,
    WidgetPreviewRequest,
    WidgetRead,
    WidgetUpdate,
    validate_widget_config,
)
from app.derived_dims import DERIVED_NAMES
from app.models import Chart

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


def _get_dashboard_or_404(db: Session, dashboard_id: int):
    dashboard = crud.get(db, dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="dashboard not found")
    return dashboard


def _get_tab_or_404(db: Session, dashboard_id: int, tab_id: int):
    tab = crud.get_tab(db, dashboard_id, tab_id)
    if tab is None:
        raise HTTPException(status_code=404, detail="tab not found")
    return tab


def _get_widget_or_404(db: Session, dashboard_id: int, widget_id: int):
    widget = crud.get_widget(db, dashboard_id, widget_id)
    if widget is None:
        raise HTTPException(status_code=404, detail="widget not found")
    return widget


def _check_config_against_chart(chart: Chart, widget_type: str, config: dict) -> None:
    """The spec guardrail: a widget may only reference metrics/dimensions its source
    chart actually produces (derived dims are backend-defined, so always allowed).
    Raises 400 with the offending name."""
    dim_names = {d.name for d in chart.dimensions} | DERIVED_NAMES
    metric_names = {m.name for m in chart.metrics}

    def _bad(kind: str, name: str):
        raise HTTPException(
            status_code=400,
            detail=f"{kind} '{name}' does not exist on source chart '{chart.name}' (id {chart.id})",
        )

    if widget_type == "chart":
        cfg = ChartWidgetConfig.model_validate(config)
        for m in cfg.metrics:
            if m.name not in metric_names:
                _bad("metric", m.name)
        for d in cfg.group_by:
            if d not in dim_names:
                _bad("group_by dimension", d)
        for d in cfg.filters:
            if d not in dim_names:
                _bad("filter dimension", d)
    else:
        cfg = NumberWidgetConfig.model_validate(config)
        if cfg.metric not in metric_names:
            _bad("metric", cfg.metric)
        for d in cfg.filters:
            if d not in dim_names:
                _bad("filter dimension", d)


# ---------- dashboards ----------

@router.post("", response_model=DashboardRead, status_code=status.HTTP_201_CREATED)
def create_dashboard(payload: DashboardCreate, db: Session = Depends(get_db)):
    try:
        return crud.create(db, payload)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"dashboard name '{payload.name}' already exists",
        )


@router.get("", response_model=list[DashboardOverview])
def list_dashboards(db: Session = Depends(get_db)):
    return [
        DashboardOverview(
            id=d.id,
            name=d.name,
            number=d.number,
            enabled=d.enabled,
            updated_at=d.updated_at,
            tab_count=len(d.tabs),
            widget_count=sum(len(t.widgets) for t in d.tabs),
        )
        for d in crud.list_all(db)
    ]


@router.get("/{dashboard_id}", response_model=DashboardFull)
def get_dashboard(dashboard_id: int, db: Session = Depends(get_db)):
    """The full tree the view renders from: tabs + widgets + layout + global filters."""
    return _get_dashboard_or_404(db, dashboard_id)


@router.put("/{dashboard_id}", response_model=DashboardRead)
def update_dashboard(
    dashboard_id: int, payload: DashboardUpdate, db: Session = Depends(get_db)
):
    dashboard = _get_dashboard_or_404(db, dashboard_id)
    try:
        return crud.update(db, dashboard, payload)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="dashboard name conflict")


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dashboard(dashboard_id: int, db: Session = Depends(get_db)):
    dashboard = _get_dashboard_or_404(db, dashboard_id)
    crud.delete(db, dashboard)


@router.post(
    "/{dashboard_id}/replicate",
    response_model=DashboardFull,
    status_code=status.HTTP_201_CREATED,
)
def replicate_dashboard(dashboard_id: int, db: Session = Depends(get_db)):
    dashboard = _get_dashboard_or_404(db, dashboard_id)
    return crud.replicate(db, dashboard)


# ---------- tabs ----------

@router.post(
    "/{dashboard_id}/tabs", response_model=TabRead, status_code=status.HTTP_201_CREATED
)
def add_tab(dashboard_id: int, payload: TabCreate, db: Session = Depends(get_db)):
    dashboard = _get_dashboard_or_404(db, dashboard_id)
    return crud.add_tab(db, dashboard, payload.name)


@router.put("/{dashboard_id}/tabs/{tab_id}", response_model=TabRead)
def update_tab(
    dashboard_id: int, tab_id: int, payload: TabUpdate, db: Session = Depends(get_db)
):
    tab = _get_tab_or_404(db, dashboard_id, tab_id)
    return crud.update_tab(db, tab, payload)


@router.delete("/{dashboard_id}/tabs/{tab_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tab(dashboard_id: int, tab_id: int, db: Session = Depends(get_db)):
    dashboard = _get_dashboard_or_404(db, dashboard_id)
    tab = _get_tab_or_404(db, dashboard_id, tab_id)
    if len(dashboard.tabs) <= 1:
        raise HTTPException(
            status_code=400, detail="a dashboard must keep at least one tab"
        )
    crud.delete_tab(db, tab)


# ---------- widgets ----------

@router.post(
    "/{dashboard_id}/tabs/{tab_id}/widgets",
    response_model=WidgetRead,
    status_code=status.HTTP_201_CREATED,
)
def add_widget(
    dashboard_id: int, tab_id: int, payload: WidgetCreate, db: Session = Depends(get_db)
):
    tab = _get_tab_or_404(db, dashboard_id, tab_id)
    chart = crud_charts.get(db, payload.source_chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="source chart not found")
    _check_config_against_chart(chart, payload.type, payload.config)
    return crud.add_widget(db, tab, payload)


@router.put("/{dashboard_id}/widgets/{widget_id}", response_model=WidgetRead)
def update_widget(
    dashboard_id: int,
    widget_id: int,
    payload: WidgetUpdate,
    db: Session = Depends(get_db),
):
    widget = _get_widget_or_404(db, dashboard_id, widget_id)

    fields = payload.model_dump(exclude_unset=True)
    if "layout" in fields and fields["layout"] is not None:
        fields["layout"] = payload.layout.model_dump()

    chart = widget.source_chart
    if fields.get("source_chart_id") is not None:
        chart = crud_charts.get(db, fields["source_chart_id"])
        if chart is None:
            raise HTTPException(status_code=404, detail="source chart not found")

    # Re-validate whenever the config or the source chart changes — the pair must
    # always be consistent (type itself is immutable).
    if "config" in fields or "source_chart_id" in fields:
        config = fields.get("config", widget.config) or {}
        try:
            config = validate_widget_config(widget.type, config)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        _check_config_against_chart(chart, widget.type, config)
        fields["config"] = config

    return crud.update_widget(db, widget, fields)


@router.delete(
    "/{dashboard_id}/widgets/{widget_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_widget(dashboard_id: int, widget_id: int, db: Session = Depends(get_db)):
    widget = _get_widget_or_404(db, dashboard_id, widget_id)
    crud.delete_widget(db, widget)


@router.put("/{dashboard_id}/tabs/{tab_id}/layout", response_model=TabRead)
def save_tab_layout(
    dashboard_id: int,
    tab_id: int,
    payload: list[LayoutItemIn],
    db: Session = Depends(get_db),
):
    """Bulk-save widget positions/sizes after drag/resize (one atomic call)."""
    tab = _get_tab_or_404(db, dashboard_id, tab_id)
    tab_widget_ids = {w.id for w in tab.widgets}
    for item in payload:
        if item.widget_id not in tab_widget_ids:
            raise HTTPException(
                status_code=400,
                detail=f"widget {item.widget_id} is not on tab {tab_id}",
            )
    return crud.save_layout(db, tab, payload)


# ---------- widget data (the read path) ----------

@router.get("/{dashboard_id}/widgets/{widget_id}/data")
def get_widget_data(
    dashboard_id: int,
    widget_id: int,
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    granularity: Literal["day", "week", "month"] = Query(default="day"),
    filters: str = Query(
        default="",
        description="Global filter-bar state: JSON object {dim_name: [values...]}, URL-encoded",
    ),
    split: list[str] | None = Query(
        default=None,
        description="Global split dimensions (unchecked filter chips) — added to a chart "
        "widget's group_by. Ignored by number widgets.",
    ),
    db: Session = Depends(get_db),
):
    """Resolve a widget to its source chart's CACHED data (never Redshift): widget
    config (metrics/group_by/own filters/offset) merged with the dashboard's global
    filter state, split cuts and date range."""
    dashboard = _get_dashboard_or_404(db, dashboard_id)
    widget = _get_widget_or_404(db, dashboard_id, widget_id)
    chart = widget.source_chart

    global_filters: dict = {}
    if filters:
        try:
            parsed = json.loads(filters)
            if not isinstance(parsed, dict):
                raise ValueError("must be a JSON object mapping dim name to list of values")
            global_filters = parsed
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"invalid filters: {e}")

    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=422, detail="from_date must be <= to_date")

    try:
        if widget.type == "chart":
            return serving.chart_widget_data(
                dashboard, widget, chart, from_date, to_date, granularity, global_filters,
                extra_group_by=split or [],
            )
        # number widgets: from_date/granularity/split don't apply — the tile is a point
        # value at the (offset-capped) as-of date plus its two lookback deltas
        return serving.number_widget_data(dashboard, widget, chart, to_date, global_filters)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except duckdb.Error as e:
        # e.g. a metric column cached with a non-numeric type — surface a readable
        # per-widget error instead of a blank 500 taking the card down
        raise HTTPException(status_code=400, detail=f"source chart cache error: {e}")


@router.post("/{dashboard_id}/widget-preview")
def preview_widget_data(
    dashboard_id: int, payload: WidgetPreviewRequest, db: Session = Depends(get_db)
):
    """Render an ad-hoc (unsaved) widget's data. Same result as /widgets/{id}/data but
    driven by a posted config instead of a stored widget — so the edit-mode working
    copy can preview widgets being added/edited before they're persisted. Reads only
    the source chart's cache; never writes anything."""
    dashboard = _get_dashboard_or_404(db, dashboard_id)
    chart = crud_charts.get(db, payload.source_chart_id)
    if chart is None:
        raise HTTPException(status_code=404, detail="source chart not found")
    try:
        config = validate_widget_config(payload.type, payload.config)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    _check_config_against_chart(chart, payload.type, config)

    # A transient stand-in for a Widget row — serving only reads .id/.type/.config.
    widget = SimpleNamespace(
        id=0, type=payload.type, source_chart_id=chart.id, config=config, source_chart=chart
    )
    try:
        if payload.type == "chart":
            return serving.chart_widget_data(
                dashboard, widget, chart, payload.from_date, payload.to_date,
                payload.granularity, payload.filters, extra_group_by=payload.split,
            )
        return serving.number_widget_data(dashboard, widget, chart, payload.to_date, payload.filters)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except duckdb.Error as e:
        raise HTTPException(status_code=400, detail=f"source chart cache error: {e}")


# ---------- global filters ----------

@router.get("/{dashboard_id}/filter-values")
def get_filter_values(dashboard_id: int, db: Session = Depends(get_db)):
    """Dropdown options for the global filter bar — distinct cached values per
    configured filter dimension, unioned across the dashboard's source charts."""
    dashboard = _get_dashboard_or_404(db, dashboard_id)
    return serving.filter_values(dashboard)


@router.put("/{dashboard_id}/filters", response_model=DashboardFull)
def replace_filters(
    dashboard_id: int, payload: DashboardFiltersReplace, db: Session = Depends(get_db)
):
    """Atomic replace of the global filter bar (same convention as dims-metrics)."""
    dashboard = _get_dashboard_or_404(db, dashboard_id)
    return crud.replace_filters(db, dashboard, payload.filters)
