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
        # ISO-2 CODES FIRST, because that is what events.country actually holds — 'US', 'PH',
        # 'GB'. The list used to contain only full names ("United States", "United Kingdom",
        # ...), which never matched anything: every row in every chart came out 'Tier-2'.
        # Measured on chart 15 before this fix: 76,011 rows, 208 distinct countries, ZERO
        # Tier-1. The dimension was a constant and nobody noticed, because a wrong bucket
        # looks exactly like a real one.
        #
        # 'GB' is the code the SDK emits for the UK — there are no 'UK' rows at all — but 'UK'
        # is kept because some SDKs do send it and the cost of a spare literal is nothing.
        # Full names are kept for the same reason: a query that joins a country lookup would
        # emit them, and matching both means neither shape silently falls through.
        buckets={
            "Tier-1": [
                "US", "GB", "UK", "AU", "CA",
                "United States", "United Kingdom", "Australia", "Canada",
            ]
        },
        default="Tier-2",
    ),
    DerivedDim(
        name="game",
        source_column="game_id",
        # Readable labels beside the raw id, which is all every chart could show before.
        # EVERY id gets its own bucket, including ones whose name we do not have: a shared
        # default would collapse six different games into one label and silently merge their
        # numbers, which is worse than showing a bare id.
        #
        # Names supplied by the user from the product list on 2026-11-26. 7, 16 and 17 were
        # not on that list and keep a "Game <id>" placeholder rather than a name nobody
        # confirmed; 17 does carry live traffic (230k events since 2026-11-17), so it is worth
        # naming when someone knows it. An id that is not listed at all falls to "Other", which
        # is visible rather than silently merged into a real game.
        buckets={
            "Nova Solitaire": [1],
            "Nut Sort":       [2],
            "Baggage Rush":   [3],
            "Arrow Maze":     [4],
            "Cloud World":    [5],
            "Sortery":        [6],   # "Category Sort" on the product list; every read,
                                     # build read and note in this project calls it Sortery
            "Game 7":         [7],
            "WoofDoku":       [8],
            "DuelMaster":     [9],
            "LineTap":        [10],
            "WoofDoku-In":    [11],
            "RoadConnect":    [12],
            "Cloud World-In": [13],
            "Zen Solitaire":  [14],
            "Seek & Find":    [15],
            "Game 16":        [16],
            "Game 17":        [17],
        },
        default="Other",
    ),
    DerivedDim(
        name="platform_name",
        source_column="platform",
        # The mapping is the production Ad Revenue query's own, not a guess: it encodes
        # android->1, ios->2, tiktok_android->3, tiktok_ios->4.
        #
        # 3 and 4 are kept even though they are 0.04% of rows (1.2M of 2.85B over 90 days).
        # Folding them into "Other" would hide a real, if tiny, TikTok population; folding
        # them into Android/iOS would overstate those.
        # BOTH SHAPES, because the column genuinely holds both. The production charts encode
        # platform as the integers the Ad Revenue query defines (android->1, ios->2); the
        # experiment charts carry the raw strings straight from the event. Listing both means
        # neither silently falls through to "Unknown" - which is exactly what the first
        # version of this did to every experiment-chart row.
        buckets={
            "Android": [1, "android"],
            "iOS":     [2, "ios"],
        },
        # 3 and 4 (tiktok_android / tiktok_ios) are deliberately NOT mapped and fall here:
        # 1.2M rows over 90 days, 0.04% of the table. They stay visible as "Unknown" rather
        # than being folded into Android/iOS, which would overstate those by a real if tiny
        # amount.
        default="Unknown",
    ),
]

DERIVED_NAMES = {d.name for d in DERIVED_DIMENSIONS}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def case_sql(d: DerivedDim) -> str:
    """SQL CASE mapping the source column to its bucket name.

    Matched on UPPER(TRIM(col)) so a value that differs only in case or padding still lands in
    the right bucket. The whole class of bug this dimension shipped with was "the value looks
    right but does not match the literal" — 'United States' against a column holding 'US' —
    and 'us' or ' US ' would fail the same way for the same reason. Bucket values are folded
    the same way here, so the lists above can be written in whatever case reads best.
    """
    lit = lambda v: "'" + str(v).replace("'", "''") + "'"
    # CAST FIRST. TRIM is VARCHAR-only in DuckDB, so a numeric source column (game_id,
    # platform) raises "No function matches trim(BIGINT)" — and materialize_derived runs
    # inside the SUCCESS branch of a backpop, so that exception would mark an otherwise
    # perfectly good run failed, on every chart, every night. Casting is a no-op for the
    # VARCHAR columns this started with.
    col = f"UPPER(TRIM(CAST({_q(d.source_column)} AS VARCHAR)))"
    whens = " ".join(
        f"WHEN {col} IN ({', '.join(lit(str(v).strip().upper()) for v in vals)}) THEN {lit(bucket)}"
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
