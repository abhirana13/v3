"""Backend-defined derived dimensions.

A derived dimension is computed from a base column via a value→bucket mapping
instead of being produced by the chart's SQL. Drop the column from your query and
the backend fills it in: it materializes the bucket into the DuckDB cache at
backpop time (only when the query doesn't already supply that column) and exposes
it as a normal filter/split dimension wherever its source column is present.

Defined in code — edit a mapping here, redeploy, and re-backpopulate to apply it.
"""

from dataclasses import dataclass
from types import SimpleNamespace


@dataclass(frozen=True)
class DerivedDim:
    name: str           # the dimension + cache column name, e.g. "country_tier"
    source_column: str  # base column it's computed from, e.g. "country"
    buckets: dict       # {bucket_name: [source values]} — order preserved
    default: str        # bucket for any value not listed


DERIVED_DIMENSIONS: list[DerivedDim] = [
    DerivedDim(
        name="country_tier",
        source_column="country",
        buckets={"Tier-1": ["United States", "United Kingdom", "Australia", "Canada"]},
        default="Tier-2",
    ),
]

DERIVED_NAMES = {d.name for d in DERIVED_DIMENSIONS}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def case_sql(d: DerivedDim) -> str:
    """SQL CASE mapping the source column to its bucket name."""
    lit = lambda v: "'" + str(v).replace("'", "''") + "'"
    whens = " ".join(
        f"WHEN {_q(d.source_column)} IN ({', '.join(lit(v) for v in vals)}) THEN {lit(bucket)}"
        for bucket, vals in d.buckets.items()
    )
    return f"CASE {whens} ELSE {lit(d.default)} END"


def derived_for_chart(chart, present_columns) -> list[DerivedDim]:
    """Derived dims that apply: their column has been materialized into the cache
    (present_columns) and the chart doesn't already define a real dim of that name.
    Gating on the materialized column avoids referencing a column that isn't there
    yet (e.g. before the first backpop, or in caches built by an older query)."""
    present = set(present_columns)
    names = {dim.name for dim in chart.dimensions}
    return [d for d in DERIVED_DIMENSIONS if d.name in present and d.name not in names]


def effective_dimensions(chart, present_columns) -> list:
    """Chart's real dimensions plus any applicable derived ones, the latter as
    lightweight dimension-shaped objects so serving treats them like normal cols."""
    extra = [
        SimpleNamespace(name=d.name, column_name=d.name, kind="regular", value_order="natural", derived=True)
        for d in derived_for_chart(chart, present_columns)
    ]
    return list(chart.dimensions) + extra
