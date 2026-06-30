import os
import time

import duckdb

from app.config import settings

# DuckDB is single-writer across processes: a write held by the worker (backpop)
# briefly excludes readers, and an open reader briefly excludes the writer. Our write
# windows are short (per-batch inserts; the slow Redshift fetch holds no DuckDB lock),
# so rather than surface a "Could not set lock" 500 we retry the open with a short
# backoff to bridge the window. Serving opens read_only=True; writers use the default.
_LOCK_RETRIES = 12
_LOCK_BACKOFF = 0.1  # seconds; linear, capped per attempt
_MAX_BACKOFF = 0.5


def _ensure_parent() -> None:
    os.makedirs(os.path.dirname(settings.duckdb_path), exist_ok=True)


def ensure_database() -> None:
    """Create the cache file if it doesn't exist yet. A read_only connection to a
    missing file errors, so serving (read_only) needs the file to exist; call this at
    backend + worker startup."""
    _ensure_parent()
    if not os.path.exists(settings.duckdb_path):
        duckdb.connect(settings.duckdb_path).close()


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
    try:
        conn = get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS _health (id INTEGER, v VARCHAR)")
        conn.execute("DELETE FROM _health")
        conn.execute("INSERT INTO _health VALUES (1, 'ok')")
        row = conn.execute("SELECT v FROM _health WHERE id = 1").fetchone()
        conn.close()
        return {"status": "ok", "result": row[0] if row else None}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}
