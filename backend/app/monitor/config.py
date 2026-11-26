"""Load games.toml into typed objects. `defaults` applies to every game; a game table may override
any default key (gates, severity, metric thresholds) by repeating it under [games.<key>]."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).parent


@dataclass
class MetricSpec:
    key: str
    label: str
    unit: str                 # usd | count | rate | share
    direction: str            # down_is_bad | up_is_bad
    min_rel: float
    family: str
    min_abs: float | None = None
    ad_type: str | None = None
    lever: str | None = None  # volume | price (ads family)
    lag_days: int = 0         # rolling_d1 is read one day late: yesterday is its return day
    read_latest: bool = False # read the latest day <= the target that has a value (Adjust cost lags a day)

    @property
    def down_is_bad(self) -> bool:
        return self.direction == "down_is_bad"


@dataclass
class Gates:
    min_ad_revenue_per_day_usd: float = 500.0
    min_ua_spend_per_day_usd: float = 100.0
    min_paid_installs_per_day: float = 50.0


@dataclass
class SeverityCfg:
    sev1_usd_per_day: float = 500.0
    sev1_share_of_platform_revenue: float = 0.15
    sev2_usd_per_day: float = 150.0
    sev2_share_of_platform_revenue: float = 0.07
    hard_break_blocks_per_dau: float = 0.5
    hard_break_placement_drop: float = 0.80


@dataclass
class IntradayCfg:
    """The intraday hard-break check (intraday.py). Rates are per active player in the window
    (distinct players with a session/dau event in those hours); 'usual' = the median of the same
    hours on each of the previous `compare_days` days."""
    enabled: bool = True
    window_hours: int = 6            # the last N complete UTC hours
    compare_days: int = 7            # vs the same hours on each of the previous N days (median)
    history_days: int = 8            # hours pulled per run (>= compare_days + 1)
    arm_days: int = 14               # experiment assignment rows looked back for the per-arm cuts
    lag_hours: int = 0               # hours held back for ingestion (measured ~0 on game 4)
    cooldown_hours: float = 12.0     # the same signature posts at most once per this many hours
    min_active_players: int = 200    # below this the platform is not read
    thin_hour_ratio: float = 0.4     # the window's last hour has < this x its usual players => incomplete
    # config-class block reasons: >= this many per active player, on >= this share of active players,
    # from ~0 (<= base_max per player, and <= a tenth of now) in the comparison windows
    blocks_per_player: float = 0.5
    blocked_players_share: float = 0.10
    blocks_base_max_per_player: float = 0.05
    # one interstitial placement at <= collapse x its usual per-player rate while every other
    # meaningful placement (usual >= min_rate per player, >= min_imps per window) holds >= hold x
    placement_collapse_ratio: float = 0.20
    placement_hold_ratio: float = 0.65
    placement_min_rate: float = 0.3
    placement_min_imps: int = 100
    # IT or RV imps per active player <= collapse x usual while level wins per player hold >= hold x
    volume_collapse_ratio: float = 0.60
    wins_hold_ratio: float = 0.85
    volume_min_imps: int = 300       # usual imps per window below which the type is not read


@dataclass
class GameCfg:
    key: str
    name: str
    game_id: int
    enabled: bool
    adjust_app: str
    platforms: dict[int, str]
    market_control_games: list[int]
    slack_webhook_env: str
    dashboard_url: str
    config_block_reasons: list[str]
    placement_aliases: dict[str, str]
    ad_type_map: dict[str, str]
    data_lookback_days: int
    # thresholds (defaults merged with the game's overrides)
    window_days: int
    baseline_days: int
    mad_days: int
    z_threshold: float
    persist_sigma: float
    exclude_countries: list[str]
    post_min_severity: int
    sev3_persist_days: int
    sev1_repost_daily: bool
    sev2_repost_every_days: int
    post_recovery_for_sev1: bool
    weekly_summary: bool
    slack_mention_sev1: str
    slack_mention_sev2: str
    gates: Gates
    severity: SeverityCfg
    metrics: dict[str, MetricSpec] = field(default_factory=dict)
    intraday: IntradayCfg = field(default_factory=IntradayCfg)

    @property
    def ua_source(self) -> str:
        """'adjust' when the game has an Adjust app (spend, installs, CPI); 'attribution' otherwise
        (paid installs from attribution.events only — no spend)."""
        return "adjust" if self.adjust_app else "attribution"

    def ad_type_case_sql(self) -> str:
        """The SQL expression that maps a raw ad-type label to the canonical one."""
        raw = "lower(split_part(p, '^', 1))"
        whens = " ".join("when '%s' then '%s'" % (k.replace("'", "''"), v) for k, v in self.ad_type_map.items() if k != v)
        return f"(case {raw} {whens} else {raw} end)" if whens else raw

    def platform_name(self, platform: int | str) -> str:
        try:
            return self.platforms[int(platform)]
        except (KeyError, ValueError, TypeError):
            return str(platform)

    def webhook_url(self) -> str:
        # Deliberately no fallback to SLACK_WEBHOOK_URL (the experiment-read channel): an alert
        # must never land in a channel it was not configured for.
        return os.environ.get(self.slack_webhook_env, "")


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | os.PathLike | None = None) -> dict[str, GameCfg]:
    p = Path(path) if path else _HERE / "games.toml"
    with open(p, "rb") as fh:
        raw = tomllib.load(fh)
    defaults: dict[str, Any] = raw.get("defaults", {})
    games: dict[str, GameCfg] = {}
    for key, g in raw.get("games", {}).items():
        merged = _merge(defaults, {k: v for k, v in g.items() if k in defaults})
        metrics = {
            mk: MetricSpec(key=mk, **{k: v for k, v in mv.items()})
            for mk, mv in merged.get("metrics", {}).items()
        }
        games[key] = GameCfg(
            key=key,
            name=g.get("name", key),
            game_id=int(g["game_id"]),
            enabled=bool(g.get("enabled", True)),
            adjust_app=g.get("adjust_app", ""),
            platforms={int(k): v for k, v in g.get("platforms", {1: "Android", 2: "iOS"}).items()},
            market_control_games=[int(x) for x in g.get("market_control_games", [])],
            slack_webhook_env=g.get("slack_webhook_env", "SLACK_MONITOR_WEBHOOK_URL"),
            dashboard_url=g.get("dashboard_url", ""),
            config_block_reasons=list(g.get("block_reasons", {}).get("config_class")
                                      or defaults.get("block_reasons", {}).get("config_class", [])),
            placement_aliases=dict(g.get("placement_aliases", {})),
            ad_type_map={**{str(k).lower(): v for k, v in defaults.get("ad_type_map", {}).items()},
                         **{str(k).lower(): v for k, v in g.get("ad_type_map", {}).items()}},
            data_lookback_days=int(merged.get("data_lookback_days", 120)),
            window_days=int(merged.get("window_days", 35)),
            baseline_days=int(merged.get("baseline_days", 7)),
            mad_days=int(merged.get("mad_days", 28)),
            z_threshold=float(merged.get("z_threshold", 3.0)),
            persist_sigma=float(merged.get("persist_sigma", 2.0)),
            exclude_countries=list(merged.get("exclude_countries", ["IN"])),
            post_min_severity=int(merged.get("post_min_severity", 2)),
            sev3_persist_days=int(merged.get("sev3_persist_days", 3)),
            sev1_repost_daily=bool(merged.get("sev1_repost_daily", True)),
            sev2_repost_every_days=int(merged.get("sev2_repost_every_days", 3)),
            post_recovery_for_sev1=bool(merged.get("post_recovery_for_sev1", True)),
            weekly_summary=bool(merged.get("weekly_summary", False)),
            slack_mention_sev1=str(merged.get("slack_mention_sev1", "")),
            slack_mention_sev2=str(merged.get("slack_mention_sev2", "")),
            gates=Gates(**merged.get("gates", {})),
            severity=SeverityCfg(**merged.get("severity", {})),
            metrics=metrics,
            intraday=IntradayCfg(**merged.get("intraday", {})),
        )
    # market control = every other enabled game unless the game names its own
    enabled_ids = [c.game_id for c in games.values() if c.enabled]
    for c in games.values():
        if not c.market_control_games:
            c.market_control_games = [i for i in enabled_ids if i != c.game_id]
    return games


def data_dir() -> Path:
    """Where runs cache their pulls and the incident state lives. /data is the compose volume
    shared by backend and worker; override with MONITOR_DATA_DIR."""
    return Path(os.environ.get("MONITOR_DATA_DIR", "/data/monitor"))
