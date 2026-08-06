import os
import time

import duckdb

from app.config import settings

# DuckDB is single-writer across processes: an open read-write connection excludes readers
# outright (immediately, whether or not it has executed anything), and an open reader
# excludes the writer. Rather than surface a "Could not set lock" error we retry the open
# with a short backoff to bridge the window. Serving opens read_only=True; writers default.
#
# The budget MUST exceed a batch's write window or it is the worst of both worlds — the
# caller waits the whole budget and still fails. Measured on a 10-day backpop of an 88k-row
# cache table: the writer held the lock 43% of the run in 9 stretches of 6.0-7.1s (an
# executemany insert plus materialize_derived's full-table UPDATE, then the close-time
# checkpoint). The old 5.0s budget sat just under that, so every request landing in a write
# window burned 5s and then failed. ~12s converts those into slow successes.
#
# This is a floor, not a fix: a big enough cache table will outrun any budget, so callers
# still have to handle the failure (see is_lock_error -> 503). The real remedy is not
# needing the file — cache_latest_date and cache_columns are mirrored into Postgres so the
# home page and config page never open it at all.
_LOCK_RETRIES = 26
_LOCK_BACKOFF = 0.1  # seconds; linear, capped per attempt
_MAX_BACKOFF = 0.5   # => ~12s total


def _ensure_parent() -> None:
    os.makedirs(os.path.dirname(settings.duckdb_path), exist_ok=True)


def ensure_database() -> None:
    """Create the cache file if it doesn't exist yet. A read_only connection to a
    missing file errors, so serving (read_only) needs the file to exist; call this at
    backend + worker startup."""
    _ensure_parent()
    if not os.path.exists(settings.duckdb_path):
        duckdb.connect(settings.duckdb_path).close()


def is_lock_error(e: BaseException) -> bool:
    """True for "another process holds the cache file" — i.e. a backpop is writing.

    Distinguishes a transient contention failure from a real data/type error, which need
    opposite messages: "try again in a moment" vs "your cache is poisoned, rebuild it".
    Telling someone to rebuild a chart because a backpop happened to be running is worse
    than useless — it destroys a good cache.
    """
    return isinstance(e, duckdb.Error) and "lock" in str(e).lower()


def get_connection(read_only: bool = False):
    """Open the aggregate cache. Reads (serving) pass read_only=True; writes (backpop)
    use the default read-write. Retries briefly on a lock conflict so a read landing
    during a short write — or vice-versa — waits the window out instead of 500ing."""
    _ensure_parent()
    if read_only and not os.path.exists(settings.duckdb_path):
        duckdb.connect(settings.duckdb_path).close()  # create empty so read_only can open
    last: Exception | None = None
    for attempt in range(_LOCK_RETRIES):
        try:
            return duckdb.connect(settings.duckdb_path, read_only=read_only)
        except duckdb.Error as e:
            if "lock" not in str(e).lower():
                raise
            last = e
            time.sleep(min(_LOCK_BACKOFF * (attempt + 1), _MAX_BACKOFF))
    assert last is not None
    raise last


def check() -> dict:
    """Health probe: can this process READ the cache?

    Deliberately read_only. It used to open read-write and round-trip a `_health` table,
    which made /health both contend with the worker's write lock (the backend never writes
    the cache — only the worker does) and report `error` for the whole of every backpop.
    A read-only open still proves the file exists and is readable, which is what the
    backend actually depends on; "a backpop is writing" is reported as busy, not error,
    since it is normal operation rather than a fault.
    """
    try:
        conn = get_connection(read_only=True)
        try:
            row = conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return {"status": "ok", "result": "ok" if row else None}
    except Exception as e:
        if is_lock_error(e):
            return {"status": "busy", "detail": "backpopulation is writing the cache"}
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}
