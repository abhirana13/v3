from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# Dimension values can be non-string (integer ids etc.) — same rule as DataRequest.
FilterValue = str | int | float | bool
Filters = dict[str, list[FilterValue]]

GRID_COLS = 12  # react-grid-layout column count; layouts are validated against it


class LayoutSpec(BaseModel):
    x: int = Field(ge=0, lt=GRID_COLS)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=GRID_COLS)
    h: int = Field(ge=1)

    @model_validator(mode="after")
    def _fits_grid(self):
        if self.x + self.w > GRID_COLS:
            raise ValueError(f"layout overflows the {GRID_COLS}-column grid (x + w > {GRID_COLS})")
        return self


class WidgetMetricSel(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    y_axis: Literal["primary", "secondary"] = "primary"


class ChartWidgetConfig(BaseModel):
    viz: Literal["line", "bar"] = "line"
    metrics: list[WidgetMetricSel] = Field(min_length=1, max_length=5)
    filters: Filters = Field(default_factory=dict)
    group_by: list[str] = Field(default_factory=list, max_length=5)
    # None => fall back to the dashboard's default_end_offset_days
    offset_days: int | None = Field(default=None, ge=0)
    offset_mode: Literal["only_on_end_date"] = "only_on_end_date"
    x_axis: Literal["time"] = "time"
    # Free-form axis display hints ({"primary": {"min": .., "max": ..}, ...}) — a
    # frontend rendering concern, stored but not interpreted by the backend.
    y_axis: dict = Field(default_factory=dict)
    target: float | None = None

    @model_validator(mode="after")
    def _unique_metrics(self):
        names = [m.name for m in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("duplicate metric in widget config")
        return self


class NumberWidgetConfig(BaseModel):
    metric: str = Field(min_length=1, max_length=128)
    filters: Filters = Field(default_factory=dict)
    decimals: int = Field(default=0, ge=0, le=10)
    unit: str | None = Field(default=None, max_length=32)
    compares: list[Literal["previous_day", "last_week"]] = Field(
        default_factory=lambda: ["previous_day", "last_week"]
    )
    offset_days: int | None = Field(default=None, ge=0)
    target: float | None = None

    @model_validator(mode="after")
    def _dedupe_compares(self):
        seen: list[str] = []
        for c in self.compares:
            if c not in seen:
                seen.append(c)
        self.compares = seen
        return self


_CONFIG_MODEL = {"chart": ChartWidgetConfig, "number": NumberWidgetConfig}


def validate_widget_config(widget_type: str, config: dict) -> dict:
    """Parse+normalize a widget's config with the schema matching its type.
    Raises ValueError (pydantic-friendly) on mismatch so callers can 422."""
    model = _CONFIG_MODEL.get(widget_type)
    if model is None:
        raise ValueError(f"unknown widget type '{widget_type}'")
    try:
        return model.model_validate(config or {}).model_dump()
    except ValidationError as e:
        raise ValueError(f"invalid {widget_type}-widget config: {e}") from e


class WidgetCreate(BaseModel):
    type: Literal["chart", "number"]
    source_chart_id: int
    name: str = Field(min_length=1, max_length=255)
    layout: LayoutSpec
    config: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_config(self):
        self.config = validate_widget_config(self.type, self.config)
        return self


class WidgetUpdate(BaseModel):
    """Type is immutable (change type = delete + recreate). config, when present,
    is validated in the API against the widget's stored type + source chart."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    source_chart_id: int | None = None
    layout: LayoutSpec | None = None
    config: dict | None = None


class WidgetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tab_id: int
    source_chart_id: int
    type: str
    name: str
    layout: dict
    config: dict
    display_order: int


class LayoutItemIn(BaseModel):
    """One entry of a bulk layout save (after drag/resize in edit mode)."""

    widget_id: int
    x: int = Field(ge=0, lt=GRID_COLS)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=GRID_COLS)
    h: int = Field(ge=1)

    @model_validator(mode="after")
    def _fits_grid(self):
        if self.x + self.w > GRID_COLS:
            raise ValueError(f"layout overflows the {GRID_COLS}-column grid (x + w > {GRID_COLS})")
        return self


class TabCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class TabUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    display_order: int | None = Field(default=None, ge=0)


class TabRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_order: int
    widgets: list[WidgetRead]


class DashboardFilterIn(BaseModel):
    dimension: str = Field(min_length=1, max_length=128)
    default_values: list[FilterValue] = Field(default_factory=list)


class DashboardFiltersReplace(BaseModel):
    filters: list[DashboardFilterIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_dimensions(self):
        dims = [f.dimension for f in self.filters]
        if len(dims) != len(set(dims)):
            raise ValueError("duplicate dimension in global filters")
        return self


class DashboardFilterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dimension: str
    default_values: list[FilterValue]
    display_order: int


class DashboardBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    default_date_range_days: int = Field(default=90, gt=0)
    default_end_offset_days: int = Field(default=2, ge=0)


class DashboardCreate(DashboardBase):
    pass


class DashboardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    default_date_range_days: int | None = Field(default=None, gt=0)
    default_end_offset_days: int | None = Field(default=None, ge=0)


class DashboardRead(DashboardBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: int | None = None
    created_at: datetime
    updated_at: datetime


class DashboardFull(DashboardRead):
    """The complete tree the view renders from: tabs (with widgets) + global filters."""

    tabs: list[TabRead]
    filters: list[DashboardFilterRead]


class WidgetPreviewRequest(BaseModel):
    """Render an AD-HOC widget (not yet persisted) so the edit-mode working copy can
    show live cached data for widgets being added/edited before Save. Same inputs a
    stored widget carries, plus the global controls the view passes."""

    type: Literal["chart", "number"]
    source_chart_id: int
    config: dict = Field(default_factory=dict)
    from_date: date | None = None
    to_date: date | None = None
    granularity: Literal["day", "week", "month"] = "day"
    filters: Filters = Field(default_factory=dict)
    split: list[str] = Field(default_factory=list)  # global split cuts (chart widgets only)

    @model_validator(mode="after")
    def _from_lte_to(self):
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must be <= to_date")
        return self


class DashboardOverview(BaseModel):
    """One row of the dashboards list."""

    id: int
    name: str
    number: int | None
    enabled: bool
    updated_at: datetime
    tab_count: int
    widget_count: int
