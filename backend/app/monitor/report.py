"""Slack messages. One message per game per run, headed by the worst severity. Block Kit with a
plain-text fallback. Layout (MONITOR.md §5):

  header    🔴 Arrows · Tue Nov 24 · SEV1 act now
  section   <!here> *iOS is losing ~$814/day* — one-sentence cause        _since Nov 23 · day 2 · confirmed_
  section   *What moved* + two-column fields, one per finding (value · vs 7d / DoD · WoW)
  section   *Why* — up to four evidence bullets, then 🔧 Fix
  context   ✔ Ruled out: play · fill · rewarded · audience mix · build        (small grey text)
  context   ads $/day · UA $/day · DAU · data frontier

Keep this file and MONITOR.md §5 in step."""
from __future__ import annotations

from datetime import date

from app.monitor import fmt
from app.monitor.config import GameCfg
from app.monitor.pull import day_date
from app.monitor.severity import EMOJI, LABEL, Scored

CONF_WORD = {"confirmed": "confirmed", "likely": "likely", "possible": "possible", "unexplained": "unexplained"}
FAMILY_RANK = {"revenue": 0, "ads": 1, "audience": 2, "engagement": 3, "retention": 4, "ua": 5, "health": 6}


def _sorted(scored: list[Scored]) -> list[Scored]:
    # within a severity the money metric leads, health signals (blocks) come last
    return sorted(scored, key=lambda s: (s.severity, FAMILY_RANK.get(s.spec.family, 9), -(abs(s.impact_usd or 0))))


def _field(s: Scored, cfg: GameCfg) -> str:
    r, spec = s.reading, s.spec
    plat = cfg.platform_name(r.platform)
    when = f" ({day_date(r.day).strftime('%b %d')})" if spec.lag_days else ""
    base_txt = (f"from {fmt.value(r.baseline, spec.unit)}" if (r.rel_base is None and r.baseline is not None)
                else f"{fmt.arrow_pct(r.rel_base)} vs 7d")
    line1 = f"{EMOJI[s.severity]} *{plat} {spec.label}*{when}"
    line2 = f"{fmt.value(r.value, spec.unit)} · {base_txt}"
    line3 = f"DoD {fmt.arrow_pct(r.rel_dod)} · WoW {fmt.arrow_pct(r.rel_wow)}"
    extras = []
    if s.impact_usd is not None and abs(s.impact_usd) >= 20 and spec.family != "health":
        extras.append(f"{fmt.usd(s.impact_usd)}/day")
    if r.persist_days >= 2:
        extras.append(f"day {r.persist_days}")
    if extras:
        line3 += " · " + " · ".join(extras)
    return "\n".join((line1, line2, line3))


def _since(s: Scored) -> str:
    d = s.diagnosis
    r = s.reading
    first = d.tags.get("first_block_day")
    if first is None and r.persist_days:
        first = r.day - r.persist_days + 1
    bits = []
    if first is not None:
        bits.append(f"since {day_date(int(first)).strftime('%b %d')}")
    if r.persist_days >= 2:
        bits.append(f"day {r.persist_days}")
    bits.append(CONF_WORD.get(d.confidence, d.confidence))
    return " · ".join(bits)


def _summary_line(top: Scored, cfg: GameCfg, mention: str) -> str:
    d = top.diagnosis
    plat = cfg.platform_name(top.reading.platform)
    if top.impact_usd is not None and top.impact_usd < -20:
        lead = f"*{plat} is losing ~{fmt.usd(-top.impact_usd)}/day*"
    else:
        lead = f"*{plat} {top.spec.label} {fmt.arrow_pct(top.reading.rel_base)}*"
    summary = d.summary or d.headline
    text = f"{lead} — {summary}\n_{_since(top)}_"
    return f"{mention} {text}" if mention else text


def _why(s: Scored, cfg: GameCfg, with_title: bool) -> str:
    d = s.diagnosis
    title = "*Why*" if not with_title else f"*Why — {cfg.platform_name(s.reading.platform)} {s.spec.label}*"
    lines = [title]
    if not d.evidence and d.summary:
        lines.append(d.summary)
    for e in d.evidence[:5]:
        lines.append(f"•  {e}")
    if d.fix:
        lines.append(f"🔧 *Fix:* {d.fix}")
    return "\n".join(lines)


def _join_items(items: list[str], limit: int) -> str:
    out: list[str] = []
    text = ""
    for x in items:
        if not x:
            continue
        cand = " · ".join(out + [x])
        if len(cand) > limit:
            text += " …"
            break
        out.append(x)
        text = cand
    return text


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:1900]}]}


def build_message(cfg: GameCfg, day: date, scored: list[Scored], recoveries: list[str], footer: dict,
                  run_label: str = "") -> dict:
    """scored = the findings to show (already filtered by the posting rules)."""
    scored = _sorted(scored)
    worst = min((s.severity for s in scored), default=3)
    head = f"{EMOJI[worst]} {cfg.name} · {day.strftime('%a %b %d')} · SEV{worst} {LABEL[worst]}"
    mention = cfg.slack_mention_sev1 if worst == 1 else (cfg.slack_mention_sev2 if worst == 2 else "")
    blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": head[:150]}}]

    top = scored[0] if scored else None
    summary = _summary_line(top, cfg, mention) if top else ""
    if top:
        blocks.append(_section(summary))

    if scored:
        blocks.append({"type": "divider"})
        fields = [{"type": "mrkdwn", "text": _field(s, cfg)[:2000]} for s in scored[:10]]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*What moved*  _(value · vs 7-day baseline · DoD · WoW)_"}, "fields": fields})

    # one Why per distinct cause, two at most; the top finding's cause first
    explained = [s for s in scored if s.severity <= 2 and (s.diagnosis.cause != "unexplained" or s.diagnosis.ruled_out)]
    seen: set = set()
    whys: list[Scored] = []
    for s in explained:
        t = s.diagnosis.tags
        ident = tuple(sorted((k, str(v)) for k, v in t.items() if k in ("reason", "exp_key", "arm", "placement", "build")))
        key = (s.diagnosis.cause, s.reading.platform, ident) if ident else (s.diagnosis.cause, s.diagnosis.headline)
        if key in seen:
            continue
        seen.add(key)
        whys.append(s)
        if len(whys) == 2:
            break
    ruled: list[str] = []
    for s in whys:
        blocks.append({"type": "divider"})
        blocks.append(_section(_why(s, cfg, with_title=len(whys) > 1)))
        for x in s.diagnosis.ruled_out:
            if x and x not in ruled:
                ruled.append(x)
    if ruled:
        blocks.append(_context("✔ Ruled out: " + _join_items(ruled, 1500)))

    if recoveries:
        blocks.append({"type": "divider"})
        blocks.append(_section("\n".join(f"✅ {x}" for x in recoveries)))

    ctx_bits = []
    if footer.get("ad_revenue_7d") is not None:
        ctx_bits.append(f"ads {fmt.usd(footer['ad_revenue_7d'])}/day (7d)")
    if footer.get("ua_spend_7d") is not None:
        ctx_bits.append(f"UA {fmt.usd(footer['ua_spend_7d'])}/day")
    elif footer.get("paid_installs_7d") is not None:
        ctx_bits.append(f"UA {footer['paid_installs_7d']:,.0f} paid installs/day (attributed)")
    if footer.get("dau"):
        ctx_bits.append("DAU " + " / ".join(f"{cfg.platform_name(p)} {v:,.0f}" for p, v in footer["dau"].items()))
    if footer.get("frontier"):
        ctx_bits.append(f"data complete to {footer['frontier']}")
    if run_label:
        ctx_bits.append(run_label)
    if cfg.dashboard_url:
        ctx_bits.append(f"<{cfg.dashboard_url}|dashboard>")
    if ctx_bits:
        blocks.append(_context(" · ".join(ctx_bits)))

    fallback = head + ("\n" + summary.replace("*", "").replace("_", "") if summary else "")
    return {"text": fallback[:3000], "blocks": blocks}


def recovery_message(cfg: GameCfg, day: date, lines: list[str], footer: dict) -> dict:
    fully = any(" is back at " in x for x in lines)
    head = f"{'✅' if fully else '🟢'} {cfg.name} · {day.strftime('%a %b %d')} · {'recovered' if fully else 'no longer flagged'}"
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": head}}, _section("\n".join(f"✅ {x}" for x in lines))]
    return {"text": head, "blocks": blocks}


def weekly_message(cfg: GameCfg, day: date, rows: list[tuple[str, str, float | None, float | None, float | None]]) -> dict:
    """rows: (platform name, metric label, this week mean, last week mean, rel)."""
    head = f"📊 {cfg.name} · week to {day.strftime('%b %d')} · WoW"
    fields = [{"type": "mrkdwn", "text": f"*{p} {label}*\n{fmt.num(now)} vs {fmt.num(prev)} · {fmt.arrow_pct(rel)}"[:2000]}
              for p, label, now, prev, rel in rows[:10]]
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": head}}]
    if fields:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*This week vs last*"}, "fields": fields})
    else:
        blocks.append(_section("no data"))
    return {"text": head, "blocks": blocks}


def to_text(payload: dict) -> str:
    """Readable rendering of a Block Kit payload for dry runs and logs."""
    out = []
    for b in payload.get("blocks", []):
        if b["type"] == "header":
            out.append(b["text"]["text"])
            out.append("=" * min(80, len(b["text"]["text"])))
        elif b["type"] == "section":
            if b.get("text"):
                out.append(b["text"]["text"])
            for f in b.get("fields", []):
                out.append("    " + f["text"].replace("\n", "\n    "))
        elif b["type"] == "divider":
            out.append("-" * 40)
        elif b["type"] == "context":
            out.append("  " + " ".join(e["text"] for e in b["elements"]))
    return "\n".join(out)


# --- intraday ------------------------------------------------------------------------------------

def _hour_dt(hr: int):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(hr) * 3600, tz=timezone.utc)


def intraday_message(cfg: GameCfg, win, brk, footer: dict) -> dict:
    """One hard break from the intraday check (app.monitor.intraday), in the same visual shape as the
    daily message: header, one-line summary, a two-column grid of the window's rates, Why + Fix,
    then the ruled-out and footer rows in small text. Everything intraday posts is SEV1."""
    plat = cfg.platform_name(brk.platform)
    head = f"🔴 {cfg.name} · intraday · {win.label()}"
    mention = cfg.slack_mention_sev1
    loss = f" → {fmt.usd(brk.impact_usd_per_day)}/day at this rate" if brk.impact_usd_per_day else ""
    if brk.since_hr is not None:
        since = f"since ~{_hour_dt(brk.since_hr):%H:%M} UTC {_hour_dt(brk.since_hr):%b %d}"
    elif brk.since_before:
        since = "since before the 48 h lookback"
    else:
        since = "started in this window"
    summary = f"*{plat} · SEV1 act now*{loss}\n{brk.headline}\n_{win.date_label()} · {since}_"
    if mention:
        summary = f"{mention} {summary}"
    blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": head[:150]}}, _section(summary)]

    if brk.rates:
        blocks.append({"type": "divider"})
        fields = []
        for r in brk.rates[:10]:
            if r.ratio is None:
                cmp = f"from {fmt.num(r.base)} usually"
            else:
                cmp = f"{fmt.arrow_pct(r.ratio - 1)} vs the same hours, last 7 days"
            txt = f"{'🔴' if r.severe else '·'} *{plat} {r.label}*\n{fmt.num(r.now)} · {cmp}"
            if r.extra:
                txt += f"\n{r.extra}"
            fields.append({"type": "mrkdwn", "text": txt[:2000]})
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": f"*What moved*  _(window {win.label()} · per active player)_"},
                       "fields": fields})

    blocks.append({"type": "divider"})
    why = [f"*Why — {plat}*"] + [f"•  {e}" for e in brk.evidence[:5]]
    if brk.fix:
        why.append(f"🔧 *Fix:* {brk.fix}")
    blocks.append(_section("\n".join(why)))
    if brk.ruled_out:
        blocks.append(_context("✔ Ruled out: " + _join_items(list(brk.ruled_out), 1500)))

    ctx_bits = []
    active = footer.get("active") or {}
    if active:
        ctx_bits.append(" / ".join(f"{cfg.platform_name(p)} {v:,.0f}" for p, v in active.items()) + " active players in the window")
    if footer.get("frontier"):
        ctx_bits.append(f"data to {footer['frontier']}")
    if cfg.dashboard_url:
        ctx_bits.append(f"<{cfg.dashboard_url}|dashboard>")
    if ctx_bits:
        blocks.append(_context(" · ".join(ctx_bits)))
    fallback = head + "\n" + summary.replace("*", "").replace("_", "")
    return {"text": fallback[:3000], "blocks": blocks}
