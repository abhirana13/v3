"""Substitute template variables into a SQL query and expand date ranges into batches.

Two kinds of variables, both `{NAME}` token style (uppercase, underscores allowed):
- Built-in date variables filled by the backpop engine per batch:
    {START_DATE}            batch start, YYYY-MM-DD
    {END_DATE}              batch end (inclusive), YYYY-MM-DD
    {CUR_DATE_HIPHEN}       alias for {END_DATE}
    {CUR_DATE_UNDERSCORE}   batch end as YYYY_MM_DD
- Static variables from `chart.variables`:
    scalar -> str(value)
    list   -> "'a', 'b', 'c'"  (SQL-friendly comma-quoted)
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class DateBatch:
    start_date: date
    end_date: date  # inclusive


def expand_date_range(
    from_date: date, to_date: date, batch_size_days: int
) -> list[DateBatch]:
    if batch_size_days < 1:
        raise ValueError("batch_size_days must be >= 1")
    if from_date > to_date:
        return []
    batches: list[DateBatch] = []
    cursor = from_date
    while cursor <= to_date:
        batch_end = min(cursor + timedelta(days=batch_size_days - 1), to_date)
        batches.append(DateBatch(start_date=cursor, end_date=batch_end))
        cursor = batch_end + timedelta(days=1)
    return batches


_TOKEN_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def _builtin_date_vars(batch: DateBatch) -> dict[str, str]:
    return {
        "START_DATE": batch.start_date.isoformat(),
        "END_DATE": batch.end_date.isoformat(),
        "CUR_DATE_HIPHEN": batch.end_date.isoformat(),
        "CUR_DATE_UNDERSCORE": batch.end_date.strftime("%Y_%m_%d"),
    }


def _render_static_value(value) -> str:
    if isinstance(value, list):
        return ", ".join(f"'{v}'" for v in value)
    return str(value)


class UnresolvedVariableError(KeyError):
    pass


def substitute(query: str, static_vars: dict, batch: DateBatch) -> str:
    builtins = _builtin_date_vars(batch)

    def replace(match):
        name = match.group(1)
        if name in builtins:
            return builtins[name]
        if static_vars and name in static_vars:
            return _render_static_value(static_vars[name])
        raise UnresolvedVariableError(name)

    return _TOKEN_RE.sub(replace, query)
