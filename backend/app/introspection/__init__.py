"""Classify a chart's SQL query into dimensions and metrics.

Append `LIMIT 0` to the query so Redshift returns column metadata without
scanning data. Append (rather than subquery-wrap) so top-level `WITH` clauses
survive — Redshift rejects `SELECT * FROM (WITH cte AS ... SELECT ...) sub`.
Strip any trailing `LIMIT N [OFFSET M]` so it doesn't conflict.

Template variables (`{CUR_DATE_HIPHEN}`, `{COUNTRY}`, ...) are substituted with
a sample date (defaults to today) + the chart's static `variables` dict before
the query is sent to Redshift — otherwise the raw `{TOKEN}` would be a syntax
error in Redshift.

Columns are classified by PostgreSQL OID (Redshift uses the same type codes).
"""

import re
from datetime import date

from app.connections import redshift as redshift_conn
from app.schemas import DimensionIn, IntrospectionResult, MetricIn
from app.templating import DateBatch, UnresolvedVariableError, substitute

_NUMERIC_OIDS = {20, 21, 23, 700, 701, 1700, 790}  # int2/4/8, float4/8, numeric, money
_STRING_OIDS = {25, 1042, 1043}  # text, bpchar, varchar
_TIME_OIDS = {1082, 1114, 1184, 1083, 1266}  # date, timestamp, timestamptz, time, timetz
_BOOL_OIDS = {16}

# Friendly type names by OID (for the config classification table).
_OID_NAMES = {
    16: "boolean", 20: "bigint", 21: "smallint", 23: "integer", 25: "text",
    700: "real", 701: "double", 790: "money", 1042: "char", 1043: "varchar",
    1082: "date", 1083: "time", 1114: "timestamp", 1184: "timestamptz",
    1266: "timetz", 1700: "numeric",
}


def _type_name(oid) -> str:
    return _OID_NAMES.get(oid, str(oid))

_TRAILING_LIMIT_RE = re.compile(
    r"\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", re.IGNORECASE
)


class IntrospectionError(Exception):
    pass


def _prepare_for_limit_zero(query: str) -> str:
    sql = query.rstrip().rstrip(";").rstrip()
    sql = _TRAILING_LIMIT_RE.sub("", sql)
    return f"{sql}\nLIMIT 0"


def introspect_query(
    query: str,
    static_vars: dict | None = None,
    sample_date: date | None = None,
) -> IntrospectionResult:
    sample_date = sample_date or date.today()
    batch = DateBatch(start_date=sample_date, end_date=sample_date)
    try:
        substituted = substitute(query, static_vars or {}, batch)
    except UnresolvedVariableError as e:
        raise IntrospectionError(
            f"unresolved template variable {{{e.args[0]}}}; set it in chart.variables"
        ) from e

    sql = _prepare_for_limit_zero(substituted)
    try:
        with redshift_conn.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            description = cursor.description or []
    except Exception as e:
        raise IntrospectionError(f"{type(e).__name__}: {e}") from e

    return classify_columns(description)


def classify_columns(description: list) -> IntrospectionResult:
    time_column: str | None = None
    dimensions: list[DimensionIn] = []
    metrics: list[MetricIn] = []

    for col in description:
        name, type_code = col[0], col[1]
        dtype = _type_name(type_code)
        if type_code in _TIME_OIDS:
            if time_column is None:
                time_column = name
            else:
                dimensions.append(DimensionIn(name=name, column_name=name, kind="regular", data_type=dtype))
        elif type_code in _NUMERIC_OIDS:
            metrics.append(MetricIn(name=name, column_name=name, data_type=dtype))
        else:
            dimensions.append(DimensionIn(name=name, column_name=name, kind="regular", data_type=dtype))

    return IntrospectionResult(
        time_column=time_column, dimensions=dimensions, metrics=metrics
    )
