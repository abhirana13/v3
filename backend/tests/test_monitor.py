"""Metric monitor: detector, diagnosis, severity, posting rules and the Slack payload, on synthetic
cuts shaped like the real pull. The headline case replays the iOS Nov 23-24 2026 break: the win-ad
test's variant had no trigger point configured, so every level win was refused
(TriggerPointNotConfigured) and iOS interstitials per DAU fell 6.7 -> 4.0 (about -$800/day)."""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.monitor import config as mcfg
from app.monitor import detect, report, severity
from app.monitor.diagnose import Ctx, diagnose
from app.monitor.pull import day_num, guard, render, render_pregate, split_statements
from app.monitor.run import apply_posting_rules, gates, gates_from_pregate
from app.monitor.series import build
from app.monitor.state import State

END = date(2026, 11, 24)
D_END = day_num(END)
DAYS = list(range(D_END - 34, D_END + 1))
AGES = {"D0": 0.15, "D1-6": 0.20, "D7-29": 0.25, "D30+": 0.40}
GEOS = {"US": 0.6, "T1": 0.2, "RoW": 0.2}


@pytest.fixture(scope="module")
def cfg():
    return mcfg.load()["arrows"]


def make_cuts(break_days: tuple[int, ...] = (D_END - 1, D_END), break_platform: int = 2,
              dau_by_platform: dict[int, int] | None = None, seed: int = 7,
              scale_days: dict[int, dict] | None = None) -> dict[str, list[dict]]:
    """A steady 35-day world where, on break_days, the win-ad variant on one platform gets no
    level-win interstitial. Variant = 50% of that platform's DAU from the first break day."""
    rnd = random.Random(seed)
    dau_by_platform = dau_by_platform or {1: 5700, 2: 7500}
    it_per_dau = {1: 7.9, 2: 6.7}
    rv_per_dau = {1: 1.6, 2: 2.1}
    ban_per_dau = {1: 90.0, 2: 150.0}
    ecpm = {("interstitial", 1): 18.0, ("interstitial", 2): 40.0, ("rewarded", 1): 27.0, ("rewarded", 2): 56.0,
            ("banner", 1): 0.2, ("banner", 2): 0.13}
    wins_per_dau = {1: 8.5, 2: 9.5}
    cuts = {k: [] for k in ("dau_cut", "rolling_cut", "ad_cut", "banner_cut", "funnel_cut", "hour_cut", "blocked_cut",
                            "exp_cut", "play_cut", "iap_cut", "market_cut", "adjust_cut", "arm_dau", "arm_ads",
                            "arm_blocked", "arm_play")}
    scale_days = scale_days or {}
    for p, dau in dau_by_platform.items():
        for d in DAYS:
            noise = 1 + rnd.uniform(-0.03, 0.03)
            sc = scale_days.get(d, {})
            dau_d = int(dau * noise * (1.04 if (d % 7) in (5, 6) else 1.0) * sc.get("dau", 1.0))
            ages = dict(AGES)
            if "d0" in sc:   # fewer install-day players: the rest of the mix scales up
                rest = 1.0 - sc["d0"]
                ages = {k: (sc["d0"] if k == "D0" else v * rest / (1.0 - AGES["D0"])) for k, v in AGES.items()}
            broken = p == break_platform and d in break_days
            # variant share: 0 before the test, 50% from the first break day
            var_share = 0.5 if (break_days and p == break_platform and d >= break_days[0]) else 0.0
            for age, wa in ages.items():
                for geo, wg in GEOS.items():
                    cuts["dau_cut"].append({"platform": p, "day": d, "age": age, "geo": geo, "app_version": "3.8.0",
                                            "dau": int(dau_d * wa * wg)})
            cuts["rolling_cut"].append({"platform": p, "day": d, "dau": dau_d, "d1_returned": int(dau_d * 0.60 * (1 + rnd.uniform(-0.02, 0.02)))})
            # interstitials: level_win placement 65%, out-of-lives 35%; the break kills the win placement for the variant
            it_total = it_per_dau[p] * dau_d * noise
            win_share = 0.75   # Nov 22 iOS variant: level_win 18,869 of ~25,600 interstitials
            for arm, share in (("control", 1 - var_share), ("variant_a", var_share)):
                if share == 0:
                    continue
                win_imps = it_total * share * win_share * (0.0 if (broken and arm == "variant_a") else 1.0)
                ool_imps = it_total * share * (1 - win_share)
                for plc, imps in (("level_win_next_button", win_imps), ("out_of_lives_restart_button", ool_imps)):
                    for age, wa in ages.items():
                        n = int(imps * wa)
                        if n <= 0:
                            continue
                        cuts["ad_cut"].append({"platform": p, "day": d, "ad_type": "interstitial", "age": age, "geo": "US",
                                               "app_version": "3.8.0", "placement": plc, "network": "AppLovin", "band": "L80-499",
                                               "imps": n, "ad_users": max(1, n // 6), "rev": n * ecpm[("interstitial", p)] / 1000})
                    if var_share:
                        cuts["arm_ads"].append({"platform": p, "day": d, "exp_key": "interstitial_placement", "arm": arm,
                                                "ad_type": "interstitial", "placement": plc, "imps": int(imps),
                                                "rev": imps * ecpm[("interstitial", p)] / 1000})
                if var_share:
                    cuts["arm_dau"].append({"platform": p, "day": d, "exp_key": "interstitial_placement", "arm": arm, "dau": int(dau_d * share)})
                    cuts["arm_play"].append({"platform": p, "day": d, "exp_key": "interstitial_placement", "arm": arm,
                                             "wins": int(wins_per_dau[p] * dau_d * share)})
                    if broken and arm == "variant_a":
                        n_blk = int(wins_per_dau[p] * dau_d * share * 1.02)
                        cuts["arm_blocked"].append({"platform": p, "day": d, "exp_key": "interstitial_placement", "arm": arm,
                                                    "ad_type": "interstitial", "reason": "TriggerPointNotConfigured", "n": n_blk,
                                                    "players": int(dau_d * share * 0.8)})
                        cuts["blocked_cut"].append({"platform": p, "day": d, "ad_type": "interstitial", "reason": "TriggerPointNotConfigured",
                                                    "n": n_blk, "players": int(dau_d * share * 0.8)})
            cuts["blocked_cut"].append({"platform": p, "day": d, "ad_type": "interstitial", "reason": "MinLevel", "n": int(dau_d * 2.5), "players": int(dau_d * 0.3)})
            cuts["blocked_cut"].append({"platform": p, "day": d, "ad_type": "interstitial", "reason": "DefaultTimeCooldown", "n": int(dau_d * 4), "players": int(dau_d * 0.5)})
            # rewarded
            rv = rv_per_dau[p] * dau_d * noise
            for age, wa in ages.items():
                cuts["ad_cut"].append({"platform": p, "day": d, "ad_type": "rewarded", "age": age, "geo": "US", "app_version": "3.8.0",
                                       "placement": "revive", "network": "AppLovin", "band": "L80-499", "imps": int(rv * wa),
                                       "ad_users": max(1, int(rv * wa) // 3), "rev": rv * wa * ecpm[("rewarded", p)] / 1000})
            ban = ban_per_dau[p] * dau_d * noise
            cuts["banner_cut"].append({"platform": p, "day": d, "network": "TapMind", "app_version": "3.8.0", "imps": int(ban),
                                       "ad_users": int(dau_d * 0.7), "rev": ban * ecpm[("banner", p)] / 1000})
            # funnel: requests follow shows (a unit reloads after a show), fill 97%
            it_shown = sum(r["imps"] for r in cuts["ad_cut"] if r["platform"] == p and r["day"] == d and r["ad_type"] == "interstitial")
            for ad_type, shown in (("interstitial", it_shown), ("rewarded", int(rv))):
                req = int(shown * 2.3)
                cuts["funnel_cut"] += [
                    {"platform": p, "day": d, "ad_type": ad_type, "step": "request", "o1": "initial", "n": req, "players": dau_d // 2},
                    {"platform": p, "day": d, "ad_type": ad_type, "step": "load", "o1": "AppLovin", "n": int(req * 0.97), "players": dau_d // 2},
                    {"platform": p, "day": d, "ad_type": ad_type, "step": "load_fail", "o1": "1000", "n": int(req * 0.03), "players": dau_d // 20},
                    {"platform": p, "day": d, "ad_type": ad_type, "step": "show_ad", "o1": "AppLovin", "n": int(shown / 0.99), "players": dau_d // 2},
                    {"platform": p, "day": d, "ad_type": ad_type, "step": "revenue", "o1": "AppLovin", "n": shown, "players": dau_d // 2},
                ]
            if d >= D_END - 7:
                for hr in range(24):
                    cuts["hour_cut"].append({"platform": p, "day": d, "hr": hr, "ad_type": "interstitial", "imps": max(1, it_shown // 24), "players": dau_d // 30})
            for age, wa in ages.items():
                w = int(wins_per_dau[p] * dau_d * wa * noise)
                cuts["play_cut"].append({"platform": p, "day": d, "age": age, "app_version": "3.8.0", "starters": int(dau_d * wa * 0.9),
                                         "starts": int(w * 1.0), "winners": int(dau_d * wa * 0.85), "wins": w})
            cuts["iap_cut"].append({"platform": p, "day": d, "purchases": 2, "buyers": 2})
            for g in (5, 6):
                cuts["market_cut"].append({"game_id": g, "platform": p, "day": d, "ad_type": "interstitial", "geo": "US", "imps": 20000,
                                           "rev": 20000 * ecpm[("interstitial", p)] * 1.1 / 1000})
            if var_share:
                cuts["exp_cut"].append({"platform": p, "day": d, "kind": "experiment", "exp_key": "interstitial_placement", "arm": "control", "players": int(dau_d * 0.5)})
                cuts["exp_cut"].append({"platform": p, "day": d, "kind": "experiment", "exp_key": "interstitial_placement", "arm": "variant_a", "players": int(dau_d * 0.5)})
            cuts["exp_cut"].append({"platform": p, "day": d, "kind": "experiment", "exp_key": "league", "arm": "control", "players": int(dau_d * 0.9)})
        for d in DAYS:
            os_name = {1: "android", 2: "ios"}[p]
            ua = scale_days.get(d, {}).get("ua", 1.0)
            cuts["adjust_cut"].append({"day": (date(1970, 1, 1) + timedelta(days=d)).isoformat(), "os_name": os_name,
                                       "installs": int(900 * ua), "cost": 0.0 if d == D_END else (1500.0 if p == 1 else 4000.0) * ua})
            cuts.setdefault("attrib_cut", []).append({"platform": p, "day": d, "paid": 1, "network": "AppLovin", "installs": int(700 * ua)})
            cuts["attrib_cut"].append({"platform": p, "day": d, "paid": 0, "network": "Organic", "installs": 150})
    return cuts


# --- detector -------------------------------------------------------------------------------------

def test_detector_flags_a_real_drop_and_ignores_noise(cfg):
    rnd = random.Random(1)
    series = {d: 6.7 * (1 + rnd.uniform(-0.03, 0.03)) for d in DAYS}
    spec = cfg.metrics["it_imps_per_dau"]
    quiet = detect.read_metric(series, spec, 2, D_END, cfg)
    assert quiet is not None and not quiet.flagged
    series[D_END] = 4.0
    r = detect.read_metric(series, spec, 2, D_END, cfg)
    assert r.flagged and r.z < -3 and r.rel_base < -0.35 and r.persist_days == 1
    assert r.rel_dod is not None and r.rel_wow is not None
    # a rise is never "wrong" for a down-is-bad metric
    series[D_END] = 9.5
    assert not detect.read_metric(series, spec, 2, D_END, cfg).flagged


def test_detector_persistence_counts_consecutive_days(cfg):
    series = {d: 10.0 for d in DAYS}
    for d in (D_END - 2, D_END - 1, D_END):
        series[d] = 7.0
    r = detect.read_metric(series, cfg.metrics["wins_per_dau"], 1, D_END, cfg)
    assert r.flagged and r.persist_days == 3


def test_up_is_bad_needs_min_abs(cfg):
    spec = cfg.metrics["blocked_config_per_dau"]
    series = {d: 0.001 for d in DAYS}
    series[D_END] = 0.05  # 50x but tiny in absolute terms
    assert not detect.read_metric(series, spec, 2, D_END, cfg).flagged
    series[D_END] = 5.5
    assert detect.read_metric(series, spec, 2, D_END, cfg).flagged


# --- the Nov 23-24 replay -------------------------------------------------------------------------

@pytest.fixture(scope="module")
def world(cfg):
    cuts = make_cuts()
    s = build(cuts, cfg)
    readings = detect.read_all(s, cfg, D_END)
    ctx = Ctx(cuts, s, cfg, D_END)
    scored = [severity.score(ctx, r, cfg.metrics[r.metric], diagnose(ctx, r, cfg.metrics[r.metric])) for r in readings if r.flagged]
    return cuts, s, readings, ctx, scored


def test_series_reproduce_the_platform_numbers(world):
    cuts, s, *_ = world
    assert 6.4 <= s.get("it_imps_per_dau", 2, D_END - 3) <= 7.0          # before the break
    assert 3.6 <= s.get("it_imps_per_dau", 2, D_END) <= 4.6              # variant lost its 65% win placement
    assert 7.5 <= s.get("it_imps_per_dau", 1, D_END) <= 8.3              # Android untouched
    assert s.get("blocked_config_per_dau", 2, D_END) > 4


def test_ios_interstitials_flagged_android_not(world, cfg):
    *_, scored = world
    keys = {(sc.reading.platform, sc.spec.key) for sc in scored}
    assert (2, "it_imps_per_dau") in keys
    assert (2, "ad_revenue") in keys
    assert (2, "blocked_config_per_dau") in keys
    assert not any(p == 1 for p, _ in keys), keys
    assert (2, "rv_imps_per_dau") not in keys
    assert (2, "wins_per_dau") not in keys


def test_cause_is_the_unconfigured_trigger_in_the_variant(world):
    *_, scored = world
    it = next(sc for sc in scored if sc.spec.key == "it_imps_per_dau" and sc.reading.platform == 2)
    d = it.diagnosis
    assert d.cause == "config_break"
    assert d.confidence == "confirmed"
    assert d.tags.get("exp_key") == "interstitial_placement" and d.tags.get("arm") == "variant_a"
    assert d.hard_break
    joined = " ".join(d.evidence)
    assert "TriggerPointNotConfigured" in joined
    assert "level_win_next_button" in joined
    assert "blocks per level win" in joined
    assert d.fix and "remote config" in d.fix
    # the checks that cleared are named with their numbers
    assert any(x.startswith("play (") for x in d.ruled_out)


def test_revenue_finding_adopts_the_volume_lever(world):
    *_, scored = world
    rev = next(sc for sc in scored if sc.spec.key == "ad_revenue" and sc.reading.platform == 2)
    assert rev.diagnosis.cause == "config_break"
    assert rev.diagnosis.tags.get("lever") == "volume" and rev.diagnosis.tags.get("ad_type") == "interstitial"
    assert any("fewer ads per player (mostly IT)" in e for e in rev.diagnosis.evidence)


def test_severity_is_sev1_with_a_dollar_figure(world):
    *_, scored = world
    it = next(sc for sc in scored if sc.spec.key == "it_imps_per_dau" and sc.reading.platform == 2)
    assert it.severity == 1
    # 7,500 DAU x ~2.2-2.7 lost imps x $40 CPM ~ -$650-800/day (the baseline median still holds one broken day)
    assert -1100 < it.impact_usd < -500
    rev = next(sc for sc in scored if sc.spec.key == "ad_revenue" and sc.reading.platform == 2)
    assert rev.severity == 1 and rev.impact_usd < -400


def test_message_has_header_moves_why_and_fix(world, cfg):
    *_, scored = world
    payload = report.build_message(cfg, END, scored, [], {"ad_revenue_7d": 5000.0, "ua_spend_7d": 5500.0, "dau": {1: 5700, 2: 7500}, "frontier": "23:59 UTC"})
    text = report.to_text(payload)
    assert payload["blocks"][0]["type"] == "header"
    assert "SEV1" in text and "act now" in text
    assert "<!here>" in text
    assert "What moved" in text and "WoW" in text and "DoD" in text
    assert "TriggerPointNotConfigured" in text
    assert "Fix:" in text and "Ruled out" in text
    assert "ads $5,000/day" in text and "UA $5,500/day" in text
    for b in payload["blocks"]:
        if b["type"] == "section":
            assert len(b["text"]["text"]) <= 3000


# --- posting rules -------------------------------------------------------------------------------

def test_posting_rules_new_sev1_posts_then_reposts_then_recovers(world, cfg):
    cuts, s, readings, ctx, scored = world
    state = State()
    post, rec = apply_posting_rules(cfg, END, scored, readings, state)
    assert post and not rec
    sig = next(k for k in state.incidents if ":it_imps_per_dau:" in k)
    assert state.incidents[sig].posted_count == 1
    # next day, still broken: SEV1 reposts
    nxt = END + timedelta(days=1)
    for sc in scored:
        sc.reading.day += 1
    post2, _ = apply_posting_rules(cfg, nxt, scored, readings, state)
    assert post2 and state.incidents[sig].posted_count == 2
    # day after: nothing flagged, metric back inside 1.5 sigma => a recovery line for the SEV1 metric
    fixed = make_cuts(break_days=(D_END - 2, D_END - 1))      # the last day is healthy again
    s2 = build(fixed, cfg)
    readings2 = detect.read_all(s2, cfg, D_END)
    flagged2 = [r for r in readings2 if r.flagged]
    post3, rec3 = apply_posting_rules(cfg, nxt + timedelta(days=1), [], readings2, state)
    assert post3 == []
    assert any("IT imps/DAU" in x and "back at" in x for x in rec3), (rec3, [r.metric for r in flagged2])


def test_posting_rules_sev3_waits_for_persistence(cfg):
    state = State()
    cuts = make_cuts(break_days=(D_END,), break_platform=2, dau_by_platform={1: 5700, 2: 7500})
    s = build(cuts, cfg)
    readings = detect.read_all(s, cfg, D_END)
    ctx = Ctx(cuts, s, cfg, D_END)
    r = next(r for r in readings if r.metric == "it_imps_per_dau" and r.platform == 2)
    sc = severity.score(ctx, r, cfg.metrics[r.metric], diagnose(ctx, r, cfg.metrics[r.metric]))
    sc.severity = 3
    sc.reading.persist_days = 1
    post, _ = apply_posting_rules(cfg, END, [sc], readings, state)
    assert post == []
    sc.reading.persist_days = 3
    post, _ = apply_posting_rules(cfg, END + timedelta(days=1), [sc], readings, state)
    assert post == [sc]


# --- gates ---------------------------------------------------------------------------------------

def test_gates_pass_for_arrows_and_fail_for_a_small_game(cfg):
    cuts = make_cuts()
    s = build(cuts, cfg)
    g = gates(cfg, s, cuts, D_END)
    assert g["revenue_ok"] and g["ua_ok"] and g["ad_revenue_7d"] > 3000
    small = make_cuts(dau_by_platform={1: 300, 2: 200})
    for r in small["adjust_cut"]:
        r["cost"] = 20.0
    g2 = gates(cfg, build(small, cfg), small, D_END)
    assert not g2["revenue_ok"] and not g2["ua_ok"]


# --- SQL plumbing ----------------------------------------------------------------------------------

def test_sql_renders_and_passes_the_read_only_guard(cfg):
    sql = render(cfg, END)
    assert "{{" not in sql
    stmts = split_statements(sql)
    guard(stmts)
    names = [n for n, _ in stmts if n]
    for needed in ("dau_cut", "ad_cut", "banner_cut", "funnel_cut", "blocked_cut", "exp_cut", "play_cut", "arm_blocked", "adjust_cut", "market_cut", "hour_cut"):
        assert needed in names
    assert "game_id = 4" in sql and "'Arrow Maze'" in sql
    assert "game_id in (" in sql and "4" not in sql.split("game_id in (")[1].split(")")[0].split(", ")


def test_guard_refuses_writes():
    with pytest.raises(PermissionError):
        guard([(None, "-- out: none\ncreate table x as select 1")])
    with pytest.raises(PermissionError):
        guard([(None, "delete from events")])
    guard([(None, "drop table if exists t_act")])


# --- UA spend as a cause ----------------------------------------------------------------------------

def test_ua_cut_explains_a_revenue_and_dau_drop(cfg):
    """Spend halved for four days: fewer install-day players, DAU -20%, revenue follows. The revenue
    and DAU findings must name the UA cut, not the game, and the spend tile must read the cut."""
    sc = {d: {"dau": 0.75, "d0": 0.04, "ua": 0.5} for d in range(D_END - 1, D_END + 1)}
    cuts = make_cuts(break_days=(), scale_days=sc)
    s = build(cuts, cfg)
    readings = detect.read_all(s, cfg, D_END)
    ctx = Ctx(cuts, s, cfg, D_END)
    scored = {(r.platform, r.metric): severity.score(ctx, r, cfg.metrics[r.metric], diagnose(ctx, r, cfg.metrics[r.metric]))
              for r in readings if r.flagged}
    assert (2, "dau") in scored and (2, "ad_revenue") in scored and (2, "ua_spend") in scored
    assert scored[(2, "dau")].diagnosis.cause == "ua_cut"
    rev = scored[(2, "ad_revenue")]
    assert rev.diagnosis.cause == "ua_cut"
    assert "UA" in rev.diagnosis.summary and "not a game problem" in rev.diagnosis.summary
    assert any(e.startswith("UA: spend") and "Adjust" in e for e in rev.diagnosis.evidence)
    assert scored[(2, "ua_spend")].severity == 3           # a spend cut on its own is context, never an alarm
    assert scored[(2, "ua_spend")].diagnosis.cause == "ua_cut"
    assert (2, "it_imps_per_dau") not in scored            # per-DAU ad volume held: nothing wrong with the ads


def test_ua_steady_is_listed_as_ruled_out(world):
    *_, scored = world
    rev = next(sc for sc in scored if sc.spec.key == "ad_revenue" and sc.reading.platform == 2)
    assert any(x.startswith("UA steady") for x in rev.diagnosis.ruled_out)


# --- many games ------------------------------------------------------------------------------------

def test_every_configured_game_renders_with_its_own_ad_type_map():
    games = mcfg.load()
    assert {"arrows", "nova", "sortery"} <= set(games)
    nova = games["nova"]
    expr = nova.ad_type_case_sql()
    assert "when 'inter' then 'interstitial'" in expr and "when 'leader' then 'banner'" in expr
    assert nova.ua_source == "adjust" and games["sortery"].ua_source == "attribution"
    for g in games.values():
        sql = render(g, END)
        assert f"game_id = {g.game_id}" in sql and "{{" not in sql
        guard(split_statements(sql))
        assert g.game_id not in g.market_control_games and g.market_control_games
    pg = render_pregate(list(games.values()), END)
    guard(split_statements(pg))
    assert "'Nova Solitaire'" in pg and "'Arrow Maze'" in pg


def test_pregate_gates_each_game_on_its_own_ua_source(cfg):
    games = mcfg.load()
    pg = {"pg_revenue": [], "pg_adjust": [], "pg_attrib": []}
    for d in range(D_END - 6, D_END + 1):
        pg["pg_revenue"] += [{"game_id": 4, "platform": 2, "day": d, "ad_type": "interstitial", "imps": 1, "rev": 3000.0},
                             {"game_id": 6, "platform": 1, "day": d, "ad_type": "rewarded", "imps": 1, "rev": 700.0},
                             {"game_id": 1, "platform": 1, "day": d, "ad_type": "interstitial", "imps": 1, "rev": 300.0}]
        pg["pg_adjust"] += [{"app": "Arrow Maze", "os_name": "ios", "day": (date(1970, 1, 1) + timedelta(days=d)).isoformat(), "installs": 900, "cost": 0.0 if d == D_END else 4000.0},
                            {"app": "Nova Solitaire", "os_name": "android", "day": (date(1970, 1, 1) + timedelta(days=d)).isoformat(), "installs": 500, "cost": 1500.0}]
        pg["pg_attrib"] += [{"game_id": 6, "day": d, "paid": 1, "installs": 320}, {"game_id": 6, "day": d, "paid": 0, "installs": 100}]
    a = gates_from_pregate(games["arrows"], pg, D_END)
    assert a["revenue_ok"] and a["ua_ok"] and abs(a["ua_spend_7d"] - 4000.0) < 1
    n = gates_from_pregate(games["nova"], pg, D_END)
    assert not n["revenue_ok"] and n["ua_ok"]
    so = gates_from_pregate(games["sortery"], pg, D_END)
    assert so["revenue_ok"] and so["ua_ok"] and so["ua_source"] == "attribution" and abs(so["paid_installs_7d"] - 320) < 1
    z = gates_from_pregate(games["zen"], pg, D_END)
    assert not z["revenue_ok"] and not z["ua_ok"]


# --- resilience -----------------------------------------------------------------------------------

def test_transient_connection_failures_are_retried(monkeypatch):
    from app.monitor import pull as _pull
    monkeypatch.setattr(_pull.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class Tunnel(Exception):
        pass
    Tunnel.__name__ = "BaseSSHTunnelForwarderError"

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Tunnel("Could not establish session to SSH gateway")
        return "ok"
    assert _pull.with_retries(flaky, log=lambda m: None) == "ok" and calls["n"] == 3

    def sql_error():
        raise ValueError("column does not exist")
    with pytest.raises(ValueError):
        _pull.with_retries(sql_error, log=lambda m: None)

    def never():
        raise Tunnel("down")
    with pytest.raises(Tunnel):
        _pull.with_retries(never, log=lambda m: None, delays=(0, 0))


def test_failed_run_posts_a_notice(monkeypatch, cfg):
    from app.monitor import run as _run
    from app.monitor.run import RunResult, notify_failures
    sent = {}

    def fake_post(url, payload):
        sent["payload"] = payload
        return "SLACK_POSTED=ok"
    monkeypatch.setattr(_run.slack, "post", fake_post)
    monkeypatch.setenv("SLACK_MONITOR_WEBHOOK_URL", "https://hooks.slack.com/services/x")
    games = mcfg.load()
    ok = RunResult("arrows", END, {}, False)
    bad = RunResult("nova", END, {}, False)
    bad.error = "BaseSSHTunnelForwarderError: Could not establish session to SSH gateway"
    assert notify_failures(games, END, [ok], dry_run=False, log=lambda m: None) == ""
    assert notify_failures(games, END, [ok, bad], dry_run=False, log=lambda m: None) == "SLACK_POSTED=ok"
    assert "Nova Solitaire" in sent["payload"]["text"] and "SSH gateway" in sent["payload"]["text"]


def test_cache_pruning_keeps_recent_days(tmp_path):
    from app.monitor.run import prune_cache
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    old = tmp_path / (today - timedelta(days=20)).isoformat()
    recent = tmp_path / (today - timedelta(days=3)).isoformat()
    intra_old = tmp_path / "intraday" / ((today - timedelta(days=20)).isoformat() + "T12")
    for d in (old, recent, intra_old):
        d.mkdir(parents=True)
        (d / "dau_cut.csv").write_text("x")
    (tmp_path / "notes").mkdir()
    assert prune_cache(tmp_path, keep_days=14, log=lambda m: None) == 2
    assert not old.exists() and not intra_old.exists() and recent.exists() and (tmp_path / "notes").exists()


# --- broken days stay out of baselines ------------------------------------------------------------

def test_dead_days_come_from_config_block_spikes_not_chronic_reasons(cfg):
    broken = (D_END - 4, D_END - 3, D_END - 2, D_END - 1)
    cuts = make_cuts(break_days=broken)
    # a chronic config-class reason (ManagerNotFound sits at ~2-3/DAU on both platforms) never counts
    for d in DAYS:
        for p, dau in ((1, 5700), (2, 7500)):
            cuts["blocked_cut"].append({"platform": p, "day": d, "ad_type": "interstitial",
                                        "reason": "ManagerNotFound", "n": int(dau * 2.5), "players": 300})
    s = build(cuts, cfg)
    assert s.dead_days(2) == frozenset(broken)
    assert s.dead_days(1) == frozenset()
    assert D_END not in s.dead_days(2)            # the fix day itself is healthy again
    assert s.dead[2][D_END - 2] == "TriggerPointNotConfigured"


def test_baseline_steps_over_dead_days():
    """Four broken days when players saw no ads and won 30% more levels, then a fix and a modest real
    dip (-8%). Against healthy days the dip is under the 10% bar; against a baseline full of broken
    days it would read as a -29% collapse — the false alarm of Nov 26."""
    cfgw = mcfg.load()["arrows"]
    spec = cfgw.metrics["wins_per_dau"]
    rnd = random.Random(5)
    series = {d: 8.4 * (1 + rnd.uniform(-0.03, 0.03)) for d in DAYS}
    broken = {D_END - 4, D_END - 3, D_END - 2, D_END - 1}
    for d in broken:
        series[d] = 8.4 * 1.30
    series[D_END] = 8.4 * 0.92
    healthy = detect.read_metric(series, spec, 2, D_END, cfgw, frozenset(broken))
    assert healthy.skipped == tuple(sorted(broken))
    assert abs(healthy.baseline - 8.4) < 0.3
    assert not healthy.flagged
    naive = detect.read_metric(series, spec, 2, D_END, cfgw)
    assert naive.flagged and naive.rel_base < -0.25


def test_diagnosis_windows_skip_dead_days(cfg):
    cuts = make_cuts(break_days=(D_END - 3, D_END - 2, D_END - 1))
    s = build(cuts, cfg)
    ctx = Ctx(cuts, s, cfg, D_END)
    ios = ctx.base_days(D_END, 2)
    assert len(ios) == 7 and not set(ios) & {D_END - 3, D_END - 2, D_END - 1} and min(ios) == D_END - 10
    assert ctx.base_days(D_END, 1) == list(range(D_END - 7, D_END))   # Android was never broken


def test_footer_names_the_skipped_days(world, cfg):
    *_, scored = world
    payload = report.build_message(cfg, END, scored, [], {"baseline_skips": {1: [], 2: [D_END - 2, D_END - 1]}})
    assert "baselines skip iOS Nov 22–23" in report.to_text(payload)


# --- an experiment is blamed only when its arms differ ---------------------------------------------

def test_experiment_change_needs_a_measured_arm_gap(cfg):
    from app.monitor.diagnose import _experiment_change
    cuts = make_cuts()                                  # interstitial_placement starts on D_END - 1, arms play alike
    s = build(cuts, cfg)
    d, note = _experiment_change(Ctx(cuts, s, cfg, D_END), 2, D_END, None, "wins_per_dau")
    assert d is None and "arms are in line" in note and "level wins/DAU by arm" in note

    for r in cuts["arm_play"]:                          # now the variant wins 60% more on the read day
        if r["platform"] == 2 and r["day"] == D_END and r["arm"] == "variant_a":
            r["wins"] = int(r["wins"] * 1.6)
    d, _ = _experiment_change(Ctx(cuts, build(cuts, cfg), cfg, D_END), 2, D_END, None, "wins_per_dau")
    assert d.cause == "experiment_change" and d.confidence == "likely" and "arms differ by" in d.headline
    assert any("level wins/DAU by arm" in e for e in d.evidence)

    d, _ = _experiment_change(Ctx(cuts, s, cfg, D_END), 2, D_END, None, "rolling_d1")   # no per-arm cut
    assert d.confidence == "possible" and "cannot be compared" in d.headline and "arms differ" not in d.headline
