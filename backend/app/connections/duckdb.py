"""Per-chart DuckDB aggregate caches.

ONE FILE PER CHART: `<dir>/charts/chart_<id>.duckdb`, each holding a single
`chart_<id>_data` table.

It used to be one shared file with a table per chart. The data was already isolated — nothing
ever joined two charts' tables — but **DuckDB's write lock is on the FILE, not the table**, so
a backpop of one chart locked out reads of every other chart. Symptom: opening an unrelated
chart mid-backpop returned "the aggregate cache is being written by a backpopulation right now".

Splitting the file makes that structurally impossible. A backpop now takes a lock only the
readers of *that* chart can notice, which is unavoidable and far rarer.

Every accessor therefore needs a chart_id. It is a required argument on purpose: a default
would silently reintroduce a shared file the moment someone forgot to pass one.
"""

import os
import re
import time

import duckdb

from app.config import settings

# Even per-chart, a reader and the writer of the SAME chart still contend, so keep the retry.
# Measured on a 10-day backpop of an 88k-row cache table: the writer held the lock 43% of the
# run in stretches of 6.0-7.1s. (Making materialize_derived incremental later halved that to
# 21% in 5 stretches.) A budget UNDER the write window is the worst case — the caller waits
# the whole budget and still fails — so ~12s, comfortably past it.
_LOCK_RETRIES = 26
_LOCK_BACKOFF = 0.1  # seconds; linear, capped per attempt
_MAX_BACKOFF = 0.5   # => ~12s total


def _config() -> dict:
    """Caps applied to EVERY open. See settings.duckdb_memory_limit for why.

    Must be identical for every connection to a given path. DuckDB caches the database
    instance per path within a process and refuses a second open with different settings —
    "Can't open a connection to same database file with a different configuration than
    existing connections" — so a single open that forgets the config poisons the path for the
    rest of the process.
    """
    return {
        "memory_limit": settings.duckdb_memory_limit,
        "threads": str(settings.duckdb_threads),
    }


def legacy_path() -> str:
    """The pre-split shared file. Kept only so the one-off migration can find it (see db.py).

    A function, not a module constant: bound at import time it could never be reconfigured,
    so a changed duckdb_path would leave the migration reading the old location.
    """
    return settings.duckdb_path


_CHART_TABLE_RE = re.compile(r"^chart_(\d+)_data$")


def charts_dir() -> str:
    return os.path.join(os.path.dirname(settings.duckdb_path), "charts")


def chart_db_path(chart_id: int) -> str:
    return os.path.join(charts_dir(), f"chart_{chart_id}.duckdb")


def legacy_chart_ids(conn) -> list[int]:
    """Chart ids still living as tables inside the legacy shared file."""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables"
    ).fetchall()
    out = []
    for (name,) in rows:
        m = _CHART_TABLE_RE.match(name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def get_legacy_connection():
    """Read-write open of the pre-split shared file, for the one-off migration only.

    Creates the charts directory first: the migration ATTACHes per-chart files into it, and
    DuckDB's ATTACH does NOT create missing parent directories — it fails with
    `Cannot open file "...": No such file or directory`. The backend and worker both run
    ensure_schema() at boot, and whichever reached the migration before anything had called
    ensure_database() failed every chart for exactly that reason.

    No retry loop: if the other process holds the file, the right move is to skip and let the
    next boot finish the split, not to block startup behind a whole backpop.
    """
    _ensure_dir()
    return duckdb.connect(legacy_path(), config=_config())


def _ensure_dir() -> None:
    os.makedirs(charts_dir(), exist_ok=True)


def ensure_database() -> None:
    """Make sure the cache directory exists. Individual chart files are created lazily on
    first open — a chart that has never been backpopped has no file, and that is the honest
    representation of "no cached data" rather than an empty file pretending otherwise."""
    _ensure_dir()


def is_lock_error(e: BaseException) -> bool:
    """True for "another process holds this chart's cache file" — i.e. its backpop is writing.

    Distinguishes a transient contention failure from a real data/type error, which need
    opposite messages: "try again in a moment" vs "your cache is poisoned, rebuild it".
    Telling someone to rebuild a chart because a backpop happened to be running is worse
    than useless — it destroys a good cache.
    """
    return isinstance(e, duckdb.Error) and "lock" in str(e).lower()


def chart_db_exists(chart_id: int) -> bool:
    return os.path.exists(chart_db_path(chart_id))


def get_connection(chart_id: int, read_only: bool = False):
    """Open one chart's cache. Reads (serving) pass read_only=True; writes (backpop) use the
    default. Retries briefly on a lock conflict so a read landing during that chart's write
    window waits it out instead of erroring.

    A read_only open of a chart that has never been backpopped would fail ("database does not
    exist"), so the file is created empty first — callers already handle "table not present"
    as "no data yet", and that path is what a fresh chart hits.
    """
    _ensure_dir()
    path = chart_db_path(chart_id)
    if read_only and not os.path.exists(path):
        # same config as the real open below — see _config()
        duckdb.connect(path, config=_config()).close()
    last: Exception | None = None
    for attempt in range(_LOCK_RETRIES):
        try:
            return duckdb.connect(path, read_only=read_only, config=_config())
        except duckdb.Error as e:
            if "lock" not in str(e).lower():
                raise
            last = e
            time.sleep(min(_LOCK_BACKOFF * (attempt + 1), _MAX_BACKOFF))
    assert last is not None
    raise last


def drop_chart_db(chart_id: int) -> None:
    """Delete a chart's cache entirely — the per-file equivalent of DROP TABLE. Used when a
    chart is deleted so no orphan file is left behind."""
    for suffix in ("", ".wal"):
        p = chart_db_path(chart_id) + suffix
        if os.path.exists(p):
            os.remove(p)


def check() -> dict:
    """Health probe: is the cache directory usable?

    There is no longer a single file to open, and probing an arbitrary chart would report a
    fault when that chart merely happens to be backpopping. So this uses its own dedicated
    `_health.duckdb`, which nothing else ever touches — it cannot contend with any backpop,
    and unlike a read-only probe it actually proves the directory is writable.
    """
    try:
        _ensure_dir()
        path = os.path.join(charts_dir(), "_health.duckdb")
        conn = duckdb.connect(path, config=_config())
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS _health (id INTEGER, v VARCHAR)")
            conn.execute("DELETE FROM _health")
            conn.execute("INSERT INTO _health VALUES (1, 'ok')")
            row = conn.execute("SELECT v FROM _health WHERE id = 1").fetchone()
        finally:
            conn.close()
        return {"status": "ok", "result": row[0] if row else None}
    except Exception as e:
        if is_lock_error(e):
            return {"status": "busy", "detail": "another process is writing the health probe"}
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}
