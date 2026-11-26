"""Incident state across runs — what is open, when it was last posted — so a broken metric is not
re-announced every morning unless the rules say so. One JSON file per game under data_dir().
The intraday check (intraday.py) keeps its cooldowns in the same file under `intraday`."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Incident:
    signature: str          # game:platform:metric:direction
    first_day: str          # ISO date the move was first seen
    last_day: str           # ISO date it was last seen open
    last_severity: int      # 1..3
    last_posted_day: str | None = None
    posted_count: int = 0
    peak_severity: int = 3
    cause: str = ""


@dataclass
class IntradayPost:
    signature: str          # game:platform:intraday:<kind>:<ident>
    first_at: str           # ISO UTC datetime of the run that first saw it
    last_at: str            # ISO UTC datetime of the last run that saw it
    last_posted_at: str | None = None   # when Slack last got it (the cooldown anchor)
    posted_count: int = 0
    headline: str = ""


@dataclass
class State:
    incidents: dict[str, Incident] = field(default_factory=dict)
    intraday: dict[str, IntradayPost] = field(default_factory=dict)
    last_run_day: str | None = None
    last_intraday_at: str | None = None

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text() or "{}")
        inc = {k: Incident(**v) for k, v in raw.get("incidents", {}).items()}
        intra = {k: IntradayPost(**v) for k, v in raw.get("intraday", {}).items()}
        return cls(incidents=inc, intraday=intra, last_run_day=raw.get("last_run_day"),
                   last_intraday_at=raw.get("last_intraday_at"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "incidents": {k: asdict(v) for k, v in self.incidents.items()},
            "intraday": {k: asdict(v) for k, v in self.intraday.items()},
            "last_run_day": self.last_run_day,
            "last_intraday_at": self.last_intraday_at,
        }, indent=2, sort_keys=True))
