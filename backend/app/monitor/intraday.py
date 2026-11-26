"""Intraday hard-break check — the metric monitor's fast path.

Every few hours (scheduler: `intraday_monitor_job`, MONITOR_INTRADAY_ENABLED) one lightweight Redshift
job (sql/intraday.sql, ~1 min) pulls the last 8 days of one game by platform x UTC hour, and this
module compares the last `window_hours` complete hours with the same hours on each of the previous
`compare_days` days (median). It fires on hard-break signals only — what a remote-config or placement
break does within an hour of going live — and everything it fires is SEV1:

  config_block  a config-class block reason (games.toml [games.<g>.block_reasons]) at >= 0.5 per active
                player in the window, hitting >= 10% of active players, from ~0 in the comparison windows
  placement     one interstitial placement at <= 20% of its usual imps per active player while the
                other placements hold (>= 65%)
  volume        IT or RV imps per active player <= 60% of usual while level wins per player hold (>= 85%)

A break is attributed to the experiment arm whose players carry it (diagnose.attribute_blocks_to_arm
and arm_dead_placement, run on the window cuts) and dated to the hour it began. One Slack message per
break, at most once per `cooldown_hours` per signature (state.py); the daily run's message says when
an intraday alert went out. Nothing else posts from here: ordinary moves wait for the daily read.

Rates are per ACTIVE PLAYER in the window = distinct players with a session/dau event in those hours
(iw_act); counts come from the hourly cuts (ih_*). "usual" = the median of the same per-player rate
over the comparison windows.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from app.monitor import config as mcfg
from app.monitor import fmt, pull, report, slack
from app.monitor.config import GameCfg
from app.monitor.diagnose import Ctx, _pretty_arm, arm_dead_placement, attribute_blocks_to_arm
from app.monitor.frames import num
from app.monitor.series import SHORT, Series
from app.monitor.state import IntradayPost, State

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
AD_TYPES = ("interstitial", "rewarded")
KIND_LABEL = {"config_block": "config break", "placement": "dead placement", "volume": "ad volume collapse"}


def hour_num(dt: datetime) -> int:
    """Epoch hour of a UTC datetime (naive = UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((dt - EPOCH).total_seconds() // 3600)


def hour_dt(h: int) -> datetime:
    return EPOCH + timedelta(hours=int(h))


# --- the window ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Window:
    end_hr: int                 # epoch hour, exclusive: the window is the `hours` hours before it
    hours: int = 6
    compare_days: int = 7

    @property
    def start_hr(self) -> int:
        return self.end_hr - self.hours

    def hours_of(self, w: int) -> list[int]:
        """The window's hours, shifted back `w` days (w = 0 is the window itself)."""
        e = self.end_hr - 24 * w
        return list(range(e - self.hours, e))

    @property
    def compare(self) -> list[int]:
        return list(range(1, self.compare_days + 1))

    def label(self) -> str:
        return f"{hour_dt(self.start_hr):%H}:00–{hour_dt(self.end_hr):%H}:00 UTC"

    def date_label(self) -> str:
        s, e = hour_dt(self.start_hr), hour_dt(self.end_hr - 1)
        return s.strftime("%a %b %d") if s.date() == e.date() else f"{s:%a %b %d} – {e:%a %b %d}"


@dataclass
class Rate:
    """One 'What moved' line: the window's per-player rate against the usual one."""
    key: str
    label: str
    now: float | None
    base: float | None
    ratio: float | None             # now / usual; None when the usual is ~0
    severe: bool = False            # the line that tripped a rule
    extra: str = ""


@dataclass
class Break:
    platform: int
    kind: str                       # config_block | placement | volume
    ident: str                      # the reason | the placement | the ad type
    headline: str
    evidence: list[str] = field(default_factory=list)
    ruled_out: list[str] = field(default_factory=list)
    fix: str = ""
    tags: dict = field(default_factory=dict)
    rates: list[Rate] = field(default_factory=list)
    impact_usd_per_day: float | None = None
    since_hr: int | None = None     # the hour the break began (None = before the lookback)
    since_before: bool = False

    def signature(self, cfg: GameCfg) -> str:
        return f"{cfg.key}:{self.platform}:intraday:{self.kind}:{self.ident}"


# --- window-level lookups over the cuts ------------------------------------------------------------

class WCtx:
    """Lookups over the intraday cuts (sql/intraday.sql) for one window."""

    def __init__(self, cuts: dict[str, list[dict]], cfg: GameCfg, win: Window):
        self.cuts, self.cfg, self.win = cuts, cfg, win
        self.ic = cfg.intraday
        self.config_reasons = set(cfg.config_block_reasons)
        al = cfg.placement_aliases
        self.act_h: dict[tuple[int, int], float] = {}
        for r in cuts.get("ih_act", []):
            k = (int(r["platform"]), int(r["hr"]))
            self.act_h[k] = self.act_h.get(k, 0.0) + num(r.get("players"))
        self.act_w: dict[tuple[int, int], float] = {}
        for r in cuts.get("iw_act", []):
            k = (int(r["platform"]), int(r["w"]))
            self.act_w[k] = self.act_w.get(k, 0.0) + num(r.get("players"))
        self.ads_h: dict[tuple[int, int, str, str], list[float]] = {}     # (p, hr, ad_type, placement) -> [imps, rev]
        for r in cuts.get("ih_ads", []):
            plc = str(r.get("placement") or "")
            k = (int(r["platform"]), int(r["hr"]), str(r.get("ad_type")), al.get(plc, plc))
            acc = self.ads_h.setdefault(k, [0.0, 0.0])
            acc[0] += num(r.get("imps"))
            acc[1] += num(r.get("rev"))
        self.blk_w: dict[tuple[int, int, str], list[float]] = {}          # (p, w, reason) -> [n, players]
        for r in cuts.get("iw_blocked", []):
            k = (int(r["platform"]), int(r["w"]), str(r.get("reason") or ""))
            acc = self.blk_w.setdefault(k, [0.0, 0.0])
            acc[0] += num(r.get("n"))
            acc[1] += num(r.get("players"))
        self.blk_h: dict[tuple[int, int, str], float] = {}                # (p, hr, reason) -> n
        for r in cuts.get("ih_blocked", []):
            k = (int(r["platform"]), int(r["hr"]), str(r.get("reason") or ""))
            self.blk_h[k] = self.blk_h.get(k, 0.0) + num(r.get("n"))
        self.wins_h: dict[tuple[int, int], float] = {}
        for r in cuts.get("ih_play", []):
            k = (int(r["platform"]), int(r["hr"]))
            self.wins_h[k] = self.wins_h.get(k, 0.0) + num(r.get("wins"))
        self.platforms = sorted({p for (p, w) in self.act_w if w == 0})
        self._diag: Ctx | None = None

    # --- basics ---
    def pname(self, p) -> str:
        return self.cfg.platform_name(p)

    def active(self, p, w: int = 0) -> float:
        return self.act_w.get((int(p), int(w)), 0.0)

    def active_hour(self, p, hr: int) -> float:
        return self.act_h.get((int(p), int(hr)), 0.0)

    def frontier(self, p) -> int | None:
        hrs = [hr for (q, hr), v in self.act_h.items() if q == int(p) and v > 0]
        return max(hrs) if hrs else None

    def imps(self, p, hrs: list[int], ad_type: str, placement: str | None = None) -> tuple[float, float]:
        hs, imps, rev = set(hrs), 0.0, 0.0
        for (q, hr, t, plc), (i, r) in self.ads_h.items():
            if q == int(p) and t == ad_type and hr in hs and (placement is None or plc == placement):
                imps += i
                rev += r
        return imps, rev

    def imps_by_placement(self, p, hrs: list[int], ad_type: str) -> dict[str, float]:
        hs, out = set(hrs), {}
        for (q, hr, t, plc), (i, _) in self.ads_h.items():
            if q == int(p) and t == ad_type and hr in hs:
                out[plc] = out.get(plc, 0.0) + i
        return out

    def blocks(self, p, w: int, config_only: bool = True) -> dict[str, dict]:
        out = {}
        for (q, ww, reason), (n, players) in self.blk_w.items():
            if q == int(p) and ww == int(w) and (not config_only or reason in self.config_reasons):
                out[reason] = {"n": n, "players": players}
        return out

    def blocks_hour(self, p, hr: int, reason: str) -> float:
        return self.blk_h.get((int(p), int(hr), reason), 0.0)

    def wins(self, p, hrs: list[int]) -> float:
        return sum(self.wins_h.get((int(p), h), 0.0) for h in hrs)

    # --- per-player rates: the window vs the usual ---
    def per_player(self, p, w: int, value_fn) -> float | None:
        """value_fn(hours of window w) / active players in window w; None without players."""
        act = self.active(p, w)
        return value_fn(self.win.hours_of(w)) / act if act > 0 else None

    def usual(self, p, value_fn) -> float | None:
        vals = [self.per_player(p, w, value_fn) for w in self.win.compare]
        vals = [v for v in vals if v is not None]
        return median(vals) if len(vals) >= 3 else None

    def read(self, p, key: str, label: str, value_fn) -> Rate:
        now, base = self.per_player(p, 0, value_fn), self.usual(p, value_fn)
        ratio = (now / base) if (now is not None and base) else None
        return Rate(key, label, now, base, ratio)

    def day_scale(self, p, ad_type: str) -> float | None:
        """How many of a day's impressions one window holds: median over the comparison days of
        (the 24 hours ending where that window ends) / (that window)."""
        vals = []
        for w in self.win.compare:
            e = self.win.end_hr - 24 * w
            day_imps, _ = self.imps(p, list(range(e - 24, e)), ad_type)
            win_imps, _ = self.imps(p, self.win.hours_of(w), ad_type)
            if win_imps > 0 and day_imps > 0:
                vals.append(day_imps / win_imps)
        return median(vals) if len(vals) >= 3 else None

    def ecpm(self, p, ad_type: str) -> float | None:
        imps, rev = self.imps(p, self.win.hours_of(0), ad_type)
        if imps >= 50 and rev > 0:
            return rev * 1000.0 / imps
        imps, rev = 0.0, 0.0
        for w in self.win.compare:
            i, r = self.imps(p, self.win.hours_of(w), ad_type)
            imps, rev = imps + i, rev + r
        return rev * 1000.0 / imps if imps else None

    def thin_last_hour(self, p) -> str | None:
        """The window's last hour against the same hour on the comparison days: incomplete data must
        not be read as a collapse."""
        h = self.win.end_hr - 1
        fr = self.frontier(p)
        if fr is None or fr < h:
            return f"no activity after {hour_dt(fr + 1):%H}:00 UTC" if fr is not None else "no activity rows at all"
        now = self.active_hour(p, h)
        base = [self.active_hour(p, h - 24 * k) for k in self.win.compare]
        base = [b for b in base if b > 0]
        if len(base) >= 3 and now < self.ic.thin_hour_ratio * median(base):
            return (f"hour {hour_dt(h):%H}:00 UTC has {now:,.0f} active players vs {median(base):,.0f} usually "
                    f"({fmt.pct(now / median(base), False)}) — data incomplete, window not read")
        return None

    # --- the daily diagnosis helpers, on window cuts: 'day' = window index (0 = now) ---
    def diag_ctx(self) -> Ctx:
        if self._diag is None:
            def arm(r):
                return str(r.get("arm") if r.get("arm") is not None else "")
            c = {
                "arm_dau": [{"platform": r["platform"], "day": int(r["w"]), "exp_key": r.get("exp_key"), "arm": arm(r),
                             "dau": num(r.get("players"))} for r in self.cuts.get("iw_arm_act", [])],
                "arm_blocked": [dict(r, day=int(r["w"]), arm=arm(r)) for r in self.cuts.get("iw_arm_blocked", [])],
                "arm_ads": [dict(r, day=int(r["w"]), arm=arm(r), rev=0.0) for r in self.cuts.get("iw_arm_ads", [])],
                "arm_play": [dict(r, day=int(r["w"]), arm=arm(r)) for r in self.cuts.get("iw_arm_play", [])],
            }
            al = self.cfg.placement_aliases
            for r in c["arm_ads"]:
                plc = str(r.get("placement") or "")
                r["placement"] = al.get(plc, plc)
            self._diag = Ctx(c, Series(), self.cfg, 0)
        return self._diag


# --- onset: the hour the break began --------------------------------------------------------------

def _onset(ctx: WCtx, p, hourly_rate, broken, lookback_hours: int = 48) -> tuple[int | None, bool]:
    """(hour, before): the first hour of the unbroken run of broken hours that ends at the window's
    end; before = True when the run reaches the start of the lookback (the break began earlier).
    hourly_rate(hr) -> rate or None; broken(rate, usual) -> bool, usual = median of the same hour on
    the comparison days."""
    end = ctx.win.end_hr
    start = end - lookback_hours
    flags = []
    for hr in range(start, end):
        base = [hourly_rate(hr - 24 * k) for k in ctx.win.compare]
        base = [b for b in base if b is not None]
        usual = median(base) if len(base) >= 3 else None
        flags.append(broken(hourly_rate(hr), usual))
    i = len(flags) - 1
    if not flags[i]:
        return None, False
    while i > 0 and flags[i - 1]:
        i -= 1
    return (start + i, i == 0)


def _since_time(brk: Break) -> str:
    """'09:00 UTC Nov 23' / 'before 09:00 UTC Nov 23' / '' when no onset was found."""
    if brk.since_hr is None:
        return ""
    d = hour_dt(brk.since_hr)
    return f"{'before ' if brk.since_before else ''}{d:%H}:00 UTC {d:%b %d}"


def _since_text(brk: Break) -> str:
    t = _since_time(brk)
    return "" if not t else (f"since {t}" if brk.since_before else f"since ~{t}")


# --- rules -----------------------------------------------------------------------------------------

def _rate_blocks(ctx: WCtx, p, reason: str, w: int) -> float | None:
    act = ctx.active(p, w)
    return ctx.blocks(p, w).get(reason, {}).get("n", 0.0) / act if act > 0 else None


def rule_config_blocks(ctx: WCtx, p) -> tuple[Break | None, list[Rate]]:
    """Config-class block reasons at >= blocks_per_player on >= blocked_players_share of active
    players, from ~0 in the comparison windows."""
    ic = ctx.ic
    act = ctx.active(p, 0)
    now = ctx.blocks(p, 0)
    best = None
    for reason, v in now.items():
        per, share = v["n"] / act, v["players"] / act
        base_vals = [_rate_blocks(ctx, p, reason, w) for w in ctx.win.compare]
        base_vals = [b if b is not None else 0.0 for b in base_vals]
        base = median(base_vals) if base_vals else 0.0
        fires = (per >= ic.blocks_per_player and share >= ic.blocked_players_share
                 and base <= ic.blocks_base_max_per_player and base <= 0.1 * per)
        if fires and (best is None or per > best[1]):
            best = (reason, per, share, base, v)
    if best is None:
        all_fn = (lambda w: sum(x["n"] for x in ctx.blocks(p, w).values()) / ctx.active(p, w) if ctx.active(p, w) else 0.0)
        return None, [Rate("blocked_config", "config-class ad blocks/player", all_fn(0),
                           median([all_fn(w) for w in ctx.win.compare] or [0.0]), None)]
    reason, per, share, base, v = best
    rate = Rate("blocked_config", "config-class ad blocks/player", per, base, None, True,
                f" · `{reason}` on {fmt.pct(share, False)} of players")
    brk = Break(p, "config_block", reason, "", tags={"reason": reason})
    brk.evidence.append(f"`show_blocked` {reason}: {fmt.num(v['n'])} in the window = {fmt.num(per)} per active player, "
                        f"hitting {fmt.pct(share, False)} of players ({fmt.num(base)}/player in the same hours on the last {ctx.win.compare_days} days)")
    brk.fix = "Check the remote config for the ad placements / trigger points this build expects; configure or roll back."
    brk.since_hr, brk.since_before = _onset(ctx, p,
                                            lambda hr: (ctx.blocks_hour(p, hr, reason) / ctx.active_hour(p, hr)) if ctx.active_hour(p, hr) >= 20 else None,
                                            lambda r, usual: r is not None and r >= 0.1)
    # attribution: the experiment arm whose players carry the blocks (same contrast rule as the daily)
    dctx = ctx.diag_ctx()
    attr = attribute_blocks_to_arm(dctx, p, 0, reason)
    if attr:
        key, arm, top, share_k, contrast = attr
        adau = dctx.arm_dau(p, key, [0])
        arm_act = adau.get(arm, 0.0)
        per_arm = top["n"] / arm_act if arm_act else None
        arm_share = top["players"] / arm_act if arm_act else None
        brk.evidence.append(
            f"{fmt.pct(share_k, False)} of them sit in `{key}` arm *{_pretty_arm(arm)}* ({fmt.pct(arm_act / (sum(adau.values()) or 1), False)} of that test's players)"
            + (f" — {fmt.num(per_arm)} per arm player, {fmt.pct(arm_share, False)} of the arm's players; the other arm(s) "
               f"{fmt.num(per_arm / contrast) if contrast != float('inf') else '0'}/player" if per_arm else ""))
        brk.tags.update(exp_key=key, arm=arm)
        dead = arm_dead_placement(dctx, p, [0], key, arm, "interstitial", ctx.win.compare, min_ref=ic.placement_min_rate)
        if dead:
            plc, drop, r_arm, r_oth, r_h = dead
            cmp = []
            if r_oth is not None:
                cmp.append(f"other arms {fmt.num(r_oth)}/player")
            if r_h:
                cmp.append(f"this arm {fmt.num(r_h)}/player in the same hours before")
            brk.evidence.append(f"placement `{plc or '(none)'}` in *{_pretty_arm(arm)}*: {fmt.num(r_arm)}/player in the window vs "
                                + ", ".join(cmp) + f" ({fmt.arrow_pct(drop)})")
            brk.tags["placement"] = plc
        wins_arm = dctx.arm_wins(p, key, [0]).get(arm, 0.0)
        if wins_arm and 0.6 <= top["n"] / wins_arm <= 1.5:
            brk.evidence.append(f"≈ {fmt.num(top['n'] / wins_arm, 2)} blocks per level win in that arm — every win asks for the ad and is refused")
        brk.fix = (f"Configure the trigger point(s) for `{key}` *{_pretty_arm(arm)}* in remote config on {ctx.pname(p)} "
                   f"(or pause that arm). Treat {_since_time(brk) or 'the broken hours'} → fix as a dead window in any read of this test.")
    brk.headline = (f"{ctx.pname(p)}: the game refused its own ads — `{reason}` {fmt.num(per)}/player on {fmt.pct(share, False)} of players"
                    + (f", in `{brk.tags['exp_key']}` *{_pretty_arm(brk.tags['arm'])}*" if "exp_key" in brk.tags else "")
                    + (f", {_since_text(brk)}" if brk.since_hr is not None else ""))
    return brk, [rate]


def rule_placement(ctx: WCtx, p) -> tuple[Break | None, list[Rate]]:
    """One interstitial placement at <= placement_collapse_ratio of its usual per-player rate while
    every other meaningful placement holds >= placement_hold_ratio."""
    ic = ctx.ic
    rows = []
    for plc in sorted({k[3] for k in ctx.ads_h if k[0] == int(p) and k[2] == "interstitial"}):
        fn = (lambda hrs, _plc=plc: ctx.imps(p, hrs, "interstitial", _plc)[0])
        r = ctx.read(p, f"placement:{plc}", f"IT `{plc or '(none)'}`/player", fn)
        base_imps = median([fn(ctx.win.hours_of(w)) for w in ctx.win.compare] or [0.0])
        if r.base is not None and r.base >= ic.placement_min_rate and base_imps >= ic.placement_min_imps:
            rows.append(r)
    if not rows:
        return None, []
    rows.sort(key=lambda r: (r.ratio if r.ratio is not None else 9.0))
    worst, others = rows[0], rows[1:]
    if (worst.ratio is not None and worst.ratio <= ic.placement_collapse_ratio
            and all(r.ratio is not None and r.ratio >= ic.placement_hold_ratio for r in others)):
        worst.severe = True
        plc = worst.key.split(":", 1)[1]
        brk = Break(p, "placement", plc, "", tags={"placement": plc})
        brk.evidence.append(f"placement `{plc or '(none)'}`: {fmt.num(worst.base)}/player usually → {fmt.num(worst.now)} in the window ({fmt.arrow_pct(worst.ratio - 1)})")
        if others:
            brk.evidence.append("other placements held: " + ", ".join(f"`{r.key.split(':', 1)[1] or '(none)'}` {fmt.arrow_pct(r.ratio - 1)}" for r in others[:3]))
        brk.since_hr, brk.since_before = _onset(ctx, p,
                                                lambda hr: (ctx.imps(p, [hr], "interstitial", plc)[0] / ctx.active_hour(p, hr)) if ctx.active_hour(p, hr) >= 20 else None,
                                                lambda r, usual: r is not None and usual is not None and usual > 0 and r <= 0.3 * usual)
        _arm_contrast(ctx, p, brk, lambda key, arm, days: ctx.diag_ctx().arm_placement_imps(p, key, arm, "interstitial", days).get(plc, 0.0))
        brk.fix = "Check the client trigger for that placement (build / remote config) — no block reason was logged for it."
        brk.headline = (f"{ctx.pname(p)}: the `{plc}` interstitial placement stopped firing"
                        + (f" in `{brk.tags['exp_key']}` *{_pretty_arm(brk.tags['arm'])}*" if "exp_key" in brk.tags else "")
                        + (f", {_since_text(brk)}" if brk.since_hr is not None else ""))
        return brk, rows
    return None, rows


def rule_volume(ctx: WCtx, p) -> tuple[Break | None, list[Rate]]:
    """IT or RV imps per active player <= volume_collapse_ratio of usual while wins per player hold."""
    ic = ctx.ic
    wins = ctx.read(p, "wins", "level wins/player", lambda hrs: ctx.wins(p, hrs))
    rates, best = [], None
    for t in AD_TYPES:
        fn = (lambda hrs, _t=t: ctx.imps(p, hrs, _t)[0])
        r = ctx.read(p, f"{SHORT[t]}_imps", f"{SHORT[t].upper()} imps/player", fn)
        rates.append(r)
        base_imps = median([fn(ctx.win.hours_of(w)) for w in ctx.win.compare] or [0.0])
        if (r.ratio is not None and r.ratio <= ic.volume_collapse_ratio and base_imps >= ic.volume_min_imps
                and wins.ratio is not None and wins.ratio >= ic.wins_hold_ratio
                and (best is None or r.ratio < best.ratio)):
            best = r
    rates.append(wins)
    if best is None:
        return None, rates
    best.severe = True
    t = "interstitial" if best.key.startswith("it") else "rewarded"
    brk = Break(p, "volume", t, "", tags={"ad_type": t})
    brk.evidence.append(f"{best.label}: {fmt.num(best.base)} usually → {fmt.num(best.now)} in the window ({fmt.arrow_pct(best.ratio - 1)}); "
                        f"level wins/player {fmt.num(wins.base)} → {fmt.num(wins.now)} ({fmt.arrow_pct(wins.ratio - 1)}) — play held, the ads did not")
    by = {r.key.split(":", 1)[1]: r for r in rule_placement(ctx, p)[1]} if t == "interstitial" else {}
    if by:
        brk.evidence.append("by placement: " + ", ".join(f"`{k or '(none)'}` {fmt.arrow_pct(r.ratio - 1) if r.ratio is not None else 'n/a'}" for k, r in list(by.items())[:4]))
    brk.since_hr, brk.since_before = _onset(ctx, p,
                                            lambda hr: (ctx.imps(p, [hr], t)[0] / ctx.active_hour(p, hr)) if ctx.active_hour(p, hr) >= 20 else None,
                                            lambda r, usual: r is not None and usual is not None and usual > 0 and r <= 0.3 * usual)
    _arm_contrast(ctx, p, brk, lambda key, arm, days: sum(ctx.diag_ctx().arm_placement_imps(p, key, arm, t, days).values()))
    brk.fix = (f"No block reason was logged: check the {SHORT[t].upper()} ad units / SDK init on this build and the serving networks "
               f"(fill), then remote config for the {SHORT[t].upper()} placements.")
    brk.headline = (f"{ctx.pname(p)}: {best.label} {fmt.arrow_pct(best.ratio - 1)} with play flat — the ads stopped, the players did not"
                    + (f"; in `{brk.tags['exp_key']}` *{_pretty_arm(brk.tags['arm'])}*" if "exp_key" in brk.tags else "")
                    + (f", {_since_text(brk)}" if brk.since_hr is not None else ""))
    return brk, rates


def _arm_contrast(ctx: WCtx, p, brk: Break, value_fn) -> None:
    """For a break with no block reason: is one experiment arm carrying it? value_fn(key, arm, days)
    -> imps for that arm; the arm at <= 25% of the other arms' per-player rate, holding >= 15% of the
    key's players, names the test. The strongest contrast wins; a key with one arm >= 95% is skipped."""
    dctx = ctx.diag_ctx()
    keys = sorted({r.get("exp_key") for r in ctx.cuts.get("iw_arm_act", []) if int(r["platform"]) == int(p) and int(r["w"]) == 0})
    best = None
    for key in keys:
        dau = dctx.arm_dau(p, key, [0])
        tot = sum(dau.values())
        if not tot or len(dau) < 2 or max(dau.values()) / tot >= 0.95:
            continue
        rates = {a: value_fn(key, a, [0]) / dau[a] for a in dau if dau[a] >= 50}
        if len(rates) < 2:
            continue
        low = min(rates, key=lambda a: rates[a])
        rest_imps = sum(value_fn(key, a, [0]) for a in rates if a != low)
        rest_dau = sum(dau[a] for a in rates if a != low)
        r_rest = rest_imps / rest_dau if rest_dau else 0.0
        if r_rest > 0 and rates[low] <= 0.25 * r_rest and dau[low] / tot >= 0.15:
            contrast = r_rest / rates[low] if rates[low] else float("inf")
            if best is None or contrast > best[0]:
                best = (contrast, key, low, rates[low], r_rest, dau[low] / tot)
    if best:
        _, key, arm, r_low, r_rest, share = best
        brk.evidence.append(f"in `{key}` arm *{_pretty_arm(arm)}* ({fmt.pct(share, False)} of that test's players): "
                            f"{fmt.num(r_low)}/player vs the other arm(s) {fmt.num(r_rest)}/player")
        brk.tags.update(exp_key=key, arm=arm)


# --- putting a platform together -----------------------------------------------------------------

def _impact(ctx: WCtx, p, ad_type: str, lost_per_player: float) -> float | None:
    """-$ per day at this rate: lost imps per active player x the window's active players, scaled to
    a day by the window's usual share of the day's impressions, at the current eCPM."""
    if lost_per_player <= 0:
        return None
    scale, ecpm = ctx.day_scale(p, ad_type), ctx.ecpm(p, ad_type)
    if not scale or not ecpm:
        return None
    return -(lost_per_player * ctx.active(p, 0) * scale * ecpm / 1000.0)


def detect_breaks(ctx: WCtx) -> tuple[list[Break], dict[int, str]]:
    """(breaks, {platform: why it was not read}). At most one break per platform: a config break
    outranks a dead placement outranks a volume collapse; the other rules' readings become its
    'What moved' lines."""
    ic = ctx.ic
    breaks: list[Break] = []
    skipped: dict[int, str] = {}
    for p in ctx.platforms:
        act = ctx.active(p, 0)
        if act < ic.min_active_players:
            skipped[p] = f"only {act:,.0f} active players in the window"
            continue
        thin = ctx.thin_last_hour(p)
        if thin:
            skipped[p] = thin
            continue
        a, ra = rule_config_blocks(ctx, p)
        b, rb = rule_placement(ctx, p)
        c, rc = rule_volume(ctx, p)
        brk = a or b or c
        if brk is None:
            continue
        it = next((r for r in rc if r.key == "it_imps"), None)
        rv = next((r for r in rc if r.key == "rv_imps"), None)
        wins = next((r for r in rc if r.key == "wins"), None)
        # the lines: the tripping metric first, then the money metrics, then the rest
        lines = []
        for r in ra + [it, rv] + [x for x in rb if x.severe] + [wins]:
            if r is not None and r.now is not None and r not in lines:
                lines.append(r)
        if brk.kind == "config_block" and b is not None:
            brk.evidence.append(b.evidence[0])
        if brk.kind != "placement" and rb and not any(x.severe for x in rb):
            brk.ruled_out.append("by placement: " + ", ".join(f"`{r.key.split(':', 1)[1] or '(none)'}` {fmt.arrow_pct(r.ratio - 1) if r.ratio is not None else 'n/a'}" for r in rb[:4]))
        if brk.kind != "volume" and wins is not None and wins.ratio is not None:
            brk.ruled_out.append(f"play {'flat' if wins.ratio >= 0.9 else fmt.arrow_pct(wins.ratio - 1)} (wins/player {fmt.arrow_pct(wins.ratio - 1)})")
        if brk.kind != "config_block" and ra:
            brk.ruled_out.append(f"no new config-class block reason (all reasons {fmt.num(ra[0].now)}/player)")
        brk.rates = lines
        # money: valued on the ad type the break sits in
        if brk.kind == "config_block" or (brk.kind == "placement"):
            lost = (it.base - it.now) if (it and it.base is not None and it.now is not None) else 0.0
            brk.impact_usd_per_day = _impact(ctx, p, "interstitial", lost)
        else:
            t = brk.ident
            r = it if t == "interstitial" else rv
            lost = (r.base - r.now) if (r and r.base is not None and r.now is not None) else 0.0
            brk.impact_usd_per_day = _impact(ctx, p, t, lost)
        breaks.append(brk)
    return breaks, skipped


# --- cooldown --------------------------------------------------------------------------------------

def under_cooldown(state: State, sig: str, at: datetime, hours: float) -> datetime | None:
    """The last post time if it is within `hours` of `at`, else None."""
    ip = state.intraday.get(sig)
    if not ip or not ip.last_posted_at:
        return None
    last = datetime.fromisoformat(ip.last_posted_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last if (at - last) < timedelta(hours=hours) else None


def record(state: State, sig: str, at: datetime, posted: bool, headline: str) -> IntradayPost:
    iso = at.isoformat(timespec="minutes")
    ip = state.intraday.get(sig)
    if ip is None:
        ip = state.intraday[sig] = IntradayPost(sig, iso, iso, None, 0, headline)
    ip.last_at = iso
    ip.headline = headline or ip.headline
    if posted:
        ip.last_posted_at = iso
        ip.posted_count += 1
    return ip


def prune(state: State, at: datetime, keep_days: int = 14) -> None:
    for sig, ip in list(state.intraday.items()):
        last = datetime.fromisoformat(ip.last_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if at - last > timedelta(days=keep_days):
            del state.intraday[sig]


# --- the run ---------------------------------------------------------------------------------------

@dataclass
class IntradayResult:
    game: str
    at: datetime
    window: Window | None = None
    breaks: list = field(default_factory=list)
    posted: list = field(default_factory=list)
    suppressed: list = field(default_factory=list)      # signatures under cooldown
    skipped: dict = field(default_factory=dict)
    payloads: list = field(default_factory=list)
    slack_status: list = field(default_factory=list)
    error: str | None = None

    def summary(self) -> dict:
        return {
            "game": self.game, "at": self.at.isoformat(), "window": self.window.label() if self.window else None,
            "breaks": [{"platform": b.platform, "kind": b.kind, "ident": b.ident, "headline": b.headline, "tags": b.tags,
                        "impact_usd_per_day": b.impact_usd_per_day,
                        "since": hour_dt(b.since_hr).isoformat() if b.since_hr is not None else None,
                        "rates": [{"key": r.key, "now": r.now, "usual": r.base, "ratio": r.ratio, "severe": r.severe} for r in b.rates]}
                       for b in self.breaks],
            "posted": len(self.posted), "suppressed": self.suppressed, "skipped": self.skipped,
            "slack": self.slack_status, "error": self.error,
        }


def run_intraday(cfg: GameCfg, at: datetime | None = None, dry_run: bool = False, from_cache: Path | None = None,
                 use_state: bool = True, cache_dir: Path | None = None, log=print) -> IntradayResult:
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    res = IntradayResult(cfg.key, at)
    ic = cfg.intraday
    try:
        end_hr = hour_num(at) - ic.lag_hours
        win = Window(end_hr, ic.window_hours, ic.compare_days)
        res.window = win
        if from_cache:
            cuts = pull.load_cache(from_cache)
            log(f"[monitor] {cfg.key} intraday: loaded {len(cuts)} cuts from {from_cache}")
        else:
            cuts = pull.execute_sql(pull.render_intraday(cfg, end_hr), log=log)
            folder = cache_dir or (mcfg.data_dir() / cfg.key / "intraday" / hour_dt(end_hr).strftime("%Y-%m-%dT%H"))
            pull.save_cache(cuts, folder)
            log(f"[monitor] {cfg.key} intraday: cached to {folder}")
        ctx = WCtx(cuts, cfg, win)
        if not ctx.platforms:
            raise RuntimeError(f"no active players in the window {win.label()} — the pull returned nothing")
        res.breaks, res.skipped = detect_breaks(ctx)
        for p, why in res.skipped.items():
            log(f"[monitor] {cfg.key} intraday: {cfg.platform_name(p)} not read — {why}")

        state_path = mcfg.data_dir() / f"state_{cfg.key}.json"
        state = State.load(state_path) if use_state else State()
        footer = {
            "active": {p: ctx.active(p, 0) for p in ctx.platforms},
            "frontier": (f"{hour_dt(max(f for f in (ctx.frontier(p) for p in ctx.platforms) if f is not None)):%H}:xx UTC"
                         if any(ctx.frontier(p) is not None for p in ctx.platforms) else None),
        }
        for brk in res.breaks:
            sig = brk.signature(cfg)
            last = under_cooldown(state, sig, at, ic.cooldown_hours)
            if last is not None:
                res.suppressed.append(sig)
                record(state, sig, at, posted=False, headline=brk.headline)
                log(f"[monitor] {cfg.key} intraday: {sig} still open, posted {last:%b %d %H:%M} UTC — under the {ic.cooldown_hours:g} h cooldown")
                continue
            payload = report.intraday_message(cfg, win, brk, footer)
            res.posted.append(brk)
            res.payloads.append(payload)
            status = "DRY_RUN" if dry_run else slack.post(cfg.webhook_url(), payload)
            res.slack_status.append(status)
            record(state, sig, at, posted=status.startswith("SLACK_POSTED"), headline=brk.headline)
        prune(state, at)
        state.last_intraday_at = at.isoformat(timespec="minutes")
        if use_state and not dry_run:
            state.save(state_path)
        return res
    except Exception as e:  # the scheduler must never die on a monitor error
        res.error = f"{type(e).__name__}: {e}"
        log("[monitor] intraday ERROR " + res.error)
        log(traceback.format_exc())
        return res


def run_all_intraday(at: datetime | None = None, dry_run: bool = False, log=print) -> list[IntradayResult]:
    """Every enabled game that passed the most recent pre-gate (gates.json, written by the morning
    run). With no pre-gate on record every enabled game is read."""
    from app.monitor.run import load_gates
    gates = load_gates() or {}
    out = []
    for key, cfg in mcfg.load().items():
        if not cfg.enabled or not cfg.intraday.enabled:
            continue
        g = gates.get(key)
        if g is not None and not (g.get("revenue_ok") and g.get("ua_ok")):
            log(f"[monitor] intraday {key}: gated out (pre-gate of {g.get('day', 'the last run')})")
            continue
        out.append(run_intraday(cfg, at, dry_run=dry_run, log=log))
    from app.monitor.run import notify_failures
    from datetime import datetime as _dt, timezone as _tz
    notify_failures(mcfg.load(), (at or _dt.now(_tz.utc)).date(), out, dry_run=dry_run, log=log, label="evening check")
    return out
