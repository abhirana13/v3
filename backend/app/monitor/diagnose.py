"""Why did it move? The Arrows drop playbook, as code.

For every flagged reading the matching rule set runs in order of how often that cause has been the
answer on Arrows (MONITOR.md §4). Each rule either returns a Diagnosis or adds a 'ruled out' line
with the number that cleared it, so the message always says what was checked.

Causes:
  pipeline          the day is incomplete (events stop early) — nothing else is read
  config_break      the game asked for an ad and its own config refused (TriggerPointNotConfigured,
                    AdTypeDisabled, ManagerNotFound ...), attributed to the experiment arm it sits in
  placement         one placement's impressions collapsed without a block reason (client-side)
  engagement        the ad volume fell because play fell
  ad_fill           requests held, fill (load/request) fell — the networks, not the game
  ua_mix            the audience changed (D0 share, geo) and the metric within each band held
  ua_cut            spend / installs were cut (Adjust)
  experiment_change an experiment started or ramped and the arms differ on this metric
  build_rollout     a new build reached a sizeable share and behaves differently
  network_price     one serving network's eCPM or share fell, others held
  market_price      eCPM fell at a fixed mix and the control games fell too
  retention         returning players fell while installs held
  unexplained       every check cleared"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from app.monitor import fmt
from app.monitor.config import GameCfg, MetricSpec
from app.monitor.detect import Reading
from app.monitor.frames import group_sum, num, safe_div, where
from app.monitor.series import OS_BY_PLATFORM, SHORT, Series, adjust_by_day

T1 = {"GB", "CA", "AU", "NZ", "DE", "FR", "NL", "SE", "NO", "DK", "CH", "AT", "BE", "IE", "FI", "JP", "KR"}


@dataclass
class Diagnosis:
    cause: str
    confidence: str                 # confirmed | likely | possible | unexplained
    headline: str
    evidence: list[str] = field(default_factory=list)
    ruled_out: list[str] = field(default_factory=list)
    fix: str | None = None
    hard_break: bool = False
    tags: dict = field(default_factory=dict)
    summary: str = ""        # one plain sentence for the message's top line; headline when empty


class Ctx:
    """Cut-level lookups for one game run. `day` is the read day; baseline = pooled over the
    baseline_days before it."""

    def __init__(self, cuts: dict[str, list[dict]], series: Series, cfg: GameCfg, day: int):
        self.cuts = cuts
        self.s = series
        self.cfg = cfg
        self.day = day
        self.config_reasons = set(cfg.config_block_reasons)
        self._adjust = adjust_by_day(cuts)

    # --- windows -------------------------------------------------------------------------------
    def base_days(self, day: int | None = None) -> list[int]:
        d = self.day if day is None else day
        return [t for t in range(d - self.cfg.baseline_days, d) if t in self.s.days]

    def pname(self, p) -> str:
        return self.cfg.platform_name(p)

    # --- generic cut aggregations --------------------------------------------------------------
    def _rows(self, table: str, p, days: list[int], extra=None) -> list[dict]:
        ds = set(days)
        rows = [r for r in self.cuts.get(table, []) if int(r["platform"]) == int(p) and int(r["day"]) in ds]
        if extra:
            rows = [r for r in rows if extra(r)]
        return rows

    def dau(self, p, day: int) -> float:
        return self.s.get("dau", p, day) or 0.0

    def dau_base(self, p) -> float:
        bd = self.base_days()
        return sum(self.dau(p, d) for d in bd) / len(bd) if bd else 0.0

    def dau_by(self, p, col: str, days: list[int]) -> dict:
        g = group_sum(self._rows("dau_cut", p, days), (col,), ("dau",))
        return {k[0]: v["dau"] for k, v in g.items()}

    def imps_by(self, p, ad_type: str, col: str, days: list[int], placement_norm: bool = False) -> dict:
        table = "banner_cut" if ad_type == "banner" else "ad_cut"
        rows = self._rows(table, p, days, (lambda r: r.get("ad_type") == ad_type) if ad_type != "banner" else None)
        if placement_norm and col == "placement":
            al = self.cfg.placement_aliases
            rows = [dict(r, placement=al.get(r.get("placement") or "", r.get("placement") or "")) for r in rows]
        g = group_sum(rows, (col,), ("imps", "rev"))
        return {k[0]: v for k, v in g.items()}

    def funnel(self, p, ad_type: str, days: list[int]) -> dict[str, float]:
        g = group_sum(self._rows("funnel_cut", p, days, lambda r: r.get("ad_type") == ad_type), ("step",), ("n",))
        return {k[0]: v["n"] for k, v in g.items()}

    def funnel_by_network(self, p, ad_type: str, step: str, days: list[int]) -> dict[str, float]:
        g = group_sum(self._rows("funnel_cut", p, days, lambda r: r.get("ad_type") == ad_type and r.get("step") == step),
                      ("o1",), ("n",))
        return {k[0]: v["n"] for k, v in g.items()}

    def blocks(self, p, days: list[int], config_only: bool = True) -> dict[str, dict]:
        """{reason: {'n': rows, 'players': distinct players (summed over days)}}."""
        rows = self._rows("blocked_cut", p, days)
        if config_only:
            rows = [r for r in rows if r.get("reason") in self.config_reasons]
        g = group_sum(rows, ("reason",), ("n", "players"))
        return {k[0]: v for k, v in g.items()}

    def blocks_by_arm(self, p, days: list[int], reason: str | None = None) -> dict[tuple, dict]:
        rows = self._rows("arm_blocked", p, days, lambda r: (r.get("reason") == reason) if reason else (r.get("reason") in self.config_reasons))
        g = group_sum(rows, ("exp_key", "arm"), ("n", "players"))
        return dict(g)

    def rising_block_reason(self, p, day: int) -> tuple[str | None, float, float]:
        """The config-class reason with the largest rise per DAU vs the baseline window:
        (reason, per_dau_now, per_dau_base). Chronic reasons (ManagerNotFound sits at ~2-3/DAU on
        both platforms) do not win unless they rose."""
        base = self.base_days(day)
        dau_now, dau_b = self.dau(p, day), self.dau_base(p)
        now = self.blocks(p, [day])
        bse = self.blocks(p, base)
        best = (None, 0.0, 0.0)
        for reason in set(now) | set(bse):
            pn = now.get(reason, {}).get("n", 0.0) / dau_now if dau_now else 0.0
            pb = (bse.get(reason, {}).get("n", 0.0) / max(1, len(base))) / dau_b if dau_b else 0.0
            if pn - pb > best[1] - best[2]:
                best = (reason, pn, pb)
        return best

    def first_block_day(self, p, reason: str | None = None) -> int | None:
        """Earliest day in the window where `reason` (or any config-class reason) runs at >= 0.05
        per DAU and at least 3x its level over the 7 days before, else None."""
        days = list(self.s.days)
        for i, d in enumerate(days):
            dau = self.dau(p, d)
            if not dau:
                continue
            blk = self.blocks(p, [d])
            n = blk.get(reason, {}).get("n", 0.0) if reason else sum(v["n"] for v in blk.values())
            if n / dau < 0.05:
                continue
            prev = days[max(0, i - 7):i]
            if prev:
                pb = self.blocks(p, prev)
                nb = (pb.get(reason, {}).get("n", 0.0) if reason else sum(v["n"] for v in pb.values())) / len(prev)
                if n / dau < 3 * (nb / (self.dau_base(p) or dau)):
                    continue
            return d
        return None

    def healthy_base_days(self, p, day: int | None = None, reason: str | None = None) -> list[int]:
        """Baseline days before the blocks began; the plain baseline when there were none."""
        base = self.base_days(day)
        fb = self.first_block_day(p, reason)
        if fb is None:
            return base
        healthy = [d for d in base if d < fb]
        return healthy or base

    def arm_dau(self, p, key: str, days: list[int]) -> dict[str, float]:
        g = group_sum(self._rows("arm_dau", p, days, lambda r: r.get("exp_key") == key), ("arm",), ("dau",))
        return {k[0]: v["dau"] for k, v in g.items()}

    def arm_imps(self, p, key: str, ad_type: str, days: list[int]) -> dict[str, dict]:
        g = group_sum(self._rows("arm_ads", p, days, lambda r: r.get("exp_key") == key and r.get("ad_type") == ad_type),
                      ("arm",), ("imps", "rev"))
        return {k[0]: v for k, v in g.items()}

    def arm_placement_imps(self, p, key: str, arm: str, ad_type: str, days: list[int]) -> dict[str, float]:
        g = group_sum(self._rows("arm_ads", p, days, lambda r: r.get("exp_key") == key and r.get("arm") == arm
                                 and r.get("ad_type") == ad_type), ("placement",), ("imps",))
        return {k[0]: v["imps"] for k, v in g.items()}

    def arm_wins(self, p, key: str, days: list[int]) -> dict[str, float]:
        g = group_sum(self._rows("arm_play", p, days, lambda r: r.get("exp_key") == key), ("arm",), ("wins",))
        return {k[0]: v["wins"] for k, v in g.items()}

    def exp_players(self, p, days: list[int], kind: str = "experiment") -> dict[tuple, float]:
        g = group_sum(self._rows("exp_cut", p, days, lambda r: r.get("kind") == kind), ("exp_key", "arm"), ("players",))
        return {k: v["players"] / max(1, len(days)) for k, v in g.items()}

    def play(self, p, days: list[int]) -> dict[str, float]:
        g = group_sum(self._rows("play_cut", p, days), (), ("starts", "wins"))
        return g.get((), {"starts": 0.0, "wins": 0.0})

    def play_by(self, p, col: str, days: list[int]) -> dict:
        g = group_sum(self._rows("play_cut", p, days), (col,), ("starts", "wins"))
        return {k[0]: v for k, v in g.items()}

    def market_ecpm(self, p, ad_type: str, days: list[int]) -> float | None:
        rows = [r for r in self.cuts.get("market_cut", []) if int(r["platform"]) == int(p) and int(r["day"]) in set(days)
                and r.get("ad_type") == ad_type]
        g = group_sum(rows, (), ("imps", "rev"))
        v = g.get(())
        return safe_div(v["rev"] * 1000.0, v["imps"]) if v else None

    def adjust(self, p, day: int) -> dict[str, float]:
        os_name = OS_BY_PLATFORM.get(int(p), "all") if p != "all" else "all"
        return self._adjust.get(os_name, {}).get(day, {"installs": 0.0, "cost": 0.0})

    def adjust_cost_days(self, p, days: list[int]) -> list[float]:
        """Cost on days where Adjust has filled it (0 means 'not fetched yet' for the newest day)."""
        return [self.adjust(p, d)["cost"] for d in days if self.adjust(p, d)["cost"] > 0]

    def hours_with_imps(self, p, day: int) -> list[int]:
        rows = [r for r in self.cuts.get("hour_cut", []) if int(r["platform"]) == int(p) and int(r["day"]) == day and num(r.get("imps")) > 0]
        return sorted({int(r["hr"]) for r in rows})

    def hour_imps(self, p, day: int) -> dict[int, float]:
        g = group_sum([r for r in self.cuts.get("hour_cut", []) if int(r["platform"]) == int(p) and int(r["day"]) == day], ("hr",), ("imps",))
        return {int(k[0]): v["imps"] for k, v in g.items()}


# --- helpers ---------------------------------------------------------------------------------------

def _rate_change(cur: dict, base: dict, cur_den: float, base_den: float) -> dict[str, tuple[float, float, float | None]]:
    """{key: (rate_now, rate_base, rel)} with rates per unit of the denominator."""
    out = {}
    for k in set(cur) | set(base):
        rn = num(cur.get(k, 0.0) if not isinstance(cur.get(k), dict) else cur[k]["imps"]) / cur_den if cur_den else 0.0
        rb = num(base.get(k, 0.0) if not isinstance(base.get(k), dict) else base[k]["imps"]) / base_den if base_den else 0.0
        out[k] = (rn, rb, (rn - rb) / rb if rb else None)
    return out


def kitagawa(w_now: dict, w_base: dict, r_now: dict, r_base: dict) -> tuple[float, float, float]:
    """total, mix, within for a per-DAU rate decomposed over bands. Weights are DAU shares."""
    keys = set(w_now) | set(w_base)
    total = sum(w_now.get(k, 0) * r_now.get(k, 0) for k in keys) - sum(w_base.get(k, 0) * r_base.get(k, 0) for k in keys)
    mix = sum((w_now.get(k, 0) - w_base.get(k, 0)) * r_base.get(k, 0) for k in keys)
    within = sum(w_now.get(k, 0) * (r_now.get(k, 0) - r_base.get(k, 0)) for k in keys)
    return total, mix, within


def _shares(d: dict) -> dict:
    tot = sum(d.values()) or 1.0
    return {k: v / tot for k, v in d.items()}


def _band_rates(ctx: Ctx, p, ad_type: str, band_col: str, days: list[int]) -> tuple[dict, dict]:
    """(DAU shares by band, imps per DAU by band) pooled over `days`."""
    dau_b = ctx.dau_by(p, band_col, days)
    imps_b = {k: v["imps"] for k, v in ctx.imps_by(p, ad_type, band_col, days).items()}
    rates = {k: imps_b.get(k, 0.0) / dau_b[k] for k in dau_b if dau_b[k] > 0}
    return _shares(dau_b), rates


def _pretty_arm(arm: str) -> str:
    return arm or "(unassigned)"


# --- rule pieces -----------------------------------------------------------------------------------

def check_pipeline(ctx: Ctx, p, day: int) -> Diagnosis | None:
    """Events for the day stop early => the day is incomplete; say so instead of reading drops."""
    hrs = ctx.hours_with_imps(p, day)
    if not hrs:
        return Diagnosis("pipeline", "confirmed", f"{ctx.pname(p)}: no ad impressions logged on this day at all",
                         ["hour_cut is empty for the day — the warehouse has not received the day"], hard_break=False,
                         fix="Check the event pipeline / ingestion lag before reading any metric for this day.")
    last = max(hrs)
    if last < 22:
        return Diagnosis("pipeline", "confirmed",
                         f"{ctx.pname(p)}: the day's events stop at {last:02d}:xx UTC — the day is incomplete",
                         [f"last hour with impressions: {last:02d}:00 UTC; {24 - last - 1} hours missing"],
                         fix="Re-run once ingestion catches up; today's run read a partial day.")
    # a tail that is far thinner than usual (ingestion lag) — compare the last 3 hours' share
    base = ctx.base_days(day)
    if base:
        tail_now = sum(v for h, v in ctx.hour_imps(p, day).items() if h >= 21)
        tot_now = sum(ctx.hour_imps(p, day).values()) or 1.0
        tails, tots = 0.0, 0.0
        for d in base:
            hi = ctx.hour_imps(p, d)
            tails += sum(v for h, v in hi.items() if h >= 21)
            tots += sum(hi.values())
        if tots and tails and (tail_now / tot_now) < 0.35 * (tails / tots):
            return Diagnosis("pipeline", "likely",
                             f"{ctx.pname(p)}: the last 3 hours of the day carry {fmt.pct(tail_now / tot_now, signed=False)} of impressions vs {fmt.pct(tails / tots, signed=False)} normally — ingestion lag",
                             [], fix="Re-run later; late events are still arriving.")
    return None


def _config_break(ctx: Ctx, p, day: int, ad_type: str | None, spec_label: str) -> tuple[Diagnosis | None, str | None]:
    """Config-class block reasons: the game asked for an ad and its own config refused. Sized per
    DAU and by the share of players hit, dated to the first day it appeared, attributed to the
    experiment arm the blocked players sit in, with the placement that died named."""
    dau_now = ctx.dau(p, day)
    reason, per_dau_now, per_dau_base = ctx.rising_block_reason(p, day)
    if reason is None or per_dau_now < 0.05 or per_dau_now < 3 * max(per_dau_base, 0.01):
        allb = ctx.blocks(p, [day])
        tot = sum(v["n"] for v in allb.values()) / dau_now if dau_now else 0.0
        return None, f"no new config-class ad blocks (all reasons {fmt.num(tot)}/DAU, unchanged)"
    blk = ctx.blocks(p, [day]).get(reason, {"n": 0.0, "players": 0.0})
    n_now, players_now = blk["n"], blk["players"]
    players_share = players_now / dau_now if dau_now else 0.0
    healthy = ctx.healthy_base_days(p, day, reason)
    n_healthy = sum(ctx.blocks(p, [d]).get(reason, {}).get("n", 0.0) for d in healthy) / max(1, len(healthy))
    per_dau_healthy = n_healthy / ctx.dau_base(p) if ctx.dau_base(p) else 0.0
    fb = ctx.first_block_day(p, reason)
    since = f"first seen {_dname(fb)}" if fb is not None else "new"
    before = "none before" if per_dau_healthy < 0.01 else f"{fmt.num(per_dau_healthy)}/DAU before"
    ev = [f"`{reason}`: {fmt.num(n_now)} blocks = {fmt.num(per_dau_now)}/DAU, hitting {fmt.pct(players_share, False)} of players ({since}; {before})"]
    tags = {"reason": reason, "first_block_day": fb}
    fix = "Check the remote config for the ad placements / trigger points this build expects; configure or roll back."
    conf = "likely"
    summary = ""
    attr = attribute_blocks_to_arm(ctx, p, day, reason)
    if attr:
        key, arm, top, share, contrast = attr
        if True:
            at = ad_type or "interstitial"
            adau_now = ctx.arm_dau(p, key, [day])
            arm_dau = adau_now.get(arm, 0.0)
            per_arm_dau = top["n"] / arm_dau if arm_dau else None
            arm_players_share = top["players"] / arm_dau if arm_dau else None
            arm_share_of_test = arm_dau / (sum(adau_now.values()) or 1)
            ev.append(f"{fmt.pct(share, False)} of them in `{key}` *{_pretty_arm(arm)}* ({fmt.pct(arm_share_of_test, False)} of the test)"
                      + (f": {fmt.num(per_arm_dau)}/DAU there vs {fmt.num(per_arm_dau / contrast) if contrast != float('inf') else '0'}/DAU in the other arm(s)" if per_arm_dau else ""))
            tags.update(exp_key=key, arm=arm)
            # same-day arm vs arm on the hurting ad type, plus each arm's own healthy history
            ai_now, ai_h = ctx.arm_imps(p, key, at, [day]), ctx.arm_imps(p, key, at, healthy)
            ad_h = ctx.arm_dau(p, key, healthy)
            parts = []
            for a in sorted(set(adau_now) | set(ai_now)):
                if adau_now.get(a, 0) < 50:
                    continue
                rn = safe_div(ai_now.get(a, {}).get("imps", 0.0), adau_now.get(a, 0.0))
                rh = safe_div(ai_h.get(a, {}).get("imps", 0.0), ad_h.get(a, 0.0))
                if rn is None:
                    continue
                parts.append(f"{_pretty_arm(a)} {fmt.num(rh)} → {fmt.num(rn)}" if rh else f"{_pretty_arm(a)} {fmt.num(rn)}")
            if parts:
                ev.append(f"{SHORT[at].upper()} imps/DAU by arm: " + "; ".join(parts))
            # the placement that died: this arm vs the other arms, per DAU, same day
            dead = arm_dead_placement(ctx, p, [day], key, arm, at, healthy)
            if dead:
                plc, drop, r_arm, r_oth, r_h = dead
                ref_txt = f"{fmt.num(r_oth)}/DAU in the other arm(s)" if r_oth else f"{fmt.num(r_h)}/DAU before"
                ev.append(f"its `{plc or '(none)'}` placement: {fmt.num(r_arm)}/DAU vs {ref_txt} ({fmt.arrow_pct(drop)})")
                tags["placement"] = plc
                conf = "confirmed"
            wins_now = ctx.arm_wins(p, key, [day]).get(arm, 0.0)
            if wins_now and 0.6 <= top["n"] / wins_now <= 1.5:
                ev.append(f"≈{fmt.num(top['n'] / wins_now, 2)} blocks per level win in that arm: every win asks and is refused")
                conf = "confirmed"
            fix = (f"Configure the `{key}` *{_pretty_arm(arm)}* trigger point(s) in remote config on {ctx.pname(p)}, or pause the arm. "
                   f"{_dname(fb) if fb is not None else 'The broken days'} → fix day is a dead window for reads of this test.")
            summary = (f"{at}s stopped showing for the `{key}` *{_pretty_arm(arm)}* players ({fmt.pct(arm_share_of_test, False)} of {ctx.pname(p)}): "
                       f"the game asks on every level win and its own config refuses it (`{reason}`)")
    hard = per_dau_now >= ctx.cfg.severity.hard_break_blocks_per_dau and players_share >= 0.10
    head = (f"{ctx.pname(p)} {spec_label}: the game refused its own ads — `{reason}` {fmt.num(per_dau_now)}/DAU on {fmt.pct(players_share, False)} of players"
            + (f", in `{tags['exp_key']}` *{_pretty_arm(tags['arm'])}*" if "exp_key" in tags else ""))
    if "exp_key" not in tags:
        summary = f"the game is refusing its own ads (`{reason}` on {fmt.pct(players_share, False)} of {ctx.pname(p)} players) — a remote-config or SDK setup problem"
    return Diagnosis("config_break", conf, head, ev, [], fix, hard_break=hard, tags=tags, summary=summary), None


def attribute_blocks_to_arm(ctx: Ctx, p, day: int, reason: str) -> tuple | None:
    """(key, arm, {'n','players'}, share of the key's blocks, contrast) for the experiment whose arms
    separate the blocked players best. A player sits in many keys, so a block is counted under every
    one of them; a key where one arm holds ~all players (a finished test logged as 'control') has no
    contrast and is skipped. Needs >= 80% of the key's blocks in one arm at >= 5x the other arms' rate."""
    by = ctx.blocks_by_arm(p, [day], reason)
    best = None
    for key in {k for k, _ in by}:
        arms = {a: v for (k, a), v in by.items() if k == key}
        dau = ctx.arm_dau(p, key, [day])
        tot_dau = sum(dau.values())
        if not tot_dau or len(dau) < 2 or max(dau.values()) / tot_dau >= 0.95:
            continue
        top_arm = max(arms, key=lambda a: arms[a]["n"])
        if not dau.get(top_arm):
            continue
        n_all = sum(v["n"] for v in arms.values()) or 1.0
        share = arms[top_arm]["n"] / n_all
        rate_top = arms[top_arm]["n"] / dau[top_arm]
        rest_n = n_all - arms[top_arm]["n"]
        rest_dau = tot_dau - dau[top_arm]
        rate_rest = rest_n / rest_dau if rest_dau else 0.0
        contrast = rate_top / rate_rest if rate_rest else float("inf")
        if share >= 0.8 and contrast >= 5 and (best is None or (contrast, share) > (best[4], best[3])):
            best = (key, top_arm, arms[top_arm], share, contrast)
    return best


def arm_dead_placement(ctx: Ctx, p, days_now: list[int], key: str, arm: str, ad_type: str, healthy: list[int],
                       min_ref: float = 0.3, min_drop: float = -0.7) -> tuple | None:
    """(placement, drop, rate_arm, rate_others, rate_arm_before) for the placement that collapsed in
    `arm` — its imps per arm player over `days_now` against the other arms over the same days and
    against the arm's own rate over `healthy`; None when no placement fell by at least `min_drop`.
    Only placements that usually run >= `min_ref` per player are read. `days_now` / `healthy` are
    daily days for the daily run and window indices (0 = now) for the intraday check."""
    adau_now = ctx.arm_dau(p, key, days_now)
    arm_dau = adau_now.get(arm, 0.0)
    pl_arm = ctx.arm_placement_imps(p, key, arm, ad_type, days_now)
    others = [a for a in adau_now if a != arm and adau_now[a] >= 50]
    pl_oth: dict[str, float] = {}
    oth_dau = sum(adau_now[a] for a in others) or 0.0
    for a in others:
        for plc, n in ctx.arm_placement_imps(p, key, a, ad_type, days_now).items():
            pl_oth[plc] = pl_oth.get(plc, 0.0) + n
    pl_h = ctx.arm_placement_imps(p, key, arm, ad_type, healthy)
    arm_dau_h = ctx.arm_dau(p, key, healthy).get(arm, 0.0)
    worst = None
    for plc in set(pl_oth) | set(pl_h):
        r_oth = pl_oth.get(plc, 0.0) / oth_dau if oth_dau else None
        r_h = pl_h.get(plc, 0.0) / arm_dau_h if arm_dau_h else None
        ref = r_oth if r_oth else r_h
        if not ref or ref < min_ref:
            continue
        r_arm = pl_arm.get(plc, 0.0) / arm_dau if arm_dau else 0.0
        drop = r_arm / ref - 1.0
        if worst is None or drop < worst[1]:
            worst = (plc, drop, r_arm, r_oth, r_h)
    return worst if (worst and worst[1] <= min_drop) else None


def _dname(d: int | None) -> str:
    from app.monitor.pull import day_date
    return day_date(d).strftime("%b %d") if d is not None else "n/a"


def _placement_collapse(ctx: Ctx, p, day: int, ad_type: str) -> tuple[Diagnosis | None, str | None]:
    base = ctx.healthy_base_days(p, day, ctx.rising_block_reason(p, day)[0])
    now = ctx.imps_by(p, ad_type, "placement", [day], placement_norm=True)
    bse = ctx.imps_by(p, ad_type, "placement", base, placement_norm=True)
    if not bse:
        return None, None
    nb = len(base) or 1
    rows = []
    for plc, v in bse.items():
        per_day = v["imps"] / nb
        if per_day < 100:
            continue
        nn = now.get(plc, {}).get("imps", 0.0)
        rows.append((plc, per_day, nn, nn / per_day - 1.0))
    if not rows:
        return None, None
    rows.sort(key=lambda r: r[3])
    worst = rows[0]
    others = [r for r in rows[1:]]
    if worst[3] <= -ctx.cfg.severity.hard_break_placement_drop and all(r[3] > -0.35 for r in others):
        ev = [f"placement `{worst[0] or '(none)'}`: {fmt.num(worst[1])}/day → {fmt.num(worst[2])} ({fmt.arrow_pct(worst[3])})"]
        if others:
            ev.append("other placements held: " + ", ".join(f"`{r[0] or '(none)'}` {fmt.arrow_pct(r[3])}" for r in others[:3]))
        return Diagnosis("placement", "likely",
                         f"{ctx.pname(p)}: the `{worst[0]}` {ad_type} placement stopped firing",
                         ev, [], "Check the client trigger for that placement (build / remote config) — no block reason was logged for it.",
                         hard_break=True, tags={"placement": worst[0]}), None
    return None, "placements (" + ", ".join(f"`{r[0] or '(none)'}` {fmt.arrow_pct(r[3])}" for r in rows[:4]) + ")"


def _engagement_follows(ctx: Ctx, p, day: int, rel_metric: float) -> tuple[Diagnosis | None, str]:
    wins = ctx.s.get("wins_per_dau", p, day)
    base = ctx.base_days(day)
    wins_b = median([ctx.s.get("wins_per_dau", p, d) for d in base if ctx.s.get("wins_per_dau", p, d) is not None] or [0])
    starts = ctx.s.get("starts_per_dau", p, day)
    starts_b = median([ctx.s.get("starts_per_dau", p, d) for d in base if ctx.s.get("starts_per_dau", p, d) is not None] or [0])
    rw = (wins - wins_b) / wins_b if (wins is not None and wins_b) else None
    rs = (starts - starts_b) / starts_b if (starts is not None and starts_b) else None
    if rw is not None and rw <= -0.08 and rel_metric < 0 and rw <= 0.6 * rel_metric:
        return Diagnosis("engagement", "likely", f"{ctx.pname(p)}: ad volume fell with play",
                         [f"level wins/DAU {fmt.num(wins_b)} → {fmt.num(wins)} ({fmt.arrow_pct(rw)}); starts/DAU {fmt.arrow_pct(rs)}"],
                         [], None), ""
    return None, f"play (wins/DAU {fmt.arrow_pct(rw)}, starts/DAU {fmt.arrow_pct(rs)})"


def _fill(ctx: Ctx, p, day: int, ad_type: str) -> tuple[Diagnosis | None, str]:
    """Networks, not the game: requests per DAU held while loads per DAU fell, or shows stopped being
    billed. Ratios on raw request / load_fail rows are not used (retry logging swings them)."""
    base = ctx.base_days(day)
    f_now, f_base = ctx.funnel(p, ad_type, [day]), ctx.funnel(p, ad_type, base)
    nb = len(base) or 1
    dau_now, dau_b = ctx.dau(p, day), ctx.dau_base(p)
    req_pd, req_pd_b = safe_div(f_now.get("request", 0.0), dau_now), safe_div(f_base.get("request", 0.0) / nb, dau_b)
    load_pd, load_pd_b = safe_div(f_now.get("load", 0.0), dau_now), safe_div(f_base.get("load", 0.0) / nb, dau_b)
    show_per_load = safe_div(f_now.get("show_ad", 0.0), f_now.get("load", 0.0))
    show_per_load_b = safe_div(f_base.get("show_ad", 0.0), f_base.get("load", 0.0))
    billed_per_show = safe_div(f_now.get("revenue", 0.0), f_now.get("show_ad", 0.0))
    billed_per_show_b = safe_div(f_base.get("revenue", 0.0), f_base.get("show_ad", 0.0))
    note = (f"fill (requests/DAU {fmt.num(req_pd_b)} → {fmt.num(req_pd)}, loads/DAU {fmt.num(load_pd_b)} → {fmt.num(load_pd)}, "
            f"billed/show {fmt.num(billed_per_show, 2)})")
    rel_load = (load_pd - load_pd_b) / load_pd_b if (load_pd is not None and load_pd_b) else None
    if rel_load is not None and rel_load <= -0.10 and req_pd and req_pd_b and req_pd >= 0.85 * req_pd_b:
        by_net_now = ctx.funnel_by_network(p, ad_type, "load", [day])
        by_net_b = ctx.funnel_by_network(p, ad_type, "load", base)
        drops = []
        for n_, vb in by_net_b.items():
            vb_d = vb / nb
            if vb_d >= 100:
                drops.append((n_, vb_d, by_net_now.get(n_, 0.0), by_net_now.get(n_, 0.0) / vb_d - 1))
        drops.sort(key=lambda r: r[3])
        ev = [note]
        if drops:
            ev.append("loads by network: " + ", ".join(f"{d[0]} {fmt.arrow_pct(d[3])}" for d in drops[:4]))
        return Diagnosis("ad_fill", "likely", f"{ctx.pname(p)}: {ad_type} requests held but loads per DAU fell {fmt.arrow_pct(rel_load)}",
                         ev, [], "Check the MAX waterfall / network SDKs for that ad type; a network stopped filling."), note
    if billed_per_show is not None and billed_per_show_b and billed_per_show < 0.8 * billed_per_show_b:
        return Diagnosis("ad_fill", "likely", f"{ctx.pname(p)}: {ad_type} shows are not being billed (billed/show {fmt.num(billed_per_show, 2)} vs {fmt.num(billed_per_show_b, 2)})",
                         [note], [], "Check the revenue callback / impression logging on the newest build."), note
    return None, note


def _rv_cross_check(ctx: Ctx, p, day: int, ad_type: str) -> tuple[None, str]:
    """The other full-screen type as a control: if it held, play, fill and logging are fine."""
    other = "rewarded" if ad_type == "interstitial" else "interstitial"
    key = f"{SHORT[other]}_imps_per_dau"
    v = ctx.s.get(key, p, day)
    b = median([ctx.s.get(key, p, d) for d in ctx.base_days(day) if ctx.s.get(key, p, d) is not None] or [0])
    r = (v - b) / b if (v is not None and b) else None
    return None, f"{'rewarded' if other == 'rewarded' else 'interstitials'} ({SHORT[other].upper()}/DAU {fmt.num(b)} → {fmt.num(v)}, {fmt.arrow_pct(r)})"


def _mix(ctx: Ctx, p, day: int, ad_type: str) -> tuple[Diagnosis | None, str]:
    base = ctx.base_days(day)
    w_now, r_now = _band_rates(ctx, p, ad_type, "age", [day])
    w_b, r_b = _band_rates(ctx, p, ad_type, "age", base)
    total, mix, within = kitagawa(w_now, w_b, r_now, r_b)
    d0_now, d0_b = w_now.get("D0", 0.0), w_b.get("D0", 0.0)
    band_txt = ", ".join(f"{k} {fmt.arrow_pct((r_now.get(k, 0) - r_b[k]) / r_b[k])}" for k in ("D0", "D1-6", "D7-29", "D30+") if r_b.get(k))
    if total < 0 and abs(mix) >= 0.6 * abs(total) and abs(d0_now - d0_b) >= 0.03:
        adj_txt = "; " + ua_text(ua_context(ctx, p, day))
        return Diagnosis("ua_mix", "likely", f"{ctx.pname(p)}: audience mix, not behaviour — D0 share of DAU {fmt.pct(d0_b, False)} → {fmt.pct(d0_now, False)}",
                         [f"mix explains {fmt.pct(mix / total, False)} of the move; within each age band: {band_txt}" + adj_txt],
                         [], None), ""
    return None, f"audience mix (D0 share {fmt.pct(d0_b, False)} → {fmt.pct(d0_now, False)}; age bands {band_txt})"


def _experiment_change(ctx: Ctx, p, day: int, ad_type: str | None, metric_key: str) -> tuple[Diagnosis | None, str]:
    """A key whose daily assigned players jumped in the last 3 days (start / ramp), with an arm gap."""
    recent = [d for d in (day - 2, day - 1, day) if d in ctx.s.days]
    earlier = [d for d in range(day - 9, day - 2) if d in ctx.s.days]
    now = ctx.exp_players(p, recent)
    before = ctx.exp_players(p, earlier)
    dau_now = ctx.dau(p, day) or 1.0
    keys_now: dict[str, float] = {}
    keys_before: dict[str, float] = {}
    for (k, a), v in now.items():
        keys_now[k] = keys_now.get(k, 0.0) + v
    for (k, a), v in before.items():
        keys_before[k] = keys_before.get(k, 0.0) + v
    changed = []
    for k, v in keys_now.items():
        vb = keys_before.get(k, 0.0)
        if v >= 0.05 * dau_now and (vb == 0 or v >= 1.8 * vb):
            changed.append((k, vb, v))
    if not changed:
        return None, "no experiment change in 3 days"
    changed.sort(key=lambda r: -(r[2] - r[1]))
    k, vb, v = changed[0]
    ev = [f"`{k}`: {fmt.num(vb)} → {fmt.num(v)} assigned players/day" + (" (new on this platform)" if vb == 0 else " (ramp)")]
    conf = "possible"
    if ad_type and ad_type != "banner":
        base = ctx.base_days(day)
        ai, ad = ctx.arm_imps(p, k, ad_type, [day]), ctx.arm_dau(p, k, [day])
        aib, adb = ctx.arm_imps(p, k, ad_type, base), ctx.arm_dau(p, k, base)
        rates = {a: safe_div(ai.get(a, {}).get("imps", 0.0), ad.get(a, 0.0)) for a in ad}
        rates_b = {a: safe_div(aib.get(a, {}).get("imps", 0.0), adb.get(a, 0.0)) for a in adb}
        vals = [(a, r) for a, r in rates.items() if r is not None and ad.get(a, 0) >= 100]
        if len(vals) >= 2:
            lo, hi = min(vals, key=lambda t: t[1]), max(vals, key=lambda t: t[1])
            if hi[1] and (hi[1] - lo[1]) / hi[1] >= 0.25:
                ev.append(f"{SHORT[ad_type].upper()} imps/DAU by arm: " + "; ".join(
                    f"{_pretty_arm(a)} {fmt.num(rates_b.get(a))} → {fmt.num(r)}" for a, r in sorted(vals)))
                conf = "likely"
    return Diagnosis("experiment_change", conf, f"{ctx.pname(p)}: `{k}` started or ramped in the last 3 days and its arms differ",
                     ev, [], f"Check `{k}`'s configuration and whether the arm gap is intended.", tags={"exp_key": k}), ""


def _build_rollout(ctx: Ctx, p, day: int, ad_type: str | None) -> tuple[Diagnosis | None, str]:
    base = ctx.base_days(day)
    sh_now = _shares(ctx.dau_by(p, "app_version", [day]))
    sh_b = _shares(ctx.dau_by(p, "app_version", base))
    new = [(v, sh_now[v], sh_b.get(v, 0.0)) for v in sh_now if sh_now[v] >= 0.15 and sh_now[v] - sh_b.get(v, 0.0) >= 0.10]
    if not new:
        top = max(sh_now.items(), key=lambda kv: kv[1]) if sh_now else None
        return None, (f"build (top {top[0]} at {fmt.pct(top[1], False)}, no rollout)" if top else "no build data")
    new.sort(key=lambda r: -(r[1] - r[2]))
    v, s_now, s_b = new[0]
    ev = [f"build {v}: {fmt.pct(s_b, False)} → {fmt.pct(s_now, False)} of DAU"]
    conf = "possible"
    if ad_type and ad_type != "banner":
        imps_v = ctx.imps_by(p, ad_type, "app_version", [day])
        dau_v = ctx.dau_by(p, "app_version", [day])
        r_new = safe_div(imps_v.get(v, {}).get("imps", 0.0), dau_v.get(v, 0.0))
        oth_i = sum(x["imps"] for k, x in imps_v.items() if k != v)
        oth_d = sum(x for k, x in dau_v.items() if k != v)
        r_oth = safe_div(oth_i, oth_d)
        if r_new is not None and r_oth:
            ev.append(f"{SHORT[ad_type].upper()} imps/DAU on {v}: {fmt.num(r_new)} vs {fmt.num(r_oth)} on other builds")
            if r_new <= 0.75 * r_oth:
                conf = "likely"
            else:
                return None, f"build {v} ({fmt.pct(s_now, False)} of DAU, in line: {fmt.num(r_new)} vs {fmt.num(r_oth)} {SHORT[ad_type].upper()}/DAU)"
    return Diagnosis("build_rollout", conf, f"{ctx.pname(p)}: build {v} reached {fmt.pct(s_now, False)} of DAU and behaves differently",
                     ev, [], f"Compare {v} against the previous build like for like (same install day) before ramping further.",
                     tags={"build": v, "share": s_now}), ""


def _network_price(ctx: Ctx, p, day: int, ad_type: str) -> tuple[Diagnosis | None, str]:
    base = ctx.base_days(day)
    nb = len(base) or 1
    now, bse = ctx.imps_by(p, ad_type, "network", [day]), ctx.imps_by(p, ad_type, "network", base)
    rows = []
    tot_now = sum(v["imps"] for v in now.values()) or 1.0
    tot_b = sum(v["imps"] for v in bse.values()) or 1.0
    for n_, vb in bse.items():
        if vb["imps"] / nb < 200:
            continue
        e_b = safe_div(vb["rev"] * 1000, vb["imps"])
        vn = now.get(n_, {"imps": 0.0, "rev": 0.0})
        e_n = safe_div(vn["rev"] * 1000, vn["imps"])
        rows.append((n_, e_b, e_n, vb["imps"] / tot_b, vn["imps"] / tot_now))
    if not rows:
        return None, "no network split available"
    moved = [(n_, e_b, e_n, sb, sn) for n_, e_b, e_n, sb, sn in rows if e_b and e_n is not None and (e_n - e_b) / e_b <= -0.15]
    held = [r for r in rows if r not in moved]
    share_txt = ", ".join(f"{r[0]} {fmt.usd(r[1])}→{fmt.usd(r[2])} ({fmt.pct(r[3], False)}→{fmt.pct(r[4], False)} share)" for r in sorted(rows, key=lambda r: -r[3])[:4])
    if moved and len(moved) <= max(1, len(rows) // 3) and held:
        n_, e_b, e_n, sb, sn = max(moved, key=lambda r: r[3])
        return Diagnosis("network_price", "likely", f"{ctx.pname(p)}: {ad_type} eCPM fell on {n_} ({fmt.usd(e_b)} → {fmt.usd(e_n)}, {fmt.pct(sb, False)} of impressions), the others held",
                         [f"eCPM by network: {share_txt}"], [], f"Check {n_}'s line items / bids for this ad type."), ""
    if moved and len(moved) >= max(2, int(0.6 * len(rows))):
        return None, f"every network fell ({len(moved)}/{len(rows)}): {share_txt}"
    return None, f"networks: {share_txt}"


def _market_price(ctx: Ctx, p, day: int, ad_type: str, rel_ours: float) -> tuple[Diagnosis | None, str]:
    base = ctx.base_days(day)
    m_now, m_b = ctx.market_ecpm(p, ad_type, [day]), ctx.market_ecpm(p, ad_type, base)
    if m_now is None or not m_b:
        return None, "no market control (games %s) data" % ",".join(map(str, ctx.cfg.market_control_games))
    rel_m = (m_now - m_b) / m_b
    txt = f"market (control games {ad_type} eCPM {fmt.usd(m_b)} → {fmt.usd(m_now)}, {fmt.arrow_pct(rel_m)})"
    if rel_ours < 0 and rel_m <= 0.5 * rel_ours:
        return Diagnosis("market_price", "likely", f"{ctx.pname(p)}: {ad_type} price fell across the market, not just here",
                         [txt], [], None), ""
    return None, txt


def _ecpm_mix(ctx: Ctx, p, day: int, ad_type: str) -> tuple[Diagnosis | None, str]:
    """eCPM at a fixed age x geo mix: if the within-band price held, the move is mix."""
    base = ctx.base_days(day)
    if ad_type == "banner":
        return None, ""

    def cells(days):
        rows = ctx._rows("ad_cut", p, days, lambda r: r.get("ad_type") == ad_type)
        g = group_sum(rows, ("age", "geo"), ("imps", "rev"))
        return {k: v for k, v in g.items()}

    c_now, c_b = cells([day]), cells(base)
    tot_now = sum(v["imps"] for v in c_now.values()) or 1.0
    tot_b = sum(v["imps"] for v in c_b.values()) or 1.0
    w_now = {k: v["imps"] / tot_now for k, v in c_now.items()}
    w_b = {k: v["imps"] / tot_b for k, v in c_b.items()}
    e_now = {k: v["rev"] * 1000 / v["imps"] for k, v in c_now.items() if v["imps"]}
    e_b = {k: v["rev"] * 1000 / v["imps"] for k, v in c_b.items() if v["imps"]}
    total, mix, within = kitagawa(w_now, w_b, e_now, e_b)
    if total < 0 and abs(mix) >= 0.6 * abs(total):
        return Diagnosis("ua_mix", "likely", f"{ctx.pname(p)}: {ad_type} eCPM fell because the impression mix shifted to cheaper players, not because prices fell",
                         [f"mix explains {fmt.pct(mix / total, False)} of the move at fixed age × geo; within-cell price {fmt.arrow_pct(within / (sum(w_b.get(k, 0) * e_b.get(k, 0) for k in e_b) or 1))}"],
                         [], None), ""
    return None, f"mix at fixed age×geo (explains {fmt.pct(abs(mix) / abs(total), False) if total else 'n/a'})"


# --- dispatcher ------------------------------------------------------------------------------------

def diagnose(ctx: Ctx, r: Reading, spec: MetricSpec) -> Diagnosis:
    p, day = r.platform, r.day
    ruled: list[str] = []
    pipe = check_pipeline(ctx, p, day)
    if pipe:
        return pipe

    def done(d: Diagnosis | None, note: str | None) -> Diagnosis | None:
        if d:
            d.ruled_out = [x for x in ruled if x]
            return d
        if note:
            ruled.append(note)
        return None

    fam, lever, at = spec.family, spec.lever, spec.ad_type
    rel_v = r.rel_base or 0.0

    if spec.key == "blocked_config_per_dau":
        # a blocks rise is read like an interstitial volume drop: config break first, then the same
        # cross-checks, so its message carries the same ruled-out line
        fam, lever, at = "ads", "volume", "interstitial"

    if fam == "ads" and lever == "volume":
        # Every check runs: the first that explains the move is the cause, the others become the
        # "ruled out" line (with their numbers), and a second explanation is kept as "also".
        checks = (lambda: _config_break(ctx, p, day, at, spec.label),
                  lambda: _placement_collapse(ctx, p, day, at) if at != "banner" else (None, None),
                  lambda: _engagement_follows(ctx, p, day, rel_v),
                  lambda: _fill(ctx, p, day, at) if at != "banner" else (None, None),
                  lambda: _rv_cross_check(ctx, p, day, at),
                  lambda: _mix(ctx, p, day, at),
                  lambda: _experiment_change(ctx, p, day, at, spec.key),
                  lambda: _build_rollout(ctx, p, day, at))
        winner = None
        for fn in checks:
            d, note = fn()
            if d and winner is None:
                winner = d
            elif d:
                if d.cause == "build_rollout" and winner.cause in ("config_break", "placement"):
                    # both arms sit on the new build, so its per-DAU rate reads low for the same reason
                    ruled.append(f"build {d.tags.get('build')} ({fmt.pct(d.tags.get('share'), False)} of DAU; both arms on it, so it reads low too)")
                elif d.cause == "experiment_change" and d.tags.get("exp_key") == winner.tags.get("exp_key"):
                    ruled.append(f"`{d.tags.get('exp_key')}` re-assigned players in the last 3 days (the test's ramp)")
                else:
                    ruled.append(f"also: {d.headline}")
            elif note:
                ruled.append(note)
        if winner:
            if winner.tags.get("placement") or winner.cause == "placement":
                ruled = [x for x in ruled if not x.startswith("placements (")]
            winner.ruled_out = [x for x in ruled if x]
            return winner
        if at == "banner":
            # banners pause while a full-screen ad is open: more interstitials => fewer banner rows
            it = ctx.s.get("it_imps_per_dau", p, day)
            it_b = median([ctx.s.get("it_imps_per_dau", p, d) for d in ctx.base_days(day) if ctx.s.get("it_imps_per_dau", p, d) is not None] or [0])
            if it and it_b and it > 1.08 * it_b:
                return Diagnosis("engagement", "likely", f"{ctx.pname(p)}: banner impressions fell because interstitials rose (banners pause under full-screen ads)",
                                 [f"IT imps/DAU {fmt.num(it_b)} → {fmt.num(it)}"], ruled)
        return Diagnosis("unexplained", "unexplained", f"{ctx.pname(p)} {spec.label}: every check cleared — look at it by hand", [], ruled)

    if fam == "ads" and lever == "price":
        checks = (lambda: _ecpm_mix(ctx, p, day, at),
                  lambda: _network_price(ctx, p, day, at),
                  lambda: _market_price(ctx, p, day, at, rel_v),
                  lambda: _experiment_change(ctx, p, day, at, spec.key),
                  lambda: _build_rollout(ctx, p, day, at))
        winner = None
        for fn in checks:
            d, note = fn()
            if d and winner is None:
                winner = d
            elif d:
                ruled.append(f"also: {d.headline}")
            elif note:
                ruled.append(note)
        if winner:
            winner.ruled_out = [x for x in ruled if x]
            return winner
        return Diagnosis("unexplained", "unexplained", f"{ctx.pname(p)} {spec.label}: price fell on every network at a fixed mix and the control games did not follow", [], ruled)

    if fam == "health":  # fill
        d, note = _fill(ctx, p, day, at)
        if d:
            d.ruled_out = ruled
            return d
        return Diagnosis("unexplained", "unexplained", f"{ctx.pname(p)} {spec.label} fell", [note], ruled)

    if fam == "revenue":
        return _revenue(ctx, r, spec)

    if fam == "audience":
        return _audience(ctx, r, spec)

    if fam == "engagement":
        return _engagement(ctx, r, spec)

    if fam == "retention":
        return _retention(ctx, r, spec)

    if fam == "ua":
        return _ua(ctx, r, spec)

    return Diagnosis("unexplained", "unexplained", f"{ctx.pname(p)} {spec.label} moved", [], ruled)


def revenue_decomposition(ctx: Ctx, p, day: int) -> list[tuple[str, str, float]]:
    """[(ad_type, 'volume'|'price'|'dau', $ change vs baseline)] — the $ the move cost per lever."""
    base = ctx.base_days(day)
    nb = len(base) or 1
    out = []
    dau_now, dau_b = ctx.dau(p, day), ctx.dau_base(p)
    for t in ("interstitial", "rewarded", "banner"):
        sh = SHORT[t]
        imps_now = ctx.s.get(f"{sh}_imps", p, day) or 0.0
        rev_now = ctx.s.get(f"{sh}_rev", p, day) or 0.0
        imps_b = sum(ctx.s.get(f"{sh}_imps", p, d) or 0.0 for d in base) / nb
        rev_b = sum(ctx.s.get(f"{sh}_rev", p, d) or 0.0 for d in base) / nb
        ipd_now, ipd_b = (imps_now / dau_now if dau_now else 0.0), (imps_b / dau_b if dau_b else 0.0)
        e_now = rev_now * 1000 / imps_now if imps_now else 0.0
        e_b = rev_b * 1000 / imps_b if imps_b else 0.0
        # rev = dau * ipd * ecpm/1000 ; decompose sequentially: dau, then volume (ipd), then price
        d_dau = (dau_now - dau_b) * ipd_b * e_b / 1000.0
        d_vol = dau_now * (ipd_now - ipd_b) * e_b / 1000.0
        d_price = dau_now * ipd_now * (e_now - e_b) / 1000.0
        out.append((t, "dau", d_dau))
        out.append((t, "volume", d_vol))
        out.append((t, "price", d_price))
    return out


def ua_context(ctx: Ctx, p, day: int) -> dict:
    """What UA did around the read day: Adjust spend / installs / CPI on the latest filled day vs the
    7 filled days before it (spend lands a day late), attributed paid installs, and the game's own
    new-player count. rel_* are relative changes (None when unknown)."""
    def latest(key, d):
        ser = ctx.s.series(key, p)
        avail = [t for t in ser if d - 3 <= t <= d]
        return (max(avail), ser[max(avail)]) if avail else (None, None)

    def base_of(key, d_ref, n=7):
        ser = ctx.s.series(key, p)
        vals = [ser[t] for t in range(d_ref - n, d_ref) if t in ser]
        return median(vals) if len(vals) >= 3 else None

    out = {"source": ctx.cfg.ua_source}
    for key in ("ua_spend", "ua_installs", "ua_cpi", "paid_installs", "installs"):
        d_ref, v = latest(key, day)
        b = base_of(key, d_ref) if d_ref is not None else None
        out[key] = v
        out[key + "_day"] = d_ref
        out[key + "_base"] = b
        out["rel_" + key] = (v - b) / b if (v is not None and b) else None
    return out


def ua_text(u: dict) -> str:
    """One line on UA for the evidence / ruled-out lists."""
    bits = []
    if u.get("ua_spend") is not None and u.get("ua_spend_base"):
        bits.append(f"spend {fmt.usd(u['ua_spend_base'])} → {fmt.usd(u['ua_spend'])}/day ({fmt.arrow_pct(u['rel_ua_spend'])}, Adjust to {_dname(u['ua_spend_day'])})")
    if u.get("ua_installs") is not None and u.get("ua_installs_base"):
        bits.append(f"installs {fmt.num(u['ua_installs_base'])} → {fmt.num(u['ua_installs'])} ({fmt.arrow_pct(u['rel_ua_installs'])})")
    if u.get("ua_cpi") is not None and u.get("ua_cpi_base"):
        bits.append(f"CPI {fmt.usd(u['ua_cpi_base'])} → {fmt.usd(u['ua_cpi'])}")
    if not bits and u.get("paid_installs") is not None and u.get("paid_installs_base"):
        bits.append(f"attributed paid installs {fmt.num(u['paid_installs_base'])} → {fmt.num(u['paid_installs'])} ({fmt.arrow_pct(u['rel_paid_installs'])}; no spend data — not in Adjust)")
    return "UA: " + ", ".join(bits) if bits else "UA: no spend data"


def ua_cut(u: dict) -> bool:
    """Spend (or, without Adjust, paid installs) down at least 20%."""
    r = u.get("rel_ua_spend") if u.get("rel_ua_spend") is not None else u.get("rel_paid_installs")
    return r is not None and r <= -0.20


def _ua(ctx: Ctx, r: Reading, spec: MetricSpec) -> Diagnosis:
    p, day = r.platform, r.day
    u = ua_context(ctx, p, day)
    inst_rel = u.get("rel_installs")
    ev = [ua_text(u)]
    if inst_rel is not None:
        ev.append(f"new players in the game {fmt.num(u['installs_base'])} → {fmt.num(u['installs'])}/day ({fmt.arrow_pct(inst_rel)})")
    head = (f"{ctx.pname(p)}: UA spend cut {fmt.arrow_pct(u.get('rel_ua_spend'))} (Adjust)" if spec.key == "ua_spend"
            else f"{ctx.pname(p)}: attributed paid installs {fmt.arrow_pct(u.get('rel_paid_installs'))}")
    summary = (f"UA on {ctx.pname(p)} was cut — fewer paid installs, so fewer new players, DAU and revenue follow over the next days; "
               f"a UA decision, not a game problem")
    return Diagnosis("ua_cut", "confirmed", head, ev, [], "Confirm the spend change with UA; nothing to fix in the game.", summary=summary)


def _revenue(ctx: Ctx, r: Reading, spec: MetricSpec) -> Diagnosis:
    p, day = r.platform, r.day
    parts = revenue_decomposition(ctx, p, day)
    total = sum(x[2] for x in parts)
    if total >= 0:
        return Diagnosis("unexplained", "unexplained", f"{ctx.pname(p)} {spec.label} moved", [], [])
    neg = sorted([x for x in parts if x[2] < 0], key=lambda x: x[2])
    t, lever, d_usd = neg[0]
    share = d_usd / total
    lever_txt = {"volume": f"{SHORT[t].upper()} impressions per DAU", "price": f"{SHORT[t].upper()} eCPM", "dau": "DAU"}[lever]
    others = [f"{SHORT[x[0]].upper()} {x[1]} {fmt.usd(x[2])}" for x in neg[1:3]]
    ev = [f"{fmt.pct(share, False)} of the {fmt.usd(total)}/day is {lever_txt}" + (f" (then {', '.join(others)})" if others else "")]
    # hand the dominant lever to its own rule set
    if lever == "dau":
        sub = _audience(ctx, Reading("dau", p, day, ctx.dau(p, day), ctx.dau_base(p), None, None,
                                     (ctx.dau(p, day) - ctx.dau_base(p)) / (ctx.dau_base(p) or 1), None, None, None, 0, True, "down"),
                        ctx.cfg.metrics.get("dau", spec))
    else:
        key = f"{SHORT[t]}_{'imps_per_dau' if lever == 'volume' else 'ecpm'}"
        sub_spec = ctx.cfg.metrics.get(key)
        if sub_spec is None:
            sub_spec = MetricSpec(key, lever_txt, "rate", "down_is_bad", 0.0, "ads", ad_type=t, lever=lever)
        v = ctx.s.get(key, p, day)
        vb = median([ctx.s.get(key, p, d) for d in ctx.base_days(day) if ctx.s.get(key, p, d) is not None] or [0])
        sub = diagnose(ctx, Reading(key, p, day, v, vb, None, None, (v - vb) / vb if (v is not None and vb) else 0.0,
                                    None, None, None, 0, True, "down"), sub_spec)
    u = ua_context(ctx, p, day)
    cut = ua_cut(u)
    ua_line = ua_text(u)
    if cut and sub.cause in ("ua_mix", "ua_cut", "retention", "unexplained"):
        # the money followed the spend: fewer new players (who play and pay the most per day)
        head = f"{ctx.pname(p)}: UA spend was cut ({fmt.arrow_pct(u.get('rel_ua_spend') if u.get('rel_ua_spend') is not None else u.get('rel_paid_installs'))}) — fewer new players, so {lever_txt} fell"
        summary = (f"UA spend on {ctx.pname(p)} was cut — fewer new players, so {lever_txt} fell. "
                   f"A UA decision, not a game problem")
        out = Diagnosis("ua_cut", "likely", head, [ua_line] + sub.evidence + ev, sub.ruled_out, "Confirm the spend change with UA; nothing to fix in the game.",
                        False, dict(sub.tags, lever=lever, ad_type=t), summary=summary)
        return out
    ruled = list(sub.ruled_out)
    if ua_line and not cut:
        ruled.append(ua_line.replace("UA: ", "UA steady (") + ")" if "no spend data" not in ua_line else ua_line)
    out = Diagnosis(sub.cause, sub.confidence, sub.headline, sub.evidence + ev, ruled, sub.fix, sub.hard_break,
                    dict(sub.tags, lever=lever, ad_type=t), summary=sub.summary)
    return out


def _audience(ctx: Ctx, r: Reading, spec: MetricSpec) -> Diagnosis:
    p, day = r.platform, r.day
    base = ctx.base_days(day)
    ruled = []
    inst_now = ctx.s.get("installs", p, day) or 0.0
    inst_b = median([ctx.s.get("installs", p, d) or 0.0 for d in base] or [0])
    ret_now = (ctx.s.get("dau", p, day) or 0.0) - inst_now
    ret_b = median([(ctx.s.get("dau", p, d) or 0.0) - (ctx.s.get("installs", p, d) or 0.0) for d in base] or [0])
    r_inst = (inst_now - inst_b) / inst_b if inst_b else None
    r_ret = (ret_now - ret_b) / ret_b if ret_b else None
    split = f"new players {fmt.num(inst_b)} → {fmt.num(inst_now)} ({fmt.arrow_pct(r_inst)}); returning {fmt.num(ret_b)} → {fmt.num(ret_now)} ({fmt.arrow_pct(r_ret)})"
    # UA
    u = ua_context(ctx, p, day)
    cost_now, cost_b = u.get("ua_spend"), u.get("ua_spend_base")
    adj_inst_now = u.get("ua_installs") if u.get("ua_installs") is not None else u.get("paid_installs")
    adj_inst_b = u.get("ua_installs_base") if u.get("ua_installs_base") is not None else u.get("paid_installs_base")
    ua_txt = ua_text(u)
    if spec.key == "installs" or (r_inst is not None and r_inst <= -0.15 and (r_ret is None or r_inst <= 2 * r_ret)):
        what = "DAU" if spec.key == "dau" else "new players"
        if cost_b and cost_now and (cost_now - cost_b) / cost_b <= -0.25:
            rel_sp = (cost_now - cost_b) / cost_b
            return Diagnosis("ua_cut", "likely", f"{ctx.pname(p)}: fewer new players because UA spend was cut", [split, ua_txt], [],
                             "Confirm the spend change with UA; nothing to fix in the game.",
                             summary=f"UA spend on {ctx.pname(p)} was cut ({fmt.arrow_pct(rel_sp)} to {fmt.usd(cost_now)}/day) — fewer new players, so {what} fell. A UA decision, not a game problem")
        if adj_inst_b and adj_inst_now and (adj_inst_now - adj_inst_b) / adj_inst_b <= -0.15:
            rel_in = (adj_inst_now - adj_inst_b) / adj_inst_b
            return Diagnosis("ua_cut", "likely", f"{ctx.pname(p)}: fewer installs from UA (installs {fmt.arrow_pct(rel_in)}) at similar spend — CPI rose",
                             [split, ua_txt], [], "Check campaign CPI / bids with UA.",
                             summary=f"UA on {ctx.pname(p)} is buying fewer installs ({fmt.arrow_pct(rel_in)}) for the same money — fewer new players, so {what} fell. Check CPI with UA")
        ruled.append(ua_txt)
        return Diagnosis("unexplained", "possible", f"{ctx.pname(p)}: fewer new players while Adjust installs held — attribution or install-logging gap?", [split], ruled)
    ruled.append(f"new players held ({fmt.arrow_pct(r_inst)})")
    # returning players fell: retention
    roll_now = ctx.s.get("rolling_d1", p, day - 1)
    roll_b = median([ctx.s.get("rolling_d1", p, d) for d in base if ctx.s.get("rolling_d1", p, d) is not None] or [0])
    if roll_now is not None and roll_b and roll_now < 0.92 * roll_b:
        return Diagnosis("retention", "likely", f"{ctx.pname(p)}: returning players fell — rolling D1 {fmt.pct(roll_b, False)} → {fmt.pct(roll_now, False)}",
                         [split], ruled, "Check what changed for existing players yesterday (build, experiment ramp, level set).")
    d, note = _build_rollout(ctx, p, day, None)
    if d:
        d.evidence.insert(0, split)
        d.ruled_out = ruled
        return d
    ruled.append(note)
    return Diagnosis("unexplained", "unexplained", f"{ctx.pname(p)} {spec.label} fell", [split], ruled)


def _engagement(ctx: Ctx, r: Reading, spec: MetricSpec) -> Diagnosis:
    p, day = r.platform, r.day
    base = ctx.base_days(day)
    ruled = []
    col = "wins" if spec.key == "wins_per_dau" else "starts"
    # age-mix Kitagawa on play per DAU
    dau_now, dau_b = ctx.dau_by(p, "age", [day]), ctx.dau_by(p, "age", base)
    pl_now, pl_b = ctx.play_by(p, "age", [day]), ctx.play_by(p, "age", base)
    r_now = {k: pl_now.get(k, {}).get(col, 0.0) / v for k, v in dau_now.items() if v}
    r_b = {k: pl_b.get(k, {}).get(col, 0.0) / v for k, v in dau_b.items() if v}
    total, mix, within = kitagawa(_shares(dau_now), _shares(dau_b), r_now, r_b)
    d0 = (_shares(dau_now).get("D0", 0.0), _shares(dau_b).get("D0", 0.0))
    if total < 0 and abs(mix) >= 0.6 * abs(total):
        return Diagnosis("ua_mix", "likely", f"{ctx.pname(p)}: play per DAU fell with the audience mix (D0 share {fmt.pct(d0[1], False)} → {fmt.pct(d0[0], False)}), not within cohorts",
                         [f"mix explains {fmt.pct(mix / total, False)}; within bands: " + ", ".join(f"{k} {fmt.arrow_pct((r_now.get(k, 0) - r_b[k]) / r_b[k])}" for k in ("D0", "D1-6", "D7-29", "D30+") if r_b.get(k))],
                         [], None)
    ruled.append(f"not mix (D0 share {fmt.pct(d0[1], False)} → {fmt.pct(d0[0], False)}; within bands " + ", ".join(f"{k} {fmt.arrow_pct((r_now.get(k, 0) - r_b[k]) / r_b[k])}" for k in ("D0", "D1-6", "D7-29", "D30+") if r_b.get(k)) + ")")
    # wins per start shift = logging change rather than play
    play_now, play_b = ctx.play(p, [day]), ctx.play(p, base)
    wps_now, wps_b = safe_div(play_now["wins"], play_now["starts"]), safe_div(play_b["wins"], play_b["starts"])
    if wps_now is not None and wps_b and abs(wps_now - wps_b) / wps_b >= 0.15:
        d, note = _build_rollout(ctx, p, day, None)
        ev = [f"wins per start {fmt.num(wps_b, 2)} → {fmt.num(wps_now, 2)} — a logging change, not play"]
        if d:
            return Diagnosis("build_rollout", "likely", f"{ctx.pname(p)}: level logging changed with a build ({d.tags.get('build')})", ev + d.evidence, ruled,
                             "Compare level_start / level_win per player on the new build against the old one.")
        return Diagnosis("unexplained", "possible", f"{ctx.pname(p)}: the wins-per-start ratio moved — logging, not play?", ev, ruled)
    d, note = _experiment_change(ctx, p, day, None, spec.key)
    if d:
        d.ruled_out = ruled
        return d
    ruled.append(note)
    d, note = _build_rollout(ctx, p, day, None)
    if d:
        d.ruled_out = ruled
        return d
    ruled.append(note)
    return Diagnosis("unexplained", "unexplained", f"{ctx.pname(p)} {spec.label} fell within every cohort", [], ruled)


def _retention(ctx: Ctx, r: Reading, spec: MetricSpec) -> Diagnosis:
    p, day = r.platform, r.day
    base = ctx.base_days(day)
    ruled = []
    sh_now, sh_b = _shares(ctx.dau_by(p, "age", [day])), _shares(ctx.dau_by(p, "age", base))
    d0_now, d0_b = sh_now.get("D0", 0.0), sh_b.get("D0", 0.0)
    if d0_now - d0_b >= 0.04:
        return Diagnosis("ua_mix", "likely", f"{ctx.pname(p)}: rolling D1 fell because the day had more install-day players (D0 share {fmt.pct(d0_b, False)} → {fmt.pct(d0_now, False)}), who return ~38% vs ~60%+ for older players",
                         [], [], None)
    ruled.append(f"D0 share {fmt.pct(d0_b, False)} → {fmt.pct(d0_now, False)}")
    ruled.append(ua_text(ua_context(ctx, p, day)))
    cost_now = ctx.adjust(p, day)["cost"]
    adj_now = ctx.adjust(p, day)["installs"]
    adj_b = median([ctx.adjust(p, d)["installs"] for d in base if ctx.adjust(p, d)["installs"] > 0] or [0])
    if adj_b and adj_now >= 1.3 * adj_b:
        return Diagnosis("ua_mix", "likely", f"{ctx.pname(p)}: an install surge (Adjust installs {fmt.num(adj_b)} → {fmt.num(adj_now)}) lowered the day's return rate",
                         [], ruled, None)
    d, note = _build_rollout(ctx, p, day, None)
    if d:
        d.ruled_out = ruled
        return d
    ruled.append(note)
    d, note = _experiment_change(ctx, p, day, None, spec.key)
    if d:
        d.ruled_out = ruled
        return d
    ruled.append(note)
    return Diagnosis("unexplained", "unexplained", f"{ctx.pname(p)} {spec.label} fell at the same mix", [], ruled)
