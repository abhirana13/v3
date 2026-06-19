"""Worker entrypoint — applies the schema then runs the nightly scheduler."""

from app.backpop import reap_stale_runs
from app.backpop.scheduler import start_scheduler
from app.connections.postgres import SessionLocal
from app.db import ensure_schema


if __name__ == "__main__":
    print("[worker] ensuring schema...", flush=True)
    ensure_schema()
    # A worker restart means any run still 'running' is orphaned — reap promptly.
    with SessionLocal() as db:
        n = reap_stale_runs(db, max_age_minutes=0)
        if n:
            print(f"[worker] reaped {n} stale backpop run(s)", flush=True)
    print("[worker] starting scheduler...", flush=True)
    start_scheduler()
