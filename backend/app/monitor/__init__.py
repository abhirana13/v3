"""Metric monitor — reads yesterday's game metrics from Redshift, decides what went wrong, and
posts to Slack only when something did. Design, thresholds and formats: MONITOR.md at the repo
root; the per-game configuration: games.toml next to this file.

Pipeline (run.py): gates -> pull -> series -> detect -> diagnose -> severity -> state -> report -> post.
Intraday (intraday.py): every few hours, the last 6 complete UTC hours vs the same hours on the previous
7 days, hard breaks only (config-class blocks, a dead placement, a volume collapse) — SEV1 or nothing.
"""
