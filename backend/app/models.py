from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Chart(Base):
    __tablename__ = "charts"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    # Human-friendly, searchable number. Certified charts live in 100+, drafts in 1000+.
    chart_number = Column(Integer, nullable=True, unique=True, index=True)
    certified = Column(Boolean, nullable=False, default=False)
    source = Column(String(64), nullable=False, default="redshift")
    query = Column(Text, nullable=False)

    refresh_interval = Column(String(32), nullable=False, default="daily")
    default_backpop_days = Column(Integer, nullable=False, default=7)
    backpop_batch_size = Column(Integer, nullable=False, default=30)
    default_date_range_days = Column(Integer, nullable=False, default=90)
    cur_date_behavior = Column(String(32), nullable=False, default="daily")
    cache_strategy = Column(String(32), nullable=False, default="append")

    time_column = Column(String(128), nullable=True)
    date_format = Column(String(64), nullable=False, default="%Y-%m-%d", server_default="%Y-%m-%d")

    variables = Column(JSON, nullable=False, default=dict, server_default="{}")
    # sha256 of (query + variables) the cached aggregates were last built from.
    # When it differs at backpop time, the cache is rebuilt (not fill-missing skipped).
    cache_query_hash = Column(String(64), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    dimensions = relationship(
        "Dimension",
        back_populates="chart",
        cascade="all, delete-orphan",
        order_by="Dimension.display_order",
    )
    metrics = relationship(
        "Metric",
        back_populates="chart",
        cascade="all, delete-orphan",
        order_by="Metric.display_order",
    )
    backpop_runs = relationship(
        "BackpopRun",
        back_populates="chart",
        cascade="all, delete-orphan",
        order_by="BackpopRun.id.desc()",
    )


class Dimension(Base):
    __tablename__ = "dimensions"

    id = Column(Integer, primary_key=True)
    chart_id = Column(
        Integer, ForeignKey("charts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(128), nullable=False)
    column_name = Column(String(128), nullable=False)
    kind = Column(String(16), nullable=False, default="regular")
    # filter-dropdown value ordering: "natural" (number-aware label sort, default) or
    # "metric" (descending by the primary metric's total — biggest contributors first)
    value_order = Column(String(16), nullable=False, default="natural", server_default="natural")
    display_order = Column(Integer, nullable=False, default=0)

    chart = relationship("Chart", back_populates="dimensions")


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True)
    chart_id = Column(
        Integer, ForeignKey("charts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(128), nullable=False)
    column_name = Column(String(128), nullable=True)  # null for formula metrics
    independent_dimensions = Column(JSON, nullable=False, default=list)
    formula = Column(Text, nullable=True)
    y_axis = Column(String(16), nullable=False, default="primary")
    decimals = Column(Integer, nullable=False, default=0)
    unit = Column(String(32), nullable=True)
    display_order = Column(Integer, nullable=False, default=0)

    chart = relationship("Chart", back_populates="metrics")


class BackpopRun(Base):
    __tablename__ = "backpop_runs"

    id = Column(Integer, primary_key=True)
    chart_id = Column(
        Integer, ForeignKey("charts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_date = Column(Date, nullable=False)
    to_date = Column(Date, nullable=False)
    batch_size = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="running")  # running|success|failed
    row_count = Column(Integer, nullable=False, default=0)
    batches_completed = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    chart = relationship("Chart", back_populates="backpop_runs")
