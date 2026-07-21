from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dashboards.models import Dashboard, DashboardFilter, DashboardTab, Widget
from app.dashboards.schemas import (
    DashboardCreate,
    DashboardFilterIn,
    DashboardUpdate,
    LayoutItemIn,
    TabUpdate,
    WidgetCreate,
)

# Searchable dashboard numbers — a single series (no certified concept), like
# charts' but starting at 100 in its own table.
NUMBER_START = 100


def _next_number(db: Session) -> int:
    cur = db.query(func.max(Dashboard.number)).scalar()
    return (cur or NUMBER_START - 1) + 1


def create(db: Session, data: DashboardCreate) -> Dashboard:
    dashboard = Dashboard(**data.model_dump())
    dashboard.number = _next_number(db)
    # Every dashboard starts with one tab so widgets always have a home.
    dashboard.tabs.append(DashboardTab(name="Main", display_order=0))
    db.add(dashboard)
    db.commit()
    db.refresh(dashboard)
    return dashboard


def get(db: Session, dashboard_id: int) -> Dashboard | None:
    return db.get(Dashboard, dashboard_id)


def list_all(db: Session) -> list[Dashboard]:
    return db.query(Dashboard).order_by(Dashboard.id).all()


def update(db: Session, dashboard: Dashboard, data: DashboardUpdate) -> Dashboard:
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(dashboard, k, v)
    db.commit()
    db.refresh(dashboard)
    return dashboard


def delete(db: Session, dashboard: Dashboard) -> None:
    db.delete(dashboard)
    db.commit()


def replicate(db: Session, dashboard: Dashboard) -> Dashboard:
    """Deep-copy a dashboard (tabs, widgets, global filters) under a fresh
    name/number. Widgets keep pointing at the same source charts."""
    base = f"{dashboard.name} (copy)"
    name, n = base, 2
    while db.query(Dashboard.id).filter(Dashboard.name == name).first() is not None:
        name = f"{base} {n}"
        n += 1

    copy = Dashboard(
        name=name,
        number=_next_number(db),
        enabled=dashboard.enabled,
        default_date_range_days=dashboard.default_date_range_days,
        default_end_offset_days=dashboard.default_end_offset_days,
    )
    for f in dashboard.filters:
        copy.filters.append(
            DashboardFilter(
                dimension=f.dimension,
                default_values=list(f.default_values or []),
                display_order=f.display_order,
            )
        )
    for tab in dashboard.tabs:
        new_tab = DashboardTab(name=tab.name, display_order=tab.display_order)
        for w in tab.widgets:
            new_tab.widgets.append(
                Widget(
                    source_chart_id=w.source_chart_id,
                    type=w.type,
                    name=w.name,
                    layout=dict(w.layout or {}),
                    config=dict(w.config or {}),
                    display_order=w.display_order,
                )
            )
        copy.tabs.append(new_tab)
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return copy


# ---------- tabs ----------

def get_tab(db: Session, dashboard_id: int, tab_id: int) -> DashboardTab | None:
    tab = db.get(DashboardTab, tab_id)
    if tab is None or tab.dashboard_id != dashboard_id:
        return None
    return tab


def add_tab(db: Session, dashboard: Dashboard, name: str) -> DashboardTab:
    next_order = max((t.display_order for t in dashboard.tabs), default=-1) + 1
    tab = DashboardTab(name=name, display_order=next_order)
    dashboard.tabs.append(tab)
    db.commit()
    db.refresh(tab)
    return tab


def update_tab(db: Session, tab: DashboardTab, data: TabUpdate) -> DashboardTab:
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(tab, k, v)
    db.commit()
    db.refresh(tab)
    return tab


def delete_tab(db: Session, tab: DashboardTab) -> None:
    db.delete(tab)
    db.commit()


# ---------- widgets ----------

def get_widget(db: Session, dashboard_id: int, widget_id: int) -> Widget | None:
    widget = db.get(Widget, widget_id)
    if widget is None or widget.tab.dashboard_id != dashboard_id:
        return None
    return widget


def add_widget(db: Session, tab: DashboardTab, data: WidgetCreate) -> Widget:
    next_order = max((w.display_order for w in tab.widgets), default=-1) + 1
    widget = Widget(
        source_chart_id=data.source_chart_id,
        type=data.type,
        name=data.name,
        layout=data.layout.model_dump(),
        config=data.config,
        display_order=next_order,
    )
    tab.widgets.append(widget)
    db.commit()
    db.refresh(widget)
    return widget


def update_widget(db: Session, widget: Widget, fields: dict) -> Widget:
    for k, v in fields.items():
        setattr(widget, k, v)
    db.commit()
    db.refresh(widget)
    return widget


def delete_widget(db: Session, widget: Widget) -> None:
    db.delete(widget)
    db.commit()


def save_layout(db: Session, tab: DashboardTab, items: list[LayoutItemIn]) -> DashboardTab:
    """Bulk-persist positions/sizes after a drag/resize session (one call, atomic)."""
    by_id = {w.id: w for w in tab.widgets}
    for item in items:
        w = by_id[item.widget_id]
        w.layout = {"x": item.x, "y": item.y, "w": item.w, "h": item.h}
    db.commit()
    db.refresh(tab)
    return tab


# ---------- global filters ----------

def replace_filters(
    db: Session, dashboard: Dashboard, items: list[DashboardFilterIn]
) -> Dashboard:
    """Atomic replace of the whole filter bar (same convention as dims-metrics)."""
    dashboard.filters.clear()
    db.flush()
    for i, f in enumerate(items):
        dashboard.filters.append(
            DashboardFilter(
                dimension=f.dimension,
                default_values=list(f.default_values),
                display_order=i,
            )
        )
    db.commit()
    db.refresh(dashboard)
    return dashboard
