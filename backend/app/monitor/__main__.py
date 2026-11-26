"""CLI.

  python -m app.monitor run --game arrows [--date 2026-11-24] [--dry-run] [--from-cache DIR] [--no-state] [--json]
  python -m app.monitor gates --game arrows [--date ...] [--from-cache DIR]
  python -m app.monitor readings --game arrows [--date ...] [--from-cache DIR]   # every metric, flagged or not
  python -m app.monitor render --game arrows --date 2026-11-24                     # the SQL that would run
  python -m app.monitor intraday --game arrows [--at 2026-11-23T18:00] [--dry-run] [--from-cache DIR] [--no-state] [--json] [--render]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.monitor import config as mcfg
from app.monitor import detect, fmt, pull, report
from app.monitor.pull import day_num
from app.monitor.run import gates, run_game
from app.monitor.series import build


def _day(s: str | None) -> date:
    return date.fromisoformat(s) if s else datetime.now(timezone.utc).date() - timedelta(days=1)


def _at(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _log(as_json: bool):
    """With --json the summary is the only thing on stdout; progress lines go to stderr."""
    return (lambda m: print(m, file=sys.stderr, flush=True)) if as_json else print


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.monitor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pg = sub.add_parser("pregate", help="run the shared pre-gate job for every configured game and print the gates")
    pg.add_argument("--date")
    for name in ("run", "gates", "readings", "render", "intraday"):
        p = sub.add_parser(name)
        p.add_argument("--game", required=True)
        if name == "intraday":
            p.add_argument("--at", help="UTC time of the run (default: now); the window is the complete hours before it")
            p.add_argument("--render", action="store_true", help="print the SQL that would run and exit")
        else:
            p.add_argument("--date", help="UTC day to read (default: yesterday)")
        p.add_argument("--from-cache", help="folder of cached cuts instead of querying Redshift")
        if name in ("run", "intraday"):
            p.add_argument("--dry-run", action="store_true", help="print the message, post nothing, keep state untouched")
            p.add_argument("--no-state", action="store_true", help="ignore and do not write incident state")
            p.add_argument("--json", action="store_true", help="print the run summary as JSON")
    a = ap.parse_args(argv)
    if a.cmd == "pregate":
        from app.monitor.run import pregate
        print(json.dumps(pregate(mcfg.load(), _day(a.date)), indent=2))
        return 0
    cfg = mcfg.load()[a.game]

    if a.cmd == "intraday":
        from app.monitor import intraday as intra
        at = _at(a.at)
        if a.render:
            print(pull.render_intraday(cfg, intra.hour_num(at) - cfg.intraday.lag_hours))
            return 0
        res = intra.run_intraday(cfg, at, dry_run=a.dry_run, from_cache=Path(a.from_cache) if a.from_cache else None,
                                 use_state=not a.no_state, log=_log(a.json))
        if a.json:
            print(json.dumps(res.summary(), indent=2, default=str))
        else:
            print(f"window {res.window.label() if res.window else 'n/a'} ({res.window.date_label() if res.window else ''})")
            for p, why in res.skipped.items():
                print(f"skipped {cfg.platform_name(p)}: {why}")
            for brk in res.breaks:
                print(f"SEV1 {cfg.platform_name(brk.platform):8s} {brk.kind:13s} {brk.ident:28s} {fmt.usd(brk.impact_usd_per_day):>8s}/day  {brk.headline}")
            for sig in res.suppressed:
                print(f"suppressed by cooldown: {sig}")
            for payload in res.payloads:
                print()
                print(report.to_text(payload))
            print()
            print("slack:", res.slack_status or "NOTHING_TO_POST", "| error:", res.error)
        return 1 if res.error else 0

    day = _day(a.date)
    if a.cmd == "render":
        print(pull.render(cfg, day))
        return 0

    if a.cmd == "run":
        res = run_game(cfg, day, dry_run=a.dry_run, from_cache=Path(a.from_cache) if a.from_cache else None,
                       use_state=not a.no_state, log=_log(a.json))
        if a.json:
            print(json.dumps(res.summary(), indent=2, default=str))
        else:
            print(json.dumps(res.gates))
            if res.gated_out:
                print("gated out — no messages for this game")
            for sc in res.scored:
                print(f"SEV{sc.severity} {cfg.platform_name(sc.reading.platform):8s} {sc.spec.key:24s} "
                      f"{fmt.value(sc.reading.value, sc.spec.unit):>10s} {fmt.arrow_pct(sc.reading.rel_base):>7s} z={sc.reading.z:5.1f} "
                      f"{fmt.usd(sc.impact_usd):>8s}/day  {sc.diagnosis.cause}/{sc.diagnosis.confidence}")
            print()
            if res.payload:
                print(report.to_text(res.payload))
            print()
            print("slack:", res.slack_status, "| error:", res.error)
        return 1 if res.error else 0

    cuts = pull.load_cache(Path(a.from_cache)) if a.from_cache else pull.run_job(cfg, day)
    s = build(cuts, cfg)
    d = day_num(day)
    if a.cmd == "gates":
        print(json.dumps(gates(cfg, s, cuts, d), indent=2))
        return 0
    if a.cmd == "readings":
        for r in detect.read_all(s, cfg, d):
            spec = cfg.metrics[r.metric]
            flag = "FLAG" if r.flagged else "    "
            print(f"{flag} {cfg.platform_name(r.platform):8s} {spec.key:24s} {fmt.value(r.value, spec.unit):>10s} "
                  f"base {fmt.value(r.baseline, spec.unit):>10s} {fmt.arrow_pct(r.rel_base):>7s} dod {fmt.arrow_pct(r.rel_dod):>7s} "
                  f"wow {fmt.arrow_pct(r.rel_wow):>7s} z={r.z if r.z is not None else float('nan'):6.1f} persist={r.persist_days} trend={fmt.arrow_pct(r.trend_rel)}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
