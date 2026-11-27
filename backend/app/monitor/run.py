"""Orchestration: gates -> pull -> series -> detect -> diagnose -> severity -> posting rules -> Slack.

    run_game(cfg, day, dry_run=True)   # the Python entry
    python -m app.monitor run --game arrows --date 2026-11-24 --dry-run      # the CLI (see __main__)"""
from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

from app.monitor import config as mcfg
from app.monitor import detect, pull, report, severity, slack
from app.monitor.config import GameCfg
from app.monitor.diagnose import Ctx, diagnose
from app.monitor.pull import day_date, day_num
from app.monitor.series import Series, adjust_by_day, build
from app.monitor.state import Incident, State


@dataclass
class RunResult:
    game: str
    day: date
    gates: dict
    gated_out: bool
    readings: list = field(default_factory=list)
    scored: list = field(default_factory=list)       # every flagged finding, scored
    posted: list = field(default_factory=list)       # the subset shown in the message
    recoveries: list = field(default_factory=list)
    payload: dict | None = None
    slack_status: str = ""
    error: str | None = None

    def summary(self) -> dict:
        return {
            "game": self.game, "day": self.day.isoformat(), "gates": self.gates, "gated_out": self.gated_out,
            "findings": [{"platform": s.reading.platform, "metric": s.spec.key, "value": s.reading.value,
                          "baseline": s.reading.baseline, "rel": s.reading.rel_base, "z": s.reading.z,
                          "severity": s.severity, "impact_usd": s.impact_usd, "cause": s.diagnosis.cause,
                          "confidence": s.diagnosis.confidence, "headline": s.diagnosis.headline,
                          "persist_days": s.reading.persist_days} for s in self.scored],
            "posted": len(self.posted), "recoveries": self.recoveries, "slack": self.slack_status, "error": self.error,
        }


# --- gates -----------------------------------------------------------------------------------------

def gates(cfg: GameCfg, s: Series, cuts: dict, day: int) -> dict:
    """Revenue gate from the full pull; UA gate from Adjust cost (games with an adjust_app) or from
    attributed paid installs (the rest). Same rules as gates_from_pregate."""
    base = [d for d in range(day - 6, day + 1) if d in s.days]
    rev = [s.get("ad_revenue", "all", d) for d in base]
    rev = [v for v in rev if v is not None]
    rev7 = mean(rev) if rev else 0.0
    out = {"ad_revenue_7d": rev7, "revenue_ok": rev7 >= cfg.gates.min_ad_revenue_per_day_usd, "ua_source": cfg.ua_source}
    if cfg.ua_source == "adjust":
        adj = adjust_by_day(cuts).get("all", {})
        costs = [adj[d]["cost"] for d in range(day - 7, day + 1) if d in adj and adj[d]["cost"] > 0]
        ua7 = mean(costs) if costs else 0.0
        out.update(ua_spend_7d=ua7, ua_ok=ua7 >= cfg.gates.min_ua_spend_per_day_usd)
    else:
        paid = [s.get("paid_installs", "all", d) for d in base]
        paid = [v for v in paid if v is not None]
        p7 = mean(paid) if paid else 0.0
        out.update(paid_installs_7d=p7, ua_ok=p7 >= cfg.gates.min_paid_installs_per_day)
    return out


def gates_from_pregate(cfg: GameCfg, pg: dict[str, list[dict]], day: int) -> dict:
    """The same gates from the shared pre-gate job (sql/pregate.sql), before any per-game pull."""
    from app.monitor.frames import num
    days7 = set(range(day - 6, day + 1))
    rev_by_day: dict[int, float] = {}
    for r in pg.get("pg_revenue", []):
        if int(r["game_id"]) == cfg.game_id and int(r["day"]) in days7:
            rev_by_day[int(r["day"])] = rev_by_day.get(int(r["day"]), 0.0) + num(r.get("rev"))
    rev7 = mean(rev_by_day.values()) if rev_by_day else 0.0
    out = {"ad_revenue_7d": rev7, "revenue_ok": rev7 >= cfg.gates.min_ad_revenue_per_day_usd, "ua_source": cfg.ua_source}
    if cfg.ua_source == "adjust":
        cost_by_day: dict[int, float] = {}
        for r in pg.get("pg_adjust", []):
            if str(r.get("app")) != cfg.adjust_app:
                continue
            d = r.get("day")
            d = day_num(date.fromisoformat(str(d)[:10])) if not isinstance(d, int) else d
            if d in set(range(day - 7, day + 1)):
                cost_by_day[d] = cost_by_day.get(d, 0.0) + num(r.get("cost"))
        costs = [v for v in cost_by_day.values() if v > 0]
        ua7 = mean(costs) if costs else 0.0
        out.update(ua_spend_7d=ua7, ua_ok=ua7 >= cfg.gates.min_ua_spend_per_day_usd)
    else:
        paid_by_day: dict[int, float] = {}
        for r in pg.get("pg_attrib", []):
            if int(r["game_id"]) == cfg.game_id and int(r["day"]) in days7 and num(r.get("paid")) == 1:
                paid_by_day[int(r["day"])] = paid_by_day.get(int(r["day"]), 0.0) + num(r.get("installs"))
        p7 = sum(paid_by_day.values()) / 7.0
        out.update(paid_installs_7d=p7, ua_ok=p7 >= cfg.gates.min_paid_installs_per_day)
    return out


def gates_path() -> Path:
    return mcfg.data_dir() / "gates.json"


def save_gates(day: date, result: dict) -> None:
    gp = gates_path()
    gp.parent.mkdir(parents=True, exist_ok=True)
    gp.write_text(json.dumps({"day": day.isoformat(), "games": result}, indent=2, sort_keys=True))


def load_gates(day: date | None = None) -> dict | None:
    """The last pre-gate result, or None when there is none for `day` (any day when None)."""
    gp = gates_path()
    if not gp.exists():
        return None
    raw = json.loads(gp.read_text() or "{}")
    if day is not None and raw.get("day") != day.isoformat():
        return None
    return raw.get("games")


def pregate(games: dict[str, GameCfg], day: date, log=print) -> dict[str, dict]:
    """Run the shared pre-gate job and decide, per enabled game, whether to pull at all."""
    enabled = [g for g in games.values() if g.enabled]
    if not enabled:
        return {}
    pg = pull.run_sql(pull.render_pregate(enabled, day), log=log)
    out = {g.key: gates_from_pregate(g, pg, day_num(day)) for g in enabled}
    for k, v in out.items():
        log(f"[monitor] pregate {k}: " + json.dumps(v))
    save_gates(day, out)
    return out


# --- posting rules ---------------------------------------------------------------------------------

def _sig(cfg: GameCfg, sc: severity.Scored) -> str:
    return f"{cfg.key}:{sc.reading.platform}:{sc.spec.key}:{sc.reading.bad_direction}"


def apply_posting_rules(cfg: GameCfg, day: date, scored: list, readings: list, state: State) -> tuple[list, list[str]]:
    """Decide which findings go into today's message and update the incident state in place.
    Returns (findings to post, recovery lines)."""
    today = day.isoformat()
    yesterday = (day - timedelta(days=1)).isoformat()
    post: list = []
    open_sigs = set()
    for sc in scored:
        sig = _sig(cfg, sc)
        open_sigs.add(sig)
        inc = state.incidents.get(sig)
        ongoing = inc is not None and inc.last_day >= yesterday
        if not ongoing:
            inc = Incident(sig, today, today, sc.severity, None, 0, sc.severity, sc.diagnosis.cause)
            state.incidents[sig] = inc
            if sc.severity <= cfg.post_min_severity or (sc.severity == 3 and sc.reading.persist_days >= cfg.sev3_persist_days):
                post.append(sc)
        else:
            escalated = sc.severity < inc.last_severity
            days_since_post = (day - date.fromisoformat(inc.last_posted_day)).days if inc.last_posted_day else 999
            if sc.severity == 1 and (cfg.sev1_repost_daily or escalated or inc.posted_count == 0):
                post.append(sc)
            elif sc.severity == 2 and (escalated or inc.posted_count == 0 or days_since_post >= cfg.sev2_repost_every_days):
                post.append(sc)
            elif sc.severity == 3 and (escalated or (inc.posted_count == 0 and sc.reading.persist_days >= cfg.sev3_persist_days)):
                post.append(sc)
            inc.last_day = today
            inc.last_severity = sc.severity
            inc.peak_severity = min(inc.peak_severity, sc.severity)
            inc.cause = sc.diagnosis.cause
    # if anything posts, the message is the day's full picture: include the rest of today's findings
    if post:
        post = list(scored)
    for sc in post:
        inc = state.incidents[_sig(cfg, sc)]
        inc.last_posted_day = today
        inc.posted_count += 1

    # recoveries: open yesterday, not flagged today, and the reading is back inside 1.5 sigma
    recoveries: list[str] = []
    by_key = {(r.platform, r.metric): r for r in readings}
    for sig, inc in list(state.incidents.items()):
        if sig in open_sigs or inc.last_day != yesterday:
            continue
        _, plat, metric, _ = sig.split(":", 3)
        r = by_key.get((int(plat) if plat.isdigit() else plat, metric))
        if r is None or inc.peak_severity != 1 or not cfg.post_recovery_for_sev1:
            continue
        spec = cfg.metrics.get(metric)
        label = f"{cfg.platform_name(plat)} {spec.label if spec else metric}"
        days_open = (day - date.fromisoformat(inc.first_day)).days
        val = report.fmt.value(r.value, spec.unit if spec else 'rate')
        if r.z is not None and abs(r.z) <= 1.5:
            recoveries.append(f"{label} is back at {val} ({report.fmt.arrow_pct(r.rel_base)} vs 7d) after {days_open} day(s)")
        else:
            # no longer beyond the alarm line, but not back to normal either: say so rather than go quiet
            recoveries.append(f"{label} is no longer flagged — {val}, still {report.fmt.arrow_pct(r.rel_base)} vs 7d "
                              f"after {days_open} day(s); watching")
    return post, recoveries


# --- the run ---------------------------------------------------------------------------------------

def run_game(cfg: GameCfg, day: date, dry_run: bool = False, cache_dir: Path | None = None,
             from_cache: Path | None = None, use_state: bool = True, log=print) -> RunResult:
    res = RunResult(cfg.key, day, {}, False)
    try:
        if from_cache:
            cuts = pull.load_cache(from_cache)
            log(f"[monitor] {cfg.key}: loaded {len(cuts)} cuts from {from_cache}")
        else:
            cuts = pull.run_job(cfg, day, log=log)
            folder = cache_dir or (mcfg.data_dir() / cfg.key / day.isoformat())
            pull.save_cache(cuts, folder)
            log(f"[monitor] {cfg.key}: cached to {folder}")
            prune_cache(mcfg.data_dir() / cfg.key, keep_days=int(os.environ.get("MONITOR_CACHE_KEEP_DAYS", "14")), log=log)
        s = build(cuts, cfg)
        d = day_num(day)
        if d not in s.days:
            raise RuntimeError(f"no activity rows for {day} — the pull returned nothing for the read day")
        res.gates = gates(cfg, s, cuts, d)
        if not (res.gates["revenue_ok"] and res.gates["ua_ok"]):
            res.gated_out = True
            log(f"[monitor] {cfg.key}: gated out ({json.dumps(res.gates)})")
            return res

        readings = detect.read_all(s, cfg, d)
        res.readings = readings
        ctx = Ctx(cuts, s, cfg, d)
        scored: list[severity.Scored] = []
        for r in readings:
            if not r.flagged:
                continue
            spec = cfg.metrics[r.metric]
            diag = diagnose(ctx, r, spec)
            scored.append(severity.score(ctx, r, spec, diag))
        # a pipeline finding on a platform silences the rest of that platform (the numbers are partial)
        pipe_plats = {sc.reading.platform for sc in scored if sc.diagnosis.cause == "pipeline"}
        if pipe_plats:
            scored = [sc for sc in scored if sc.reading.platform not in pipe_plats or sc.diagnosis.cause == "pipeline"]
            seen = set()
            dedup = []
            for sc in scored:
                k = (sc.reading.platform, sc.diagnosis.cause == "pipeline")
                if sc.diagnosis.cause == "pipeline" and k in seen:
                    continue
                seen.add(k)
                dedup.append(sc)
            scored = dedup
        # ARPDAU repeats ad revenue unless DAU moved too: keep the one the reader acts on
        flagged_keys = {(sc.reading.platform, sc.spec.key) for sc in scored}
        scored = [sc for sc in scored if not (sc.spec.key == "arpdau"
                                              and (sc.reading.platform, "ad_revenue") in flagged_keys
                                              and (sc.reading.platform, "dau") not in flagged_keys)]
        res.scored = scored

        state_path = mcfg.data_dir() / f"state_{cfg.key}.json"
        state = State.load(state_path) if use_state else State()
        post, recoveries = apply_posting_rules(cfg, day, scored, readings, state)
        res.posted, res.recoveries = post, recoveries
        state.last_run_day = day.isoformat()

        footer = {
            "ad_revenue_7d": res.gates["ad_revenue_7d"], "ua_spend_7d": res.gates.get("ua_spend_7d"),
            "paid_installs_7d": res.gates.get("paid_installs_7d"),
            "dau": {p: s.get("dau", p, d) for p in s.platforms},
            "frontier": _frontier_text(ctx, s.platforms, d),
            "intraday": _intraday_notes(state, day),
            # broken days the baselines stepped over (shown so a "vs 7d" figure can be read right)
            "baseline_skips": {p: sorted(t for t in s.dead_days(p) if d - 2 * cfg.baseline_days <= t < d) for p in s.platforms},
        }
        if post:
            res.payload = report.build_message(cfg, day, post, recoveries, footer)
        elif recoveries:
            res.payload = report.recovery_message(cfg, day, recoveries, footer)
        elif cfg.weekly_summary and day.weekday() == 6:   # Sunday's data posts on Monday morning
            res.payload = report.weekly_message(cfg, day, _weekly_rows(cfg, readings))

        if res.payload and not dry_run:
            res.slack_status = slack.post(cfg.webhook_url(), res.payload)
        elif res.payload:
            res.slack_status = "DRY_RUN"
        else:
            res.slack_status = "NOTHING_TO_POST"
        if use_state and not dry_run:
            state.save(state_path)
        return res
    except Exception as e:  # the scheduler must never die on a monitor error
        res.error = f"{type(e).__name__}: {e}"
        log("[monitor] ERROR " + res.error)
        log(traceback.format_exc())
        return res


def _intraday_notes(state: State, day: date) -> list[str]:
    """Intraday alerts (intraday.py) posted on the read day or the morning after it, so the daily
    message carries them even when the break was fixed before the day ended."""
    out = []
    for ip in sorted(state.intraday.values(), key=lambda x: x.last_posted_at or ""):
        if not ip.last_posted_at:
            continue
        try:
            dt = datetime.fromisoformat(ip.last_posted_at)
        except ValueError:
            continue
        if dt.date() in (day, day + timedelta(days=1)):
            out.append(f"intraday alert {dt:%b %d %H:%M} UTC: {ip.headline}")
    return out


def prune_cache(game_dir: Path, keep_days: int = 14, log=print) -> int:
    """Delete cached pull folders older than keep_days (each daily pull is ~10–20 MB; the prod box
    has run out of disk before). Folder names are ISO dates; anything else is left alone."""
    import shutil
    removed = 0
    if not game_dir.exists():
        return 0
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=keep_days)).isoformat()
    for child in list(game_dir.iterdir()) + list((game_dir / "intraday").iterdir() if (game_dir / "intraday").is_dir() else []):
        name = child.name
        if not child.is_dir() or len(name) < 10 or not name[:10].count("-") == 2:
            continue
        if name[:10] < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    if removed:
        log(f"[monitor] pruned {removed} cached pull folder(s) older than {keep_days} days under {game_dir}")
    return removed


def _frontier_text(ctx: Ctx, platforms: list, d: int) -> str:
    hrs = [max(ctx.hours_with_imps(p, d), default=-1) for p in platforms]
    h = min(hrs) if hrs else -1
    return f"{h:02d}:59 UTC" if h >= 0 else "n/a"


def _weekly_rows(cfg: GameCfg, readings: list) -> list:
    rows = []
    core = ("ad_revenue", "arpdau", "dau", "installs", "it_imps_per_dau", "rv_imps_per_dau", "it_ecpm", "rv_ecpm", "wins_per_dau", "rolling_d1")
    for r in readings:
        if r.metric in core and r.week_mean is not None and r.prior_week_mean:
            rows.append((cfg.platform_name(r.platform), cfg.metrics[r.metric].label, r.week_mean, r.prior_week_mean, r.trend_rel))
    return rows


def run_all(day: date | None = None, dry_run: bool = False, log=print, use_pregate: bool = True) -> list[RunResult]:
    """Every enabled game: one shared pre-gate job, then a full pull only for the games that pass
    both gates. A game that fails is logged and reported as gated out without touching Redshift again."""
    day = day or (datetime.now(timezone.utc).date() - timedelta(days=1))
    games = mcfg.load()
    out = []
    pre: dict[str, dict] = {}
    if use_pregate:
        try:
            pre = pregate(games, day, log=log)
        except Exception as e:  # the pre-gate is an optimisation: fall back to per-game gates
            log(f"[monitor] pregate failed ({type(e).__name__}: {e}); running every game")
            pre = {}
    for key, cfg in games.items():
        if not cfg.enabled:
            continue
        g = pre.get(key)
        if g is not None and not (g["revenue_ok"] and g["ua_ok"]):
            res = RunResult(cfg.key, day, g, True)
            res.slack_status = "GATED_OUT"
            log(f"[monitor] {key}: gated out by the pre-gate ({json.dumps(g)})")
            out.append(res)
            continue
        out.append(run_game(cfg, day, dry_run=dry_run, log=log))
    notify_failures(games, day, out, dry_run=dry_run, log=log, label="daily run")
    return out


def notify_failures(games: dict, day: date, results: list, dry_run: bool, log=print, label: str = "run") -> str:
    """One ⚠️ line to the channel when a run could not read its games, so a quiet morning is never
    mistaken for a healthy one. Posted through the first enabled game's webhook."""
    failed = [r for r in results if getattr(r, "error", None)]
    if not failed:
        return ""
    cfg = next((c for c in games.values() if c.enabled), None)
    if cfg is None:
        return ""
    names = ", ".join(games[r.game].name if r.game in games else r.game for r in failed)
    err = failed[0].error or ""
    text = (f"⚠️ Metric monitor: the {label} for {day.strftime('%a %b %d')} could not read {names} — "
            f"`{err[:160]}`. Nothing was checked for them; the next scheduled run will try again, "
            f"or run `python -m app.monitor run --game <game>` by hand.")
    payload = {"text": text, "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]}
    if dry_run:
        log("[monitor] (dry run) failure notice: " + text)
        return "DRY_RUN"
    try:
        status = slack.post(cfg.webhook_url(), payload)
    except Exception as e:  # never let the notice itself take the scheduler down
        status = f"notice failed: {type(e).__name__}: {e}"
    log("[monitor] failure notice: " + status)
    return status
