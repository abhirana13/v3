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

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler

from app.backpop import drain_backpop_queue, run_backpop
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


def drain_queue_job() -> None:
    """Poll job: execute any manual backpop runs the API has queued. Runs in the worker
    so the triggering HTTP request returns immediately."""
    db = SessionLocal()
    try:
        n = drain_backpop_queue(db)
        if n:
            print(f"[scheduler] drained {n} queued backpop run(s)", flush=True)
    finally:
        db.close()


def metric_monitor_job() -> None:
    """Daily metric monitor (app.monitor): reads yesterday for every enabled game and posts to
    Slack only when something went wrong. Queries Redshift directly (not DuckDB), so it does not
    depend on the backpop having run. Errors are logged, never raised — the scheduler must live."""
    from app.monitor.run import run_all

    try:
        for res in run_all(log=lambda m: print(m, flush=True)):
            print(f"[monitor] {res.game} {res.day}: gated_out={res.gated_out} findings={len(res.scored)} "
                  f"posted={len(res.posted)} slack={res.slack_status} error={res.error}", flush=True)
    except Exception as e:  # pragma: no cover - belt and braces
        print(f"[monitor] job failed: {type(e).__name__}: {e}", flush=True)


def intraday_monitor_job() -> None:
    """Evening hard-break check (app.monitor.intraday): the last 12 complete UTC hours vs the same
    hours on the previous 7 days, for every enabled game; posts only on a hard break (config-class
    ad blocks, a dead placement, an ad-volume collapse), at most once per 12 h per break. One
    lightweight Redshift job (~1 min). Errors are logged, never raised."""
    from app.monitor.intraday import run_all_intraday

    try:
        for res in run_all_intraday(log=lambda m: print(m, flush=True)):
            print(f"[monitor] intraday {res.game} {res.window.label() if res.window else 'n/a'}: "
                  f"breaks={len(res.breaks)} posted={len(res.posted)} suppressed={len(res.suppressed)} "
                  f"skipped={res.skipped} slack={res.slack_status} error={res.error}", flush=True)
    except Exception as e:  # pragma: no cover - belt and braces
        print(f"[monitor] intraday job failed: {type(e).__name__}: {e}", flush=True)


def start_scheduler() -> None:
    hour = int(os.getenv("SCHEDULER_HOUR_UTC", "3"))
    minute = int(os.getenv("SCHEDULER_MINUTE_UTC", "0"))
    poll_seconds = int(os.getenv("BACKPOP_QUEUE_POLL_SECONDS", "5"))
    monitor_enabled = os.getenv("MONITOR_ENABLED", "false").lower() in ("1", "true", "yes")
    monitor_hour = int(os.getenv("MONITOR_HOUR_UTC", "4"))
    monitor_minute = int(os.getenv("MONITOR_MINUTE_UTC", "30"))
    intraday_enabled = os.getenv("MONITOR_INTRADAY_ENABLED", "false").lower() in ("1", "true", "yes")
    intraday_hour = int(os.getenv("MONITOR_INTRADAY_HOUR_UTC", "12"))
    intraday_minute = int(os.getenv("MONITOR_INTRADAY_MINUTE_UTC", "45"))
    # single executor thread => the nightly job and the queue drainer never run at the
    # same time, so there's only ever one DuckDB writer in this process.
    sched = BlockingScheduler(
        timezone="UTC", executors={"default": ThreadPoolExecutor(1)}
    )
    sched.add_job(
        nightly_backpop_for_yesterday,
        trigger="cron",
        hour=hour,
        minute=minute,
        id="nightly_backpop",
    )
    sched.add_job(
        drain_queue_job,
        trigger="interval",
        seconds=poll_seconds,
        id="drain_queue",
        max_instances=1,
        coalesce=True,
    )
    if monitor_enabled:
        # Same single-thread executor: it simply queues behind the nightly backpop if that is
        # still running. 04:30 UTC = 10:00 IST; yesterday's events have had 4.5 h to land.
        sched.add_job(
            metric_monitor_job,
            trigger="cron",
            hour=monitor_hour,
            minute=monitor_minute,
            id="metric_monitor",
            coalesce=True,
            # a laptop asleep at 04:30 UTC runs the job when it wakes, up to 4 h late
            misfire_grace_time=4 * 3600,
        )
    if intraday_enabled:
        # One evening run (12:45 UTC = 18:15 IST by default). Its window is the complete UTC hours
        # before the run — 12 by default (games.toml window_hours), i.e. 00:00–12:00 UTC = 05:30–17:30
        # IST — so a morning change is caught in the evening and an evening change by the next
        # morning's daily read, with no gap between the two.
        sched.add_job(
            intraday_monitor_job,
            trigger="cron",
            hour=intraday_hour,
            minute=intraday_minute,
            id="metric_monitor_intraday",
            coalesce=True,
            misfire_grace_time=4 * 3600,
        )
    print(
        f"[scheduler] starting; nightly at {hour:02d}:{minute:02d} UTC, "
        f"queue drain every {poll_seconds}s"
        + (f", metric monitor at {monitor_hour:02d}:{monitor_minute:02d} UTC" if monitor_enabled else ", metric monitor off")
        + (f", evening check at {intraday_hour:02d}:{intraday_minute:02d} UTC" if intraday_enabled else ", evening check off"),
        flush=True,
    )
    sched.start()
