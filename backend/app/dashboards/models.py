from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models import Base, Chart


class Dashboard(Base):
    __tablename__ = "dashboards"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    # Human-friendly, searchable number (like Chart.chart_number). Dashboards have
    # no certified concept, so it's a single series from 100+.
    number = Column(Integer, nullable=True, unique=True, index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    # Defaults the view opens with; the global date range derives from these.
    default_date_range_days = Column(Integer, nullable=False, default=90)
    default_end_offset_days = Column(Integer, nullable=False, default=2)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    tabs = relationship(
        "DashboardTab",
        back_populates="dashboard",
        cascade="all, delete-orphan",
        order_by="DashboardTab.display_order",
    )
    filters = relationship(
        "DashboardFilter",
        back_populates="dashboard",
        cascade="all, delete-orphan",
        order_by="DashboardFilter.display_order",
    )


class DashboardTab(Base):
    __tablename__ = "dashboard_tabs"

    id = Column(Integer, primary_key=True)
    dashboard_id = Column(
        Integer, ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(128), nullable=False)
    display_order = Column(Integer, nullable=False, default=0)

    dashboard = relationship("Dashboard", back_populates="tabs")
    widgets = relationship(
        "Widget",
        back_populates="tab",
        cascade="all, delete-orphan",
        order_by="Widget.display_order",
    )


class DashboardFilter(Base):
    """One chip of the global filter bar (e.g. gid / cli / country / install_type).
    Cascades into every widget on every tab; a widget's own filters narrow further."""

    __tablename__ = "dashboard_filters"

    id = Column(Integer, primary_key=True)
    dashboard_id = Column(
        Integer, ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dimension = Column(String(128), nullable=False)
    default_values = Column(JSON, nullable=False, default=list)  # [] = All
    display_order = Column(Integer, nullable=False, default=0)

    dashboard = relationship("Dashboard", back_populates="filters")


class Widget(Base):
    __tablename__ = "dashboard_widgets"

    id = Column(Integer, primary_key=True)
    tab_id = Column(
        Integer, ForeignKey("dashboard_tabs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The chart whose cached data this widget renders. Deleting the chart deletes
    # the widgets built on it (a widget is meaningless without its source).
    source_chart_id = Column(
        Integer, ForeignKey("charts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type = Column(String(16), nullable=False)  # 'chart' | 'number'
    name = Column(String(255), nullable=False)
    layout = Column(JSON, nullable=False)  # {x, y, w, h} for the grid
    config = Column(JSON, nullable=False, default=dict)  # type-specific, see schemas
    display_order = Column(Integer, nullable=False, default=0)

    tab = relationship("DashboardTab", back_populates="widgets")
    source_chart = relationship("Chart", back_populates="dashboard_widgets")


# Attached here (not in app.models) so the chart→widget coupling lives entirely in
# this package. Gives ORM-level cascade on chart deletion, which also covers the
# SQLite test database where the FK ON DELETE CASCADE pragma isn't enforced.
Chart.dashboard_widgets = relationship(
    "Widget", cascade="all, delete", back_populates="source_chart"
)
