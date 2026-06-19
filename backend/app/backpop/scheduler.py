"""Nightly backpop scheduler — runs in the worker container.

Once per day at SCHEDULER_HOUR_UTC:SCHEDULER_MINUTE_UTC (default 03:00 UTC),
for every chart with refresh_interval=='daily':

  from_date = today - default_backpop_days   (e.g. 7 days ago)
  to_date   = today - 1                      (yesterday)
  run_backpop(...)

For charts with `cache_strategy='append'` + a `time_column`, the runner
fill-missing-only — it queries DuckDB to see which days in [from_date, to_date]
already have data and only re-fetches the gaps. So a healthy nightly run for
yesterday's data adds one day; a worker that was down for three days catches
up by fetching the three missing days; everything else is a no-op.
"""

import os
from datetime import date, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from app.backpop import run_backpop
from app.connections.postgres import SessionLocal
from app.models import Chart


def nightly_backpop_for_yesterday(today: date | None = None) -> dict:
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    summary: dict = {"date": yesterday.isoformat(), "charts": []}
    db = SessionLocal()
    try:
        charts = db.query(Chart).filter(Chart.refresh_interval == "daily").all()
        for c in charts:
            from_date = today - timedelta(days=c.default_backpop_days)
            entry: dict = {
                "chart_id": c.id,
                "name": c.name,
                "from_date": from_date.isoformat(),
                "to_date": yesterday.isoformat(),
            }
            try:
                run = run_backpop(db, c.id, from_date=from_date, to_date=yesterday)
                entry["status"] = run.status
                entry["rows"] = run.row_count
                entry["batches"] = run.batches_completed
                if run.error_message:
                    entry["error"] = run.error_message
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = f"{type(e).__name__}: {e}"
            summary["charts"].append(entry)
        return summary
    finally:
        db.close()


def start_scheduler() -> None:
    hour = int(os.getenv("SCHEDULER_HOUR_UTC", "3"))
    minute = int(os.getenv("SCHEDULER_MINUTE_UTC", "0"))
    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(
        nightly_backpop_for_yesterday,
        trigger="cron",
        hour=hour,
        minute=minute,
        id="nightly_backpop",
    )
    print(
        f"[scheduler] starting; nightly run at {hour:02d}:{minute:02d} UTC",
        flush=True,
    )
    sched.start()
