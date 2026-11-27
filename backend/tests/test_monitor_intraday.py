"""Intraday hard-break check (app.monitor.intraday) on synthetic hourly cuts shaped like
sql/intraday.sql's output. The headline case is the iOS Nov 23 2026 break seen at 18:00 UTC: the
win-ad test's variant had no trigger point configured from ~13:00, so every level win in that arm
was refused (TriggerPointNotConfigured) and its level_win_next_button interstitial died."""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.monitor import config as mcfg
from app.monitor import intraday as intra
from app.monitor import report
from app.monitor.intraday import Window, WCtx, detect_breaks, hour_num, record, run_intraday, under_cooldown
from app.monitor.pull import guard, render_intraday, save_cache, split_statements
from app.monitor.state import State

AT = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)
END_HR = hour_num(AT)
H = 6
WIN = Window(END_HR, H, 7)
START_HR = END_HR - 24 * 8
PLACEMENTS = (("level_win_next_button", 0.65), ("out_of_lives_restart_button", 0.30), ("dc_level_win_continue_button", 0.05))


@pytest.fixture(scope="module")
def cfg():
    c = mcfg.load()["arrows"]
    # the synthetic world is built hour by hour for a 6-hour window; production reads 12 (games.toml)
    c.intraday.window_hours = H
    return c


def make_hourly_cuts(mode: str = "config", break_from_hr: int = END_HR - 5, break_platform: int = 2,
                     act: dict[int, int] | None = None, seed: int = 3, end_hr: int = END_HR) -> dict[str, list[dict]]:
    """8 days of hourly cuts for two platforms with a diurnal shape, chronic non-config blocks
    (MinLevel) and a chronic config-class one (ManagerNotFound, which must never fire on its own),
    and a 50/50 `interstitial_placement` test plus a finished `league` test logged 100% control.
    mode: quiet | config (variant_a loses the win placement and logs TriggerPointNotConfigured per
    win from break_from_hr) | placement (the whole platform loses the win placement, no block logged) |
    volume (every IT placement halves, wins flat) | engagement (IT halves because wins halve) |
    thin (quiet, but the window's last hour has 20% of its players). Window-level distinct players
    are 0.5 x the hourly sum (a player is active ~2 of the 6 hours)."""
    rnd = random.Random(seed)
    win, start_hr = Window(end_hr, H, 7), end_hr - 24 * 8
    act = act or {1: 700, 2: 1000}
    it_ph = {1: 1.3, 2: 1.2}            # IT imps per active player-hour
    rv_ph = {1: 0.12, 2: 0.16}
    wins_ph = {1: 1.0, 2: 1.1}
    ecpm = {1: 18.0, 2: 40.0}
    cuts = {k: [] for k in ("ih_act", "ih_ads", "ih_blocked", "ih_play", "iw_act", "iw_blocked",
                            "iw_arm_act", "iw_arm_ads", "iw_arm_blocked", "iw_arm_play")}
    arm_hour: dict[tuple, float] = {}   # (p, hr, arm, ad_type, placement) -> imps
    blk_arm_hour: dict[tuple, list] = {}  # (p, hr, arm, reason) -> [n, players]
    for p, a in act.items():
        for hr in range(start_hr, end_hr + 1):
            hod = hr % 24
            diurnal = 1 + 0.3 * math.sin((hod - 2) / 24 * 2 * math.pi)
            players = int(a * diurnal * (1 + rnd.uniform(-0.04, 0.04)))
            if hr == end_hr:
                players = int(players * 0.3)          # the partial current hour (the frontier)
            if mode == "thin" and hr == end_hr - 1 and p == break_platform:
                players = int(players * 0.2)
            broken = p == break_platform and hr >= break_from_hr and mode in ("config", "placement", "volume", "engagement")
            cuts["ih_act"].append({"platform": p, "hr": hr, "players": players})
            wins = wins_ph[p] * players * (0.5 if (broken and mode == "engagement") else 1.0)
            cuts["ih_play"].append({"platform": p, "hr": hr, "winners": int(players * 0.7), "wins": int(wins)})
            for plc, share in PLACEMENTS:
                healthy = it_ph[p] * players * share
                per_arm = healthy / 2
                for arm in ("control", "variant_a"):
                    n = per_arm
                    if broken:
                        if mode == "config" and plc == "level_win_next_button" and arm == "variant_a":
                            n = 0.0
                        elif mode == "placement" and plc == "level_win_next_button":
                            n = 0.0
                        elif mode in ("volume", "engagement"):
                            n *= 0.5
                    arm_hour[(p, hr, arm, "interstitial", plc)] = n
                n_tot = sum(arm_hour[(p, hr, arm, "interstitial", plc)] for arm in ("control", "variant_a"))
                cuts["ih_ads"].append({"platform": p, "hr": hr, "ad_type": "interstitial", "placement": plc, "imps": int(n_tot),
                                       "players": int(n_tot / 2), "rev": n_tot * ecpm[p] / 1000})
            rv = rv_ph[p] * players
            for arm in ("control", "variant_a"):
                arm_hour[(p, hr, arm, "rewarded", "revive")] = rv / 2
            cuts["ih_ads"].append({"platform": p, "hr": hr, "ad_type": "rewarded", "placement": "revive", "imps": int(rv),
                                   "players": int(rv / 3), "rev": rv * ecpm[p] * 1.4 / 1000})
            # blocks: chronic MinLevel (a rule working) and ManagerNotFound (config-class, chronic)
            for reason, per, shr, cls in (("MinLevel", 0.4, 0.2, False), ("ManagerNotFound", 0.3, 0.1, True)):
                cuts["ih_blocked"].append({"platform": p, "hr": hr, "ad_type": "interstitial", "reason": reason,
                                           "n": int(players * per), "players": int(players * shr)})
                for arm in ("control", "variant_a"):
                    blk_arm_hour[(p, hr, arm, reason)] = [players * per / 2, players * shr / 2]
            if broken and mode == "config":
                n_blk, pl_blk = wins * 0.5 * 1.02, players * 0.5 * 0.8
                cuts["ih_blocked"].append({"platform": p, "hr": hr, "ad_type": "interstitial", "reason": "TriggerPointNotConfigured",
                                           "n": int(n_blk), "players": int(pl_blk)})
                blk_arm_hour[(p, hr, "variant_a", "TriggerPointNotConfigured")] = [n_blk, pl_blk]
        for w in range(0, 8):
            hrs = set(win.hours_of(w))
            hp = sum(r["players"] for r in cuts["ih_act"] if r["platform"] == p and r["hr"] in hrs)
            act_w = int(hp * 0.5)
            cuts["iw_act"].append({"platform": p, "w": w, "players": act_w})
            by_reason: dict[str, list] = {}
            for r in cuts["ih_blocked"]:
                if r["platform"] == p and r["hr"] in hrs:
                    acc = by_reason.setdefault(r["reason"], [0, 0])
                    acc[0] += r["n"]
                    acc[1] += r["players"]
            for reason, (n, pl) in by_reason.items():
                cuts["iw_blocked"].append({"platform": p, "w": w, "reason": reason, "n": n, "players": int(pl * 0.5)})
            wins_w = sum(r["wins"] for r in cuts["ih_play"] if r["platform"] == p and r["hr"] in hrs)
            for arm in ("control", "variant_a"):
                cuts["iw_arm_act"].append({"platform": p, "w": w, "exp_key": "interstitial_placement", "arm": arm, "players": act_w // 2})
                cuts["iw_arm_play"].append({"platform": p, "w": w, "exp_key": "interstitial_placement", "arm": arm, "wins": wins_w // 2})
                by_plc: dict[tuple, float] = {}
                for (q, hr, ar, t, plc), n in arm_hour.items():
                    if q == p and ar == arm and hr in hrs:
                        by_plc[(t, plc)] = by_plc.get((t, plc), 0.0) + n
                for (t, plc), n in by_plc.items():
                    cuts["iw_arm_ads"].append({"platform": p, "w": w, "exp_key": "interstitial_placement", "arm": arm,
                                               "ad_type": t, "placement": plc, "imps": int(n)})
                by_r: dict[str, list] = {}
                for (q, hr, ar, reason), (n, pl) in blk_arm_hour.items():
                    if q == p and ar == arm and hr in hrs:
                        acc = by_r.setdefault(reason, [0.0, 0.0])
                        acc[0] += n
                        acc[1] += pl
                for reason, (n, pl) in by_r.items():
                    cuts["iw_arm_blocked"].append({"platform": p, "w": w, "exp_key": "interstitial_placement", "arm": arm,
                                                   "ad_type": "interstitial", "reason": reason, "n": int(n), "players": int(pl * 0.5)})
            # a finished test: everyone logged as control (no contrast, must be skipped by attribution)
            cuts["iw_arm_act"].append({"platform": p, "w": w, "exp_key": "league", "arm": "control", "players": int(act_w * 0.98)})
            cuts["iw_arm_play"].append({"platform": p, "w": w, "exp_key": "league", "arm": "control", "wins": int(wins_w * 0.98)})
            for reason, (n, pl) in by_reason.items():
                cuts["iw_arm_blocked"].append({"platform": p, "w": w, "exp_key": "league", "arm": "control", "ad_type": "interstitial",
                                               "reason": reason, "n": int(n * 0.98), "players": int(pl * 0.5 * 0.98)})
    return cuts


# --- the window ------------------------------------------------------------------------------------

def test_window_arithmetic():
    assert WIN.label() == "12:00–18:00 UTC"
    assert WIN.hours_of(0) == list(range(END_HR - 6, END_HR))
    assert WIN.hours_of(1) == list(range(END_HR - 30, END_HR - 24))
    assert WIN.date_label() == "Mon Nov 23"
    assert hour_num(datetime(2026, 11, 23, 18, 25)) == END_HR          # naive = UTC, floor to the hour
    assert intra.hour_dt(END_HR) == AT


# --- quiet / chronic -------------------------------------------------------------------------------

def test_quiet_world_posts_nothing(cfg):
    ctx = WCtx(make_hourly_cuts(mode="quiet"), cfg, WIN)
    assert ctx.platforms == [1, 2]
    breaks, skipped = detect_breaks(ctx)
    assert breaks == [] and skipped == {}
    # the chronic config-class reason sits well above the 'from ~0' bar on every day: never a break
    assert ctx.blocks(2, 0)["ManagerNotFound"]["n"] / ctx.active(2, 0) > 0.3


# --- the Nov 23 18:00 replay -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def world(cfg):
    cuts = make_hourly_cuts(mode="config")
    ctx = WCtx(cuts, cfg, WIN)
    breaks, skipped = detect_breaks(ctx)
    return cuts, ctx, breaks, skipped


def test_config_break_fires_sev1_on_ios_only(world):
    cuts, ctx, breaks, skipped = world
    assert skipped == {}
    assert [b.platform for b in breaks] == [2]
    b = breaks[0]
    assert b.kind == "config_block" and b.ident == "TriggerPointNotConfigured"
    assert b.signature(ctx.cfg) == "arrows:2:intraday:config_block:TriggerPointNotConfigured"


def test_config_break_is_attributed_to_the_arm_and_the_dead_placement(world):
    *_, breaks, _ = world
    b = breaks[0]
    assert b.tags.get("exp_key") == "interstitial_placement" and b.tags.get("arm") == "variant_a"
    assert b.tags.get("placement") == "level_win_next_button"
    joined = " ".join(b.evidence)
    assert "TriggerPointNotConfigured" in joined and "variant_a" in joined and "level_win_next_button" in joined
    assert "blocks per level win" in joined
    assert "remote config" in b.fix and "interstitial_placement" in b.fix
    assert b.since_hr == END_HR - 5 and not b.since_before
    assert "since ~13:00 UTC" in b.headline
    # the money line: the variant lost its 65% win placement for 5 of 6 hours (~1,900 imps at $40 CPM,
    # the window being ~a quarter of the day's impressions) => roughly -$300/day
    assert b.impact_usd_per_day is not None and -600 < b.impact_usd_per_day < -150
    # the other rules' readings ride along: IT fell but not to the volume bar, RV and wins held
    keys = {r.key: r for r in b.rates}
    assert keys["blocked_config"].severe and keys["blocked_config"].now >= 0.5
    assert 0.6 < keys["it_imps"].ratio < 0.85 and not keys["it_imps"].severe
    assert keys["wins"].ratio > 0.95 and keys["rv_imps"].ratio > 0.9


def test_a_break_in_the_last_hour_waits_for_the_next_run(cfg):
    ctx = WCtx(make_hourly_cuts(mode="config", break_from_hr=END_HR - 1), cfg, WIN)
    breaks, _ = detect_breaks(ctx)
    assert breaks == []            # one broken hour in six is under 0.5 blocks per player


# --- the other hard breaks -------------------------------------------------------------------------

def test_dead_placement_without_a_block_reason(cfg):
    ctx = WCtx(make_hourly_cuts(mode="placement", break_from_hr=END_HR - 6), cfg, WIN)
    breaks, _ = detect_breaks(ctx)
    assert len(breaks) == 1
    b = breaks[0]
    assert b.kind == "placement" and b.ident == "level_win_next_button" and b.platform == 2
    assert any("other placements held" in e for e in b.evidence)
    assert any("no new config-class block reason" in x for x in b.ruled_out)
    assert b.since_before or b.since_hr == END_HR - 6


def test_volume_collapse_fires_only_when_play_holds(cfg):
    ctx = WCtx(make_hourly_cuts(mode="volume", break_from_hr=END_HR - 6), cfg, WIN)
    breaks, _ = detect_breaks(ctx)
    assert len(breaks) == 1 and breaks[0].kind == "volume" and breaks[0].ident == "interstitial"
    assert any("play held" in e for e in breaks[0].evidence)
    ctx2 = WCtx(make_hourly_cuts(mode="engagement", break_from_hr=END_HR - 6), cfg, WIN)
    breaks2, _ = detect_breaks(ctx2)
    assert breaks2 == []           # the ads fell because the play fell: not a hard break


def test_thin_last_hour_is_not_read(cfg):
    ctx = WCtx(make_hourly_cuts(mode="thin"), cfg, WIN)
    breaks, skipped = detect_breaks(ctx)
    assert breaks == []
    assert 2 in skipped and "incomplete" in skipped[2] and 1 not in skipped


def test_small_platform_is_not_read(cfg):
    ctx = WCtx(make_hourly_cuts(mode="config", act={1: 700, 2: 60}), cfg, WIN)
    breaks, skipped = detect_breaks(ctx)
    assert breaks == [] and "active players" in skipped[2]


# --- message ---------------------------------------------------------------------------------------

def test_message_format(world, cfg):
    *_, breaks, _ = world
    payload = report.intraday_message(cfg, WIN, breaks[0], {"active": {1: 2100, 2: 3000}, "frontier": "18:xx UTC"})
    assert payload["blocks"][0]["type"] == "header"
    assert payload["blocks"][0]["text"]["text"] == "🔴 Arrows · intraday · 12:00–18:00 UTC"
    text = report.to_text(payload)
    assert "<!here>" in text
    for needle in ("TriggerPointNotConfigured", "interstitial_placement", "variant_a", "level_win_next_button",
                   "What moved", "Why — iOS", "Fix:", "Mon Nov 23", "SEV1", "/day at this rate", "active players in the window"):
        assert needle in text, needle
    for b in payload["blocks"]:
        if b["type"] == "section":
            assert len(b["text"]["text"]) <= 3000


# --- cooldown --------------------------------------------------------------------------------------

def test_cooldown_posts_at_most_once_per_12h(tmp_path):
    st = State()
    sig = "arrows:2:intraday:config_block:TriggerPointNotConfigured"
    assert under_cooldown(st, sig, AT, 12) is None
    record(st, sig, AT, posted=True, headline="iOS: the game refused its own ads")
    assert under_cooldown(st, sig, AT + timedelta(hours=6), 12) == AT
    assert under_cooldown(st, sig, AT + timedelta(hours=11, minutes=59), 12) == AT
    assert under_cooldown(st, sig, AT + timedelta(hours=12), 12) is None
    # a run that saw it under cooldown updates last_at, not the anchor
    record(st, sig, AT + timedelta(hours=6), posted=False, headline="")
    assert st.intraday[sig].last_posted_at == AT.isoformat(timespec="minutes") and st.intraday[sig].posted_count == 1
    # round trip through the state file next to the daily incidents
    path = tmp_path / "state_arrows.json"
    st.save(path)
    back = State.load(path)
    assert back.intraday[sig].headline == "iOS: the game refused its own ads"
    assert json.loads(path.read_text())["intraday"][sig]["posted_count"] == 1


def test_run_from_cache_posts_once_then_respects_the_cooldown(tmp_path, monkeypatch, cfg):
    cache = tmp_path / "cache"
    save_cache(make_hourly_cuts(mode="config"), cache)
    monkeypatch.setenv("MONITOR_DATA_DIR", str(tmp_path / "data"))
    sent = []
    monkeypatch.setattr(intra.slack, "post", lambda url, payload: (sent.append(payload), "SLACK_POSTED=ok")[1])
    # dry run: the message is built, nothing is sent, no state is written
    res = run_intraday(cfg, AT, dry_run=True, from_cache=cache)
    assert res.error is None and len(res.posted) == 1 and res.slack_status == ["DRY_RUN"] and sent == []
    assert not (tmp_path / "data" / "state_arrows.json").exists()
    # the real thing
    res = run_intraday(cfg, AT, from_cache=cache)
    assert res.error is None and res.slack_status == ["SLACK_POSTED=ok"] and len(sent) == 1
    assert sent[0]["blocks"][0]["text"]["text"] == "🔴 Arrows · intraday · 12:00–18:00 UTC"
    st = State.load(tmp_path / "data" / "state_arrows.json")
    assert list(st.intraday) == ["arrows:2:intraday:config_block:TriggerPointNotConfigured"]
    # six hours later, still broken: suppressed; the daily run's footer still carries it
    save_cache(make_hourly_cuts(mode="config", break_from_hr=END_HR - 5, end_hr=END_HR + 6), cache)
    res2 = run_intraday(cfg, AT + timedelta(hours=6), from_cache=cache)
    assert res2.error is None and res2.posted == [] and len(res2.suppressed) == 1 and len(sent) == 1
    from app.monitor.run import _intraday_notes
    notes = _intraday_notes(State.load(tmp_path / "data" / "state_arrows.json"), AT.date())
    assert len(notes) == 1 and "intraday alert Nov 23 18:00 UTC" in notes[0]
    assert _intraday_notes(State.load(tmp_path / "data" / "state_arrows.json"), AT.date() - timedelta(days=3)) == []
    # fourteen hours later: the cooldown has passed, it posts again
    save_cache(make_hourly_cuts(mode="config", break_from_hr=END_HR - 5, end_hr=END_HR + 14), cache)
    res3 = run_intraday(cfg, AT + timedelta(hours=14), from_cache=cache)
    assert res3.error is None and len(res3.posted) == 1 and len(sent) == 2


def test_run_summary_is_json(cfg, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    save_cache(make_hourly_cuts(mode="config"), cache)
    monkeypatch.setenv("MONITOR_DATA_DIR", str(tmp_path / "data"))
    res = run_intraday(cfg, AT, dry_run=True, from_cache=cache, use_state=False)
    s = json.loads(json.dumps(res.summary(), default=str))
    assert s["window"] == "12:00–18:00 UTC" and s["breaks"][0]["kind"] == "config_block"
    assert s["breaks"][0]["since"].startswith("2026-11-23T13:00")


# --- SQL plumbing ----------------------------------------------------------------------------------

def test_intraday_sql_renders_and_passes_the_read_only_guard(cfg):
    sql = render_intraday(cfg, END_HR)
    assert "{{" not in sql
    stmts = split_statements(sql)
    guard(stmts)
    names = [n for n, _ in stmts if n]
    for needed in ("ih_act", "iw_act", "ih_ads", "ih_blocked", "iw_blocked", "ih_play", "iw_arm_act", "iw_arm_ads", "iw_arm_blocked", "iw_arm_play"):
        assert needed in names
    assert f"hr < {END_HR}" in sql and "% 24 < 6" in sql and "game_id = 4" in sql
    assert sql.count("create temp table") == 5
