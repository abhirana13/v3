from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChartBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    certified: bool = False
    source: str = "redshift"
    query: str = Field(min_length=1)
    refresh_interval: str = "daily"
    default_backpop_days: int = Field(default=7, gt=0)
    backpop_batch_size: int = Field(default=30, gt=0)
    default_date_range_days: int = Field(default=90, gt=0)
    cur_date_behavior: Literal["daily", "batched"] = "daily"
    cache_strategy: str = "append"
    time_column: str | None = None
    date_format: str = "%Y-%m-%d"
    variables: dict[str, Any] = Field(default_factory=dict)


class ChartCreate(ChartBase):
    initial_backpop_days: int | None = Field(default=None, gt=0, le=365)


class ChartUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    source: str | None = None
    query: str | None = Field(default=None, min_length=1)
    refresh_interval: str | None = None
    default_backpop_days: int | None = Field(default=None, gt=0)
    backpop_batch_size: int | None = Field(default=None, gt=0)
    default_date_range_days: int | None = Field(default=None, gt=0)
    cur_date_behavior: Literal["daily", "batched"] | None = None
    certified: bool | None = None
    cache_strategy: str | None = None
    time_column: str | None = None
    date_format: str | None = None
    variables: dict[str, Any] | None = None


class ChartRead(ChartBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chart_number: int | None = None
    created_at: datetime
    updated_at: datetime


class DimensionIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    column_name: str = Field(min_length=1, max_length=128)
    kind: Literal["regular", "time"] = "regular"
    value_order: Literal["natural", "metric"] = "natural"  # filter-value ordering
    data_type: str | None = None  # introspection hint; not persisted


class DimensionOut(DimensionIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_order: int
    derived: bool = False  # true => computed in the backend, not a query column (not editable)


class MetricIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    column_name: str | None = Field(default=None, max_length=128)
    independent_dimensions: list[str] = Field(default_factory=list)
    formula: str | None = None
    y_axis: Literal["primary", "secondary"] = "primary"
    decimals: int = Field(default=0, ge=0, le=10)
    unit: str | None = Field(default=None, max_length=32)
    data_type: str | None = None  # introspection hint; not persisted

    @model_validator(mode="after")
    def _base_xor_formula(self):
        has_col = bool(self.column_name)
        has_formula = bool(self.formula)
        if has_col == has_formula:
            raise ValueError(
                f"metric '{self.name}': set exactly one of column_name "
                "(base metric) or formula (derived metric)"
            )
        return self


class MetricOut(MetricIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_order: int


class IntrospectionResult(BaseModel):
    time_column: str | None
    dimensions: list[DimensionIn]
    metrics: list[MetricIn]


class DimsMetricsIn(BaseModel):
    time_column: str | None = None
    date_format: str | None = None
    dimensions: list[DimensionIn] = Field(default_factory=list)
    metrics: list[MetricIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_independent_dims_reference_dimensions(self):
        dim_names = {d.name for d in self.dimensions}
        for m in self.metrics:
            for ind in m.independent_dimensions:
                if ind not in dim_names:
                    raise ValueError(
                        f"metric '{m.name}': independent_dimensions references unknown dimension '{ind}'"
                    )
        return self

    @model_validator(mode="after")
    def _validate_formulas(self):
        from app.formulas import FormulaError, validate_formula

        base_names = {m.name for m in self.metrics if not m.formula}
        for m in self.metrics:
            if m.formula:
                try:
                    validate_formula(m.formula, base_names)
                except FormulaError as e:
                    raise ValueError(f"metric '{m.name}': {e}") from e
        return self


class DimsMetricsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    time_column: str | None
    date_format: str | None
    dimensions: list[DimensionOut]
    metrics: list[MetricOut]


class BackpopRequest(BaseModel):
    from_date: date | None = None
    to_date: date | None = None
    batch_size: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _from_lte_to(self):
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must be <= to_date")
        return self


class DataRequest(BaseModel):
    from_date: date | None = None
    to_date: date | None = None
    granularity: Literal["day", "week", "month"] = "day"
    dimensions: list[str] | None = None  # None = group by all configured dims
    metrics: list[str] | None = None  # None = include all configured metrics
    filters: dict[str, list[str]] = Field(default_factory=dict)
    hide_zero: bool = False

    @model_validator(mode="after")
    def _from_lte_to(self):
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must be <= to_date")
        return self


class DataResponse(BaseModel):
    chart_id: int
    from_date: date | None
    to_date: date | None
    granularity: str
    dimensions: list[str]
    metrics: list[str]
    rows: list[dict[str, Any]]
    row_count: int


class BackpopRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chart_id: int
    from_date: date
    to_date: date
    batch_size: int
    status: str
    row_count: int
    batches_completed: int
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None


class FreshnessRead(BaseModel):
    latest_data_date: date | None
    running: bool
    last_run: BackpopRunRead | None


class ChartOverview(BaseModel):
    """One row of the charts-list / home page: identity + freshness in one shot."""

    id: int
    name: str
    chart_number: int | None
    certified: bool
    latest_data_date: date | None
    last_backpop_status: str | None
    last_backpop_at: datetime | None
    last_backpop_rows: int | None
    running: bool
