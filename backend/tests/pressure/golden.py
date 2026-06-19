"""Deterministic golden dataset for backend pressure tests.

One row per (event_date x gid x country x source x source_category x platform),
at the finest grain — exactly what a real chart query would return. The oracle
(oracle.py) reads these raw rows; the system gets the same rows loaded into a
DuckDB chart table via the real writer. If the two agree, the math is trusted.

Independence is BAKED INTO THE DATA, not asserted by fiat:
  - `dau`      depends only on (date, gid, country, platform)  -> independent of source + source_category
  - `installs` depends only on (date)                          -> independent of ALL dims
  - `revenue`, `sessions` depend on the full key               -> sum across every cut
  - `crashes`  is summable but ~15% NULL and ~15% zero         -> exercises null/zero + hide_zero

Everything is computed from integer indices (NOT Python's salted str hash), so
re-running reproduces the dataset byte-for-byte regardless of PYTHONHASHSEED.
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

SEED = 20260618
START = date(2026, 5, 25)  # a Monday — keeps week-boundary reasoning obvious
DAYS = 21                  # 3 ISO weeks, crossing the May->June month boundary
MISSING_DAY = date(2026, 6, 3)  # interior gap: no rows at all this day

# Kept deliberately small: DuckDB row-by-row INSERT (the real writer) dominates
# fixture setup, and correctness needs variety, not volume. The source x
# source_category fan-out is still 4x2=8x, so any double-count is glaring.
GIDS = ["g1", "g2"]
COUNTRIES = ["US", "UK", "BR"]
SOURCES = ["A", "B", "C", "D"]                 # the independence axis for `dau`
SOURCE_CATS = {"A": "paid", "B": "paid", "C": "organic", "D": "organic"}
PLATFORMS = ["ios", "android"]

BR_FROM = date(2026, 6, 1)  # `country=BR` is sparse: only present from June onward

COLUMNS = [
    "event_date", "gid", "country", "source", "source_category", "platform",
    "revenue", "sessions", "dau", "installs", "crashes",
]

TIME_COLUMN = "event_date"
DIMS = ["gid", "country", "source", "source_category", "platform"]


@dataclass
class BaseMetric:
    name: str
    column_name: str
    independent: list = field(default_factory=list)


@dataclass
class FormulaMetric:
    name: str
    formula: str
    decimals: int = 4


BASE_METRICS = [
    BaseMetric("revenue", "revenue", []),
    BaseMetric("sessions", "sessions", []),
    BaseMetric("crashes", "crashes", []),
    BaseMetric("dau", "dau", ["source", "source_category"]),
    BaseMetric("installs", "installs", ["gid", "country", "source", "source_category", "platform"]),
]
FORMULAS = [
    FormulaMetric("arpu", "revenue / dau", 4),
    FormulaMetric("rev_per_session", "revenue / sessions", 4),
]


@dataclass
class GoldenConfig:
    time_column: str = TIME_COLUMN
    dims: list = field(default_factory=lambda: list(DIMS))
    base_metrics: list = field(default_factory=lambda: list(BASE_METRICS))
    formulas: list = field(default_factory=lambda: list(FORMULAS))

    @property
    def all_metric_names(self):
        return [m.name for m in self.base_metrics] + [f.name for f in self.formulas]


CONFIG = GoldenConfig()


def active_dates() -> list[date]:
    return [
        START + timedelta(days=i)
        for i in range(DAYS)
        if START + timedelta(days=i) != MISSING_DAY
    ]


def _dau_value(di: int, gi: int, ci: int, pi: int) -> int:
    # deterministic; crucially does NOT depend on source/source_category index
    return 20 + ((di * 7 + gi * 11 + ci * 13 + pi * 17) % 80)


def _installs_value(di: int) -> int:
    return 500 + (di % 30) * 10


def generate(sources: list | None = None, seed: int = SEED):
    """Return (columns, rows).

    `sources` overrides the source cardinality for the cardinality-invariance
    test (Phase 6): `dau`/`installs` totals must be identical no matter how many
    source values exist, because they don't depend on source.
    """
    src = list(sources) if sources is not None else SOURCES
    rng = random.Random(seed)
    dates = active_dates()
    rows = []
    for di, d in enumerate(dates):
        installs = _installs_value(di)
        for gi, gid in enumerate(GIDS):
            for ci, country in enumerate(COUNTRIES):
                if country == "BR" and d < BR_FROM:
                    continue
                for pi, plat in enumerate(PLATFORMS):
                    dau = _dau_value(di, gi, ci, pi)
                    for s in src:
                        sc = SOURCE_CATS.get(s, "organic")
                        revenue = round(rng.uniform(0, 5) + len(s), 3)
                        sessions = rng.randint(1, 20)
                        roll = rng.random()
                        crashes = None if roll < 0.15 else (0 if roll < 0.30 else rng.randint(1, 5))
                        rows.append(
                            (d, gid, country, s, sc, plat, revenue, sessions, dau, installs, crashes)
                        )
    return COLUMNS, rows


def date_bounds() -> tuple[date, date]:
    ds = active_dates()
    return ds[0], ds[-1]
