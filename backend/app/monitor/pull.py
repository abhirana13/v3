"""Render sql/daily.sql for a game and a window, run it in ONE Redshift session, hand back the
'-- out:' results as {name: [row dict, ...]}. Also writes/reads a CSV cache per run so a day can be
replayed (`--from-cache`) without touching the warehouse.

Read-only guard (same as the rsm.py runner): a statement may only be CREATE TEMP TABLE ... AS
SELECT, SELECT / WITH, or DROP TABLE t_*. Anything else aborts before the session opens."""
from __future__ import annotations

import csv
import re
import sys
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.monitor.config import GameCfg

_SQL_DIR = Path(__file__).parent / "sql"
_T1 = None  # placeholder: the T1 list lives in the SQL itself

EPOCH = date(1970, 1, 1)


def day_num(d: date) -> int:
    return (d - EPOCH).days


def day_date(n: int) -> date:
    return EPOCH + timedelta(days=int(n))


def render(cfg: GameCfg, end_day: date, template: str = "daily.sql") -> str:
    start_day = end_day - timedelta(days=cfg.window_days - 1)
    act_start = end_day - timedelta(days=cfg.data_lookback_days)   # install-day lookback (age bands)
    arm_start = end_day - timedelta(days=13)    # per-arm cuts: the last 14 days
    params = {
        "GAME_ID": str(cfg.game_id),
        "AD_TYPE_EXPR": cfg.ad_type_case_sql(),
        "ACT_START_MS": str(day_num(act_start) * 86400000),
        "ACT_START_S": str(day_num(act_start) * 86400),
        "START_MS": str(day_num(start_day) * 86400000),
        "START_S": str(day_num(start_day) * 86400),
        # END_MS covers END_DAY + 1 (today) so the hour cut can see the frontier; every other
        # statement filters on day <= END_DAY explicitly.
        "END_MS": str((day_num(end_day) + 2) * 86400000 - 1),
        "END_S": str((day_num(end_day) + 2) * 86400 - 1),
        "START_DAY": str(day_num(start_day)),
        "END_DAY": str(day_num(end_day)),
        "ARM_START_DAY": str(day_num(arm_start)),
        "ARM_START_MS": str(day_num(arm_start) * 86400000),
        "ARM_START_S": str(day_num(arm_start) * 86400),
        "START_DATE": start_day.isoformat(),
        "END_DATE": end_day.isoformat(),
        "ADJUST_APP": cfg.adjust_app.replace("'", "''"),
        "EXCLUDE_COUNTRIES": ", ".join("'%s'" % c.replace("'", "''") for c in cfg.exclude_countries) or "''",
        "MARKET_GAMES": ", ".join(str(int(g)) for g in cfg.market_control_games) or "-1",
    }
    return _fill(template, params)


def _fill(template: str, params: dict[str, str]) -> str:
    sql = (_SQL_DIR / template).read_text()
    for k, v in params.items():
        sql = sql.replace("{{%s}}" % k, v)
    missing = re.findall(r"\{\{(\w+)\}\}", sql)
    if missing:
        raise ValueError("unfilled placeholders: %s" % sorted(set(missing)))
    return sql


def render_intraday(cfg: GameCfg, end_hr: int, template: str = "intraday.sql") -> str:
    """sql/intraday.sql for the window ending at epoch hour `end_hr` (exclusive): the last
    cfg.intraday.history_days days by platform x hour, plus the window cuts (see intraday.py)."""
    ic = cfg.intraday
    start_hr = end_hr - 24 * ic.history_days
    arm_start_hr = end_hr - 24 * ic.arm_days
    params = {
        "GAME_ID": str(cfg.game_id),
        "AD_TYPE_EXPR": cfg.ad_type_case_sql(),
        "START_MS": str(start_hr * 3600000),
        "START_S": str(start_hr * 3600),
        # one extra hour past END_HR so the frontier (the partial current hour) is visible in ih_*;
        # the window cuts filter on hr < END_HR themselves.
        "END_MS": str((end_hr + 1) * 3600000 - 1),
        "END_S": str((end_hr + 1) * 3600 - 1),
        "START_HR": str(start_hr),
        "END_HR": str(end_hr),
        "WIN_HOURS": str(ic.window_hours),
        "ARM_START_MS": str(arm_start_hr * 3600000),
        "ARM_START_S": str(arm_start_hr * 3600),
        "EXCLUDE_COUNTRIES": ", ".join("'%s'" % c.replace("'", "''") for c in cfg.exclude_countries) or "''",
    }
    return _fill(template, params)


def render_pregate(games: list[GameCfg], end_day: date, days: int = 8, template: str = "pregate.sql") -> str:
    """sql/pregate.sql over every configured game at once: revenue per game-day, Adjust cost per
    app-day, attributed paid installs per game-day, for the `days` days ending `end_day`. ~1 min."""
    start_day = end_day - timedelta(days=days - 1)
    any_cfg = games[0]
    ids = sorted({g.game_id for g in games})
    apps = sorted({g.adjust_app for g in games if g.adjust_app})
    ad_map: dict[str, str] = {}
    for g in games:
        ad_map.update(g.ad_type_map)
    raw = "lower(split_part(p, '^', 1))"
    whens = " ".join("when '%s' then '%s'" % (k.replace("'", "''"), v) for k, v in ad_map.items() if k != v)
    params = {
        "GAME_IDS": ", ".join(str(i) for i in ids),
        "ADJUST_APPS": ", ".join("'%s'" % a.replace("'", "''") for a in apps) or "''",
        "AD_TYPE_EXPR": f"(case {raw} {whens} else {raw} end)" if whens else raw,
        "START_MS": str(day_num(start_day) * 86400000),
        "START_S": str(day_num(start_day) * 86400),
        "END_MS": str((day_num(end_day) + 1) * 86400000 - 1),
        "END_S": str((day_num(end_day) + 1) * 86400 - 1),
        "START_DAY": str(day_num(start_day)),
        "END_DAY": str(day_num(end_day)),
        "START_DATE": start_day.isoformat(),
        "END_DATE": end_day.isoformat(),
        "EXCLUDE_COUNTRIES": ", ".join("'%s'" % c.replace("'", "''") for c in any_cfg.exclude_countries) or "''",
    }
    return _fill(template, params)


def run_sql(sql: str, database: str = "app_events", log=print) -> dict[str, list[dict]]:
    """Alias of execute_sql (kept for callers written against it)."""
    return execute_sql(sql, database=database, log=log)


def split_statements(sql: str) -> list[tuple[str | None, str]]:
    """[(out_name or None, statement)] in order. out_name None = '-- out: none' or no header."""
    stmts = [s.strip() for s in re.split(r"^;;\s*$", sql, flags=re.M) if s.strip()]
    out = []
    for s in stmts:
        m = re.search(r"^--\s*out:\s*(\w+)", s, flags=re.M)
        name = m.group(1) if m else None
        if name == "none":
            name = None
        out.append((name, s))
    return out


def _body(s: str) -> str:
    return re.sub(r"^\s*--[^\n]*\n", "", s + "\n", flags=re.M).strip().lower()


def guard(statements: list[tuple[str | None, str]]) -> None:
    for _, s in statements:
        b = _body(s)
        if not b:
            continue
        ok = (
            b.startswith("create temp table")
            or b.startswith("select")
            or b.startswith("with")
            or re.match(r"drop table (if exists )?t_\w+$", b)
        )
        if not ok:
            raise PermissionError("refused statement (read-only guard): %s" % b[:80])


def _py(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.decode()
    return v


def run_job(cfg: GameCfg, end_day: date, database: str = "app_events", template: str = "daily.sql",
            log=print) -> dict[str, list[dict]]:
    return execute_sql(render(cfg, end_day, template), database=database, log=log)


RETRY_DELAYS = (60, 180, 300)   # seconds between attempts: ~9 min in all, enough for a laptop to find its network


def _transient(e: Exception) -> bool:
    """A failure worth retrying: the SSH tunnel or the TCP connection, never a SQL error."""
    name = type(e).__name__
    text = str(e).lower()
    return ("tunnel" in name.lower() or "ssh" in name.lower()
            or name in ("OSError", "ConnectionError", "TimeoutError", "gaierror", "InterfaceError")
            or "could not establish" in text or "connection refused" in text or "network is unreachable" in text
            or "timed out" in text or "name resolution" in text)


def with_retries(fn, log=print, delays: tuple = RETRY_DELAYS):
    """Call fn(); on a transient connection failure wait and try again, up to len(delays) more times.
    The whole job restarts (temp tables live in the session that died)."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if attempt >= len(delays) or not _transient(e):
                raise
            log(f"[monitor] connection failed ({type(e).__name__}: {str(e)[:120]}); retrying in {delays[attempt]}s")
            time.sleep(delays[attempt])
            attempt += 1


def execute_sql(sql: str, database: str = "app_events", log=print) -> dict[str, list[dict]]:
    """Run a rendered multi-statement job in one Redshift session, retrying transient connection
    failures (the Mac wakes before its network does; the bastion is unreachable for a minute)."""
    return with_retries(lambda: _execute_once(sql, database, log), log)


def _execute_once(sql: str, database: str = "app_events", log=print) -> dict[str, list[dict]]:
    """Run a rendered job (daily.sql, intraday.sql) in ONE Redshift session, after the read-only
    guard; returns the '-- out:' results."""
    from app.connections import redshift as rs

    statements = split_statements(sql)
    guard(statements)
    results: dict[str, list[dict]] = {}
    t0 = time.time()
    with rs.connect(database) as conn:
        cur = conn.cursor()
        for name, s in statements:
            t1 = time.time()
            cur.execute(s)
            if name:
                cols = [d[0].decode() if isinstance(d[0], bytes) else str(d[0]) for d in cur.description]
                rows = [dict(zip(cols, (_py(v) for v in r))) for r in cur.fetchall()]
                results[name] = rows
                log("[monitor] %-14s %7d rows  %5.1fs" % (name, len(rows), time.time() - t1))
            else:
                log("[monitor] %-14s %s  %5.1fs" % ("(temp)", _body(s)[:50].replace("\n", " "), time.time() - t1))
    log("[monitor] pull done in %.0fs" % (time.time() - t0))
    return results


# --- CSV cache ----------------------------------------------------------------------------------

def save_cache(results: dict[str, list[dict]], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, rows in results.items():
        with open(folder / f"{name}.csv", "w", newline="") as fh:
            if not rows:
                fh.write("")
                continue
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if v is None else v) for k, v in r.items()})


def _coerce(v: str):
    if v == "":
        return None
    try:
        if re.fullmatch(r"-?\d+", v):
            return int(v)
        return float(v)
    except ValueError:
        return v


def load_cache(folder: Path) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    for p in sorted(Path(folder).glob("*.csv")):
        with open(p, newline="") as fh:
            rd = csv.DictReader(fh)
            rows = [{k: _coerce(v) for k, v in r.items()} for r in rd] if rd.fieldnames else []
        results[p.stem] = rows
    return results


if __name__ == "__main__":  # python -m app.monitor.pull arrows 2026-11-24  -> prints the rendered SQL
    from app.monitor import config as _c
    games = _c.load()
    g = games[sys.argv[1]]
    print(render(g, date.fromisoformat(sys.argv[2])))
