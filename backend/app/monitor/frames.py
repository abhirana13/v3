"""Tiny row-list helpers. Cuts come back as lists of dicts (one per group); nothing here needs
pandas. Values are plain int/float/str; None stays None."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Callable, Iterable


def num(v) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def group_sum(rows: Iterable[dict], keys: tuple[str, ...], sums: tuple[str, ...]) -> dict[tuple, dict]:
    """{(k1, k2, ...): {sum_col: total}} over rows."""
    out: dict[tuple, dict] = {}
    for r in rows:
        k = tuple(r.get(c) for c in keys)
        acc = out.get(k)
        if acc is None:
            acc = out[k] = {c: 0.0 for c in sums}
        for c in sums:
            acc[c] += num(r.get(c))
    return out


def where(rows: Iterable[dict], pred: Callable[[dict], bool]) -> list[dict]:
    return [r for r in rows if pred(r)]


def by_day(rows: Iterable[dict], value: str, day_col: str = "day") -> dict[int, float]:
    out: dict[int, float] = defaultdict(float)
    for r in rows:
        out[int(r[day_col])] += num(r.get(value))
    return dict(out)


def safe_div(a: float, b: float) -> float | None:
    return a / b if b else None


def med(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return median(xs) if xs else None


def rel(a: float | None, b: float | None) -> float | None:
    """a relative to b: (a - b) / |b|."""
    if a is None or b is None or b == 0:
        return None
    return (a - b) / abs(b)
