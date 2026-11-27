"""Turn the pulled cuts into per-platform daily metric series, plus the handful of totals the gates
and the report need. Every metric is per UTC day and per platform; 'all' sums the platforms."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.monitor.config import GameCfg
from app.monitor.frames import by_day, group_sum, num, safe_div, where

AD_TYPES = ("interstitial", "rewarded", "banner")
SHORT = {"interstitial": "it", "rewarded": "rv", "banner": "banner"}


@dataclass
class Series:
    """metric -> platform -> {day: value}. Platforms are ints (1, 2) or 'all'."""
    data: dict[str, dict[object, dict[int, float]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(dict)))
    days: list[int] = field(default_factory=list)
    platforms: list[int] = field(default_factory=list)
    # Days a platform was running broken (a config-class block reason far above its usual level):
    # they are not a normal day, so baselines skip them (detect.py, Ctx.base_days). {platform: {day: reason}}
    dead: dict = field(default_factory=dict)

    def dead_days(self, platform) -> frozenset:
        if platform == "all":
            return frozenset(d for v in self.dead.values() for d in v)
        return frozenset(self.dead.get(platform, {}))

    def get(self, metric: str, platform, day: int) -> float | None:
        return self.data.get(metric, {}).get(platform, {}).get(day)

    def series(self, metric: str, platform) -> dict[int, float]:
        return self.data.get(metric, {}).get(platform, {})

    def set(self, metric: str, platform, day: int, value: float | None) -> None:
        if value is None:
            return
        self.data[metric][platform][day] = float(value)


def build(cuts: dict[str, list[dict]], cfg: GameCfg) -> Series:
    s = Series()
    dau_cut = cuts.get("dau_cut", [])
    ad_cut = cuts.get("ad_cut", [])
    banner_cut = cuts.get("banner_cut", [])
    funnel_cut = cuts.get("funnel_cut", [])
    blocked_cut = cuts.get("blocked_cut", [])
    play_cut = cuts.get("play_cut", [])
    rolling_cut = cuts.get("rolling_cut", [])

    days = sorted({int(r["day"]) for r in dau_cut})
    plats = sorted({int(r["platform"]) for r in dau_cut})
    s.days, s.platforms = days, plats
    config_reasons = set(cfg.config_block_reasons)
    s.dead = dead_days(cuts, cfg, plats, days)

    dau = group_sum(dau_cut, ("platform", "day"), ("dau",))
    inst = group_sum(where(dau_cut, lambda r: r.get("age") == "D0"), ("platform", "day"), ("dau",))
    ads = group_sum(ad_cut, ("platform", "day", "ad_type"), ("imps", "rev"))
    ban = group_sum(banner_cut, ("platform", "day"), ("imps", "rev"))
    fun = group_sum(funnel_cut, ("platform", "day", "ad_type", "step"), ("n",))
    blk = group_sum(where(blocked_cut, lambda r: r.get("reason") in config_reasons), ("platform", "day"), ("n",))
    play = group_sum(play_cut, ("platform", "day"), ("starts", "wins"))
    roll = group_sum(rolling_cut, ("platform", "day"), ("dau", "d1_returned"))

    for p in plats + ["all"]:
        for d in days:
            pl = plats if p == "all" else [p]

            def g(table, *key, col):
                return sum(num(table.get((q, d) + key, {}).get(col)) for q in pl)

            dau_v = g(dau, col="dau")
            if dau_v <= 0:
                continue
            s.set("dau", p, d, dau_v)
            s.set("installs", p, d, g(inst, col="dau"))
            rev_total = 0.0
            for t in AD_TYPES:
                if t == "banner":
                    imps, rev = g(ban, col="imps"), g(ban, col="rev")
                else:
                    imps, rev = g(ads, t, col="imps"), g(ads, t, col="rev")
                sh = SHORT[t]
                s.set(f"{sh}_imps", p, d, imps)
                s.set(f"{sh}_rev", p, d, rev)
                s.set(f"{sh}_imps_per_dau", p, d, imps / dau_v)
                s.set(f"{sh}_rev_per_dau", p, d, rev / dau_v)
                s.set(f"{sh}_ecpm", p, d, safe_div(rev * 1000.0, imps))
                rev_total += rev
                if t != "banner":
                    req = g(fun, t, "request", col="n")
                    load = g(fun, t, "load", col="n")
                    load_fail = g(fun, t, "load_fail", col="n")
                    show = g(fun, t, "show_ad", col="n")
                    s.set(f"{sh}_requests_per_dau", p, d, req / dau_v)
                    # No fill ratio: Android 4.0.x logs retry requests (3x) and ~200k/day empty-reason
                    # load_fail rows from a few looping offline clients, so load/request and
                    # load/(load+fail) both swing with logging, not with the networks. Loads per DAU
                    # against requests per DAU is what the fill rule reads instead.
                    s.set(f"{sh}_loads_per_dau", p, d, load / dau_v)
                    s.set(f"{sh}_load_fails_per_dau", p, d, load_fail / dau_v)
                    s.set(f"{sh}_show_per_load", p, d, safe_div(show, load))
                    s.set(f"{sh}_billed_per_show", p, d, safe_div(imps, show))
            s.set("ad_revenue", p, d, rev_total)
            s.set("arpdau", p, d, rev_total / dau_v)
            s.set("blocked_config", p, d, g(blk, col="n"))
            s.set("blocked_config_per_dau", p, d, g(blk, col="n") / dau_v)
            s.set("wins_per_dau", p, d, g(play, col="wins") / dau_v)
            s.set("starts_per_dau", p, d, g(play, col="starts") / dau_v)
            rd = g(roll, col="dau")
            if rd > 0:
                s.set("rolling_d1", p, d, g(roll, col="d1_returned") / rd)

    # UA: Adjust spend / installs / CPI per OS (cost is written the day after, so a day with cost 0
    # and installs > 0 is "not filled yet" and left missing), and attributed paid installs.
    adj = adjust_by_day(cuts)
    attrib = group_sum(where(cuts.get("attrib_cut", []), lambda r: num(r.get("paid")) == 1 and int(r.get("platform") or 0) in plats),
                       ("platform", "day"), ("installs",))
    for p in plats + ["all"]:
        pl = plats if p == "all" else [p]
        for d in days:
            cost = sum(adj.get(OS_BY_PLATFORM.get(q, ""), {}).get(d, {}).get("cost", 0.0) for q in pl)
            inst = sum(adj.get(OS_BY_PLATFORM.get(q, ""), {}).get(d, {}).get("installs", 0.0) for q in pl)
            if inst > 0:
                s.set("ua_installs", p, d, inst)
            if cost > 0:
                s.set("ua_spend", p, d, cost)
                if inst > 0:
                    s.set("ua_cpi", p, d, cost / inst)
            paid = sum(num(attrib.get((q, d), {}).get("installs")) for q in pl)
            if (q_any := any((q, d) in attrib for q in pl)) or paid:
                s.set("paid_installs", p, d, paid)
    return s


def dead_days(cuts: dict[str, list[dict]], cfg: GameCfg, plats: list[int], days: list[int]) -> dict:
    """{platform: {day: reason}} for days a config-class block reason ran at >= the hard-break level
    per DAU AND >= 3x its own median over the window (so a chronic reason like ManagerNotFound, at
    2-3/DAU every day, never qualifies). These are the days the game refused its own ads; anything
    measured on them (ad volume, revenue, and the extra play of players who saw no ads) is not normal."""
    from statistics import median as _median
    config_reasons = set(cfg.config_block_reasons)
    dau = group_sum(cuts.get("dau_cut", []), ("platform", "day"), ("dau",))
    blk = group_sum(where(cuts.get("blocked_cut", []), lambda r: r.get("reason") in config_reasons),
                    ("platform", "day", "reason"), ("n",))
    thr = cfg.severity.hard_break_blocks_per_dau
    out: dict = {}
    for p in plats:
        reasons = {k[2] for k in blk if k[0] == p}
        for reason in reasons:
            rate = {}
            for d in days:
                dd = num(dau.get((p, d), {}).get("dau"))
                if dd > 0:
                    rate[d] = num(blk.get((p, d, reason), {}).get("n")) / dd
            if not rate:
                continue
            med = _median(rate.values())
            for d, r in rate.items():
                if r >= thr and r >= 3 * max(med, 0.05):
                    out.setdefault(p, {})[d] = reason
    return out


def adjust_by_day(cuts: dict[str, list[dict]]) -> dict[str, dict[int, dict[str, float]]]:
    """{'android': {day: {'installs': x, 'cost': y}}, 'ios': ..., 'all': ...} keyed by epoch day."""
    from datetime import date as _date
    from app.monitor.pull import day_num
    out: dict[str, dict[int, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: {"installs": 0.0, "cost": 0.0}))
    for r in cuts.get("adjust_cut", []):
        d = r.get("day")
        if d is None:
            continue
        if isinstance(d, str):
            d = day_num(_date.fromisoformat(d[:10]))
        elif isinstance(d, _date):
            d = day_num(d)
        else:
            d = int(d)
        os_name = str(r.get("os_name") or "").lower()
        for k in ("installs", "cost"):
            out[os_name][d][k] += num(r.get(k))
            out["all"][d][k] += num(r.get(k))
    return out


OS_BY_PLATFORM = {1: "android", 2: "ios"}
