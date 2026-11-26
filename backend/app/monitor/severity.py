"""How much does the move cost, and how loudly should it be said.

$ impact per day = what yesterday would have earned had the metric stayed at its baseline, at
yesterday's volume (so a volume move is valued at yesterday's price and a price move at yesterday's
volume). Tiers (MONITOR.md §3):
  SEV1  act now     hard break (config/placement/pipeline) OR impact >= $500/day OR >= 15% of the
                    platform's daily ad revenue
  SEV2  look today  impact >= $150/day or >= 7%; OR any unusual move with a game-side cause
                    (config, experiment, build) attached
  SEV3  watch       everything else that passed the detector"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from app.monitor.config import GameCfg, MetricSpec
from app.monitor.detect import Reading
from app.monitor.diagnose import Ctx, Diagnosis
from app.monitor.series import SHORT

GAME_SIDE = {"config_break", "placement", "experiment_change", "build_rollout"}
LABEL = {1: "act now", 2: "look today", 3: "watch"}
EMOJI = {1: "🔴", 2: "🟠", 3: "🟡"}


@dataclass
class Scored:
    reading: Reading
    spec: MetricSpec
    diagnosis: Diagnosis
    impact_usd: float | None     # per day, negative = lost
    severity: int                # 1, 2, 3
    platform_revenue: float      # yesterday's ad revenue on that platform (for the share)


def _base(ctx: Ctx, key: str, p, day: int) -> float | None:
    vals = [ctx.s.get(key, p, d) for d in ctx.base_days(day)]
    vals = [v for v in vals if v is not None]
    return median(vals) if vals else None


def impact(ctx: Ctx, r: Reading, spec: MetricSpec) -> float | None:
    """Negative $ per day. None when the metric has no money attached (fill, rolling D1 ...)."""
    p, day = r.platform, r.day
    if r.value is None or r.baseline is None:
        return None
    dau = ctx.dau(p, day) or 0.0
    if spec.key == "ad_revenue":
        return r.value - r.baseline
    if spec.key == "arpdau":
        return (r.value - r.baseline) * dau
    if spec.key == "dau":
        arpdau = _base(ctx, "arpdau", p, day) or 0.0
        return (r.value - r.baseline) * arpdau
    if spec.key == "installs":
        # a lost install is worth roughly its first-week revenue; use ARPDAU x 3 active days as a floor
        arpdau = _base(ctx, "arpdau", p, day) or 0.0
        return (r.value - r.baseline) * arpdau * 3
    if spec.ad_type and spec.lever == "volume":
        ecpm = ctx.s.get(f"{SHORT[spec.ad_type]}_ecpm", p, day) or _base(ctx, f"{SHORT[spec.ad_type]}_ecpm", p, day) or 0.0
        return (r.value - r.baseline) * dau * ecpm / 1000.0
    if spec.ad_type and spec.lever == "price":
        imps = ctx.s.get(f"{SHORT[spec.ad_type]}_imps", p, day) or 0.0
        return (r.value - r.baseline) / 1000.0 * imps
    if spec.key == "blocked_config_per_dau":
        # blocks that would have shown: ~65% of asks become billed interstitials on Arrows
        ecpm = ctx.s.get("it_ecpm", p, day) or _base(ctx, "it_ecpm", p, day) or 0.0
        return -(r.value - r.baseline) * dau * 0.65 * ecpm / 1000.0
    if spec.key in ("wins_per_dau", "starts_per_dau"):
        # play drives interstitials: value the lost wins at the IT revenue per win
        it_rev = ctx.s.get("it_rev", p, day) or 0.0
        wins = (ctx.s.get("wins_per_dau", p, day) or 0.0) * dau
        per_win = it_rev / wins if wins else 0.0
        return (r.value - r.baseline) * dau * per_win
    return None


def score(ctx: Ctx, r: Reading, spec: MetricSpec, d: Diagnosis) -> Scored:
    p, day = r.platform, r.day
    plat_rev = ctx.s.get("ad_revenue", p, day) or 0.0
    usd = impact(ctx, r, spec)
    cfg = ctx.cfg.severity
    sev = 3
    loss = -usd if (usd is not None and usd < 0) else 0.0
    share = loss / plat_rev if plat_rev else 0.0
    if d.hard_break or d.cause == "pipeline":
        sev = 1 if d.cause != "pipeline" else 2
    elif loss >= cfg.sev1_usd_per_day or share >= cfg.sev1_share_of_platform_revenue:
        sev = 1
    elif loss >= cfg.sev2_usd_per_day or share >= cfg.sev2_share_of_platform_revenue:
        sev = 2
    elif d.cause in GAME_SIDE:
        sev = 2
    if d.cause == "ua_cut":
        # a spend cut is a decision, not a fault: worth a "look today", never an "act now"
        sev = max(sev, 2)
    return Scored(r, spec, d, usd, sev, plat_rev)
