"""Robust change detection on a daily series.

baseline  = median of the `baseline_days` days before the day read (the day itself excluded),
            skipping "dead" days (series.dead_days: the platform was running broken) and reaching one
            day further back for each one skipped
sigma     = 1.4826 x MAD of the residuals (value - trailing median) over the trailing `mad_days`,
            floored at 2% of the baseline so a perfectly flat series cannot alarm on rounding
z         = (value - baseline) / sigma
A finding needs |z| >= z_threshold AND |rel| >= the metric's min_rel in the metric's bad direction.
persist_days counts consecutive days (ending on the read day) beyond `persist_sigma` the same way.
DoD and WoW are reported alongside; the 7-day mean vs the previous 7-day mean is the trend."""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, median

from app.monitor.config import GameCfg, MetricSpec
from app.monitor.series import Series


@dataclass
class Reading:
    metric: str
    platform: object
    day: int
    value: float | None
    baseline: float | None
    sigma: float | None
    z: float | None
    rel_base: float | None
    rel_dod: float | None
    rel_wow: float | None
    trend_rel: float | None      # mean(last 7) vs mean(prior 7) - 1
    persist_days: int
    flagged: bool
    bad_direction: str           # "down" | "up" (which way this metric is wrong)
    week_mean: float | None = None
    prior_week_mean: float | None = None
    skipped: tuple = ()          # dead days the baseline stepped over


def window_days(series: dict[int, float], end_excl: int, n: int, skip=frozenset()) -> tuple[list[int], list[int]]:
    """(days used, days skipped): the `n` days before end_excl that have a value, stepping over days
    in `skip` and reaching one day further back for each. With no skip this is exactly the n-day
    window [end_excl - n, end_excl)."""
    used, skipped = [], []
    t, floor = end_excl - 1, end_excl - n
    while t >= floor:
        if t in skip:
            skipped.append(t)
            floor -= 1
        elif t in series:
            used.append(t)
        t -= 1
    return used, skipped


def _median_window(series: dict[int, float], end_excl: int, n: int, skip=frozenset()) -> float | None:
    used, _ = window_days(series, end_excl, n, skip)
    xs = [series[d] for d in used]
    return median(xs) if len(xs) >= max(3, n // 2) else None


def robust_sigma(series: dict[int, float], day: int, cfg: GameCfg, skip=frozenset()) -> float | None:
    """MAD of residuals (value - trailing median) over the mad_days ending the day BEFORE `day`."""
    res = []
    for t in range(day - cfg.mad_days, day):
        if t not in series or t in skip:
            continue
        m = _median_window(series, t, cfg.baseline_days, skip)
        if m is None:
            continue
        res.append(series[t] - m)
    if len(res) < 7:
        return None
    c = median(res)
    mad = median(abs(r - c) for r in res)
    return 1.4826 * mad


def read_metric(series: dict[int, float], spec: MetricSpec, platform, day: int, cfg: GameCfg,
                skip=frozenset()) -> Reading | None:
    bad = "down" if spec.down_is_bad else "up"
    if spec.read_latest and day not in series:
        # Adjust writes a day's cost the day after: read the latest filled day instead
        avail = [d for d in series if d <= day and d >= day - 3]
        if not avail:
            return None
        day = max(avail)
    v = series.get(day)
    base = _median_window(series, day, cfg.baseline_days, skip)
    if v is None or base is None:
        return None
    skipped = tuple(sorted(window_days(series, day, cfg.baseline_days, skip)[1]))
    sigma = robust_sigma(series, day, cfg, skip)
    floor = 0.02 * abs(base) if base else None
    if sigma is None or (floor is not None and sigma < floor):
        sigma = floor if floor else sigma
    z = (v - base) / sigma if sigma else None
    rel_base = (v - base) / abs(base) if base else None
    dod = series.get(day - 1)
    wow = series.get(day - 7)
    rel_dod = (v - dod) / abs(dod) if dod else None
    rel_wow = (v - wow) / abs(wow) if wow else None
    wk = [series[d] for d in range(day - 6, day + 1) if d in series]
    pwk = [series[d] for d in range(day - 13, day - 6) if d in series]
    wm = mean(wk) if len(wk) >= 4 else None
    pwm = mean(pwk) if len(pwk) >= 4 else None
    trend = (wm - pwm) / abs(pwm) if (wm is not None and pwm) else None

    def is_bad(zv: float | None, rv: float | None, val: float | None, zthr: float, relthr: float, ref: float | None) -> bool:
        # a rise from a zero baseline (blocks that never happened before) has no relative size:
        # it is wrong as soon as it reaches the metric's absolute floor
        if bad == "up" and ref is not None and ref == 0 and val is not None:
            return val > 0 and val >= (spec.min_abs or 0.0)
        if zv is None or rv is None:
            return False
        if bad == "down":
            return zv <= -zthr and rv <= -relthr
        ok = zv >= zthr and rv >= relthr
        if spec.min_abs is not None and val is not None:
            ok = ok and val >= spec.min_abs
        return ok

    flagged = is_bad(z, rel_base, v, cfg.z_threshold, spec.min_rel, base)

    # persistence: walk back while each day is beyond persist_sigma vs ITS OWN trailing median
    persist = 0
    t = day
    read_dead = day in skip
    while t in series:
        if (t in skip) != read_dead:
            break   # a healthy-day move is not the same move as the broken days before it
        m = _median_window(series, t, cfg.baseline_days, skip)
        if m is None or not sigma:
            break
        zt = (series[t] - m) / sigma
        rt = (series[t] - m) / abs(m) if m else None
        if is_bad(zt, rt, series[t], cfg.persist_sigma, spec.min_rel / 2, m):
            persist += 1
            t -= 1
        else:
            break
    return Reading(spec.key, platform, day, v, base, sigma, z, rel_base, rel_dod, rel_wow, trend,
                   persist, flagged, bad, wm, pwm, skipped)


def read_all(s: Series, cfg: GameCfg, day: int, platforms: list | None = None) -> list[Reading]:
    out: list[Reading] = []
    for p in (platforms or s.platforms):
        for spec in cfg.metrics.values():
            d = day - spec.lag_days
            r = read_metric(s.series(spec.key, p), spec, p, d, cfg, s.dead_days(p))
            if r is not None:
                out.append(r)
    return out
