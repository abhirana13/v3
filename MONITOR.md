# Metric monitor — Slack reports of what went wrong, and why

`backend/app/monitor/` reads yesterday's metrics for every watched game straight from Redshift,
decides whether anything went wrong, works out the cause the way the Arrows drop playbook does
(`~/Documents/claude context/arrows metric drops context.md` §3), and posts ONE message per game
per day to Slack — and only when there is something to act on. An evening check (§5b) adds one message
per hard break the same day a remote-config / placement break happens. Configuration: `games.toml` next
to the code; adding a game is a TOML block, the gates decide whether it is watched.

| key | game | `game_id` | UA data | watched on 2026-11-25? |
|---|---|---|---|---|
| `arrows` | Arrows (Arrow Maze / LineOut) | 4 | Adjust "Arrow Maze" | yes — $4.0k/day, $5.5k UA |
| `nova` | Nova Solitaire | 1 (events from build 3.24.0+) | Adjust "Nova Solitaire" | yes — $1.25k/day, $1.75k UA |
| `sortery` | Sortery | 6 | not in Adjust ("Nut Sort" is another game): attributed paid installs | yes — $715/day, 387 paid installs/day |
| `zen` | Zen Solitaire (name inferred from install shape) | 14 | Adjust "Zen Solitaire" | no — $389/day |
| `seekfind` | Seek & Find (name inferred from install shape) | 15 | Adjust "Seek & Find" | no — $216/day |

Games 2, 3, 5, 8, 10, 11, 17 earn under $500/day; game 2 (≈$330/day, name unknown) is the next candidate.
Ad-type labels differ per game (Nova logs `inter` and `leader`): `[defaults.ad_type_map]` folds them to
interstitial / rewarded / banner; per-game additions go under `[games.<key>.ad_type_map]`.

## 1. When a game is watched at all (gates)

Both must hold, computed on every run; a game that fails either posts nothing, not even a digest.

| gate | rule |
|---|---|
| revenue | billed ad revenue, trailing 7-day mean, all platforms ≥ **$500/day** |
| active UA | games with an `adjust_app`: Adjust `cost`, mean of the trailing 7 days that have a non-zero cost ≥ **$100/day** (the newest day reads 0 until Adjust fills it, so it is skipped). Games not in Adjust: attributed paid installs (`attribution.events`, source client, every network but organic/unattributed), mean per day ≥ **50** |

**Pre-gate.** Before any per-game pull the run executes one shared job (`sql/pregate.sql`, ~20 s): revenue
per game-day, Adjust cost per app-day and attributed paid installs per game-day for the last 8 days.
Games that fail are logged as gated out and never pull their own 3-minute job. The result is written to
`MONITOR_DATA_DIR/gates.json`; the evening check reads it so it only runs on the games the morning admitted.

## 2. The metrics (per platform, UTC day, ex-India)

Every definition is the dashboard's (`TOOL_QUERIES_CONTEXT.md`): dates from `event_timestamp`
normalised to milliseconds, platform from `events.platform` (1 Android, 2 iOS), DAU = distinct
players with a `session` or `dau` event, impression = one `ad_tracking_max` row with `c = 'revenue'`
after dedup on the natural key, revenue = `g` with `,` → `.`.

| family | metric | wrong when | smallest move that counts |
|---|---|---|---|
| revenue | ad revenue ($), ARPDAU | down | 8 % |
| audience | DAU; new players (first active day, earlier of `users.created_at` and first event) | down | 12 % / 25 % |
| ua | UA spend (Adjust, latest filled day); attributed paid installs | down | 30 % — SEV3 at most on their own: a UA cut is a decision, not a fault, but it is the first thing a revenue finding checks |
| ads · volume | IT, RV, banner impressions per DAU | down | 10 / 10 / 15 % |
| ads · price | IT, RV, banner eCPM | down | 10 / 10 / 15 % |
| health | config-class ad blocks per DAU (`show_blocked` with `TriggerPointNotConfigured`, `TriggerPointDisabled`, `AdTypeDisabled`, `ManagerNotFound`, `PlacementNotFound`, `NoAdUnit`) | up | ×2 and ≥ 0.1/DAU; a rise from 0 counts at once |
| engagement | level wins per DAU, level starts per DAU (wins deduped on player × level × board hash) | down | 10 % |
| retention | rolling D1 = of players active on day D, share active on D+1 (read for the day before yesterday) | down | 8 % |

Rises in a "down is wrong" metric never post. Context pulled alongside, not alerted on: requests per
DAU, loads per DAU, show/load, billed/show (no fill *ratio*: Android 4.0.x logs 3× retry requests and
~200k/day empty-reason `load_fail` rows from a few looping offline clients, so any ratio on those rows
swings with logging — the fill rule reads loads per DAU against requests per DAU instead), placement split, age bands (D0 / D1–6 / D7–29 / D30+), geo (US / T1 /
RoW), build, serving network, experiment arms, Adjust installs/spend, control games' eCPM (games 5, 6),
hourly impression shape (data completeness).

## 3. How a move becomes a finding, and how loud it is

**Detection** (`detect.py`). baseline = median of the 7 days before the day read; σ = 1.4826 × MAD of
the residuals (value − trailing median) over the trailing 28 days, floored at 2 % of the baseline.
A finding needs |z| ≥ 3 **and** the metric's minimum relative move, in the wrong direction. Each
finding carries DoD (vs the day before), WoW (vs the same weekday last week), the trend (this
week's mean vs last week's) and `persist_days` — consecutive days beyond 2σ the same way.

**$ impact** (`severity.py`) = what yesterday would have earned had the metric stayed at its baseline,
at yesterday's volume: a volume move at yesterday's price, a price move at yesterday's volume, DAU at
baseline ARPDAU, blocks at 65 % show-through × IT eCPM.

| severity | meaning | rule |
|---|---|---|
| 🔴 **SEV1** | act now | a hard break (config-class blocks ≥ 0.5/DAU on ≥ 10 % of players from ~0 before; one placement −80 % while the others hold) — whatever the $ — **or** impact ≥ **$500/day** or ≥ **15 %** of that platform's daily ad revenue |
| 🟠 **SEV2** | look today | impact ≥ **$150/day** or ≥ **7 %**; or any unusual move whose cause is game-side (config, experiment, build); or an incomplete day (pipeline) |
| 🟡 **SEV3** | watch | everything else the detector flagged |

**Posting rules** (`run.py`, state in `/data/monitor/state_<game>.json`): SEV1 and SEV2 post the day
they appear; SEV3 posts only once it has lasted 3 consecutive days. An open SEV1 is repeated every day
until it recovers (it is money); an open SEV2 repeats on escalation or every 3 days; when anything
posts, the message carries every current finding. A SEV1 metric back inside 1.5σ gets one ✅ line.
Quiet days post nothing (a weekly WoW table is available, `weekly_summary = true`, default off).

## 4. The reasoning (`diagnose.py`)

The day is first checked for completeness (the last hour with impressions; a thin last-3-hours
share). An incomplete day posts one SEV2 "data incomplete" line and nothing else for that platform.

For a **volume** drop (impressions per DAU) the checks run in the order these causes have actually
been the answer on Arrows; the first that fits is the cause, the rest become the *Ruled out* line
with their numbers, a second fit is kept as *also*:

1. **config break** — a config-class block reason rose (picked by its rise, not its count: `ManagerNotFound`
   sits at 2–3/DAU chronically). Dated to the first day it appeared; attributed to the experiment
   arm whose players carry it — scored by the contrast between a key's arms, because a player sits in
   many keys and a finished test logs everyone as `control`; needs ≥ 80 % of the key's blocks in one
   arm at ≥ 5× the other arms' rate. Then the placement that died (this arm vs the others, same day,
   and vs its own healthy history) and blocks per level win (≈ 1 = every win asks and is refused).
2. **placement** — one placement −80 % while the others hold and no block reason logged (client side).
3. **engagement** — wins per DAU fell at least 60 % as much as the ad volume did.
4. **ad fill** — requests held but load success fell ≥ 5 pts (by network), or billed/show fell 20 %.
5. **mix** — Kitagawa split of the per-DAU rate by age band: mix ≥ 60 % of the move and D0 share
   moved ≥ 3 pts → audience, not behaviour (Adjust installs quoted).
6. **experiment change** — a key whose assigned players/day jumped ≥ 1.8× (or appeared) in the last
   3 days, with arms ≥ 25 % apart on this metric.
7. **build rollout** — a build that gained ≥ 10 pts to ≥ 15 % of DAU and runs ≤ 0.75× the others.
8. **banner only** — interstitials rose (banners pause under full-screen ads).

For a **price** drop (eCPM): fixed age × geo mix (mix ≥ 60 % → audience), one network vs the rest,
the control games' same-type eCPM (fell ≥ half as much → market), then experiment and build.
**Revenue / ARPDAU**: decomposed into DAU, volume and price per ad type; the dominant lever is
diagnosed and the finding adopts that diagnosis with the $ split on top. **UA is read on every revenue
finding** (`ua_context`): Adjust spend, installs and CPI on the latest filled day against the 7 filled
days before it (attributed paid installs when the game is not in Adjust). Spend down ≥ 20 % while the
lever is the audience or the mix → cause `ua_cut` ("UA spend was cut — fewer new players, so … fell; a
UA decision, not a game problem"), with the spend line as the first evidence; UA steady → it is named in
the ruled-out line with its numbers. **DAU / new players**: new
vs returning, Adjust spend and installs (UA cut / CPI), rolling D1, build. **Wins / starts per DAU**:
age mix, wins-per-start ratio (a logging change), experiment, build. **Rolling D1**: D0 share, install
surge, build, experiment. Anything that clears every check is reported as *unexplained* with the
checks listed — never silently dropped.

## 5. Message format

One Slack Block Kit message per game per run; the header carries the worst severity and SEV1 starts
with `<!here>` (configurable). The layout is built to be read in one glance:

| block | content |
|---|---|
| header | `🔴 Arrows · Tue Nov 24 · SEV1 act now` |
| summary | *who is losing how much* — one plain sentence on the cause, then `since … · day N · confirmed` |
| **What moved** | a two-column grid, one tile per finding: value · vs 7-day baseline, then DoD · WoW · $/day · day N |
| **Why** | up to five evidence bullets (proof first, the $ split last) and a `🔧 Fix` line; one per distinct cause, two at most |
| ruled out | small grey text: the checks that cleared, each with its number |
| footer | small grey text: the gates, DAU per platform, how far the day's data reaches |

The real Nov 24 2026 message, as the dry run renders it (fields print indented; in Slack they sit in two
columns): `python -m app.monitor run --game arrows --date 2026-11-24 --from-cache /data/monitor/arrows/2026-11-24 --dry-run --no-state`

```
🔴 Arrows · Tue Nov 24 · SEV1 act now
====================================
<!here> *iOS is losing ~$814/day* — interstitials stopped showing for the `interstitial_placement` *variant_a* players (49% of iOS): the game asks on every level win and its own config refuses it (`TriggerPointNotConfigured`)
_since Nov 23 · day 2 · confirmed_
----------------------------------------
*What moved*  _(value · vs 7-day baseline · DoD · WoW)_
    🔴 *iOS ad revenue*
    $2,101 · ▼28% vs 7d
    DoD ▼16% · WoW ▼28% · -$814/day · day 2
    🔴 *iOS IT imps/DAU*
    4.05 · ▼38% vs 7d
    DoD ▼25% · WoW ▼41% · -$663/day · day 2
    🔴 *iOS config-class ad blocks/DAU*
    6.41 · ▲211% vs 7d
    DoD ▲56% · WoW ▲278% · day 2
----------------------------------------
*Why*
•  `TriggerPointNotConfigured`: 37,592 blocks = 5.35/DAU, hitting 36% of players (first seen Nov 23; none before)
•  100% of them in `interstitial_placement` *variant_a* (49% of the test): 11.1/DAU there vs 0/DAU in the other arm(s)
•  IT imps/DAU by arm: control 7.98 → 6.14; variant_a 8.59 → 1.88
•  its `level_win_next_button` placement: 0.003/DAU vs 4.67/DAU in the other arm(s) (▼100%)
•  ≈1.03 blocks per level win in that arm: every win asks and is refused
🔧 *Fix:* Configure the `interstitial_placement` *variant_a* trigger point(s) in remote config on iOS, or pause the arm. Nov 23 → fix day is a dead window for reads of this test.
  ✔ Ruled out: play (wins/DAU ▲11%, starts/DAU ▲12%) · fill (requests/DAU 15.1 → 12.4, loads/DAU 14.6 → 11.9, billed/show 0.993) · rewarded (RV/DAU 2.07 → 2.19, ▲6%) · audience mix (D0 share 11% → 12%; age bands D0 ▼37%, D1-6 ▼42%, D7-29 ▼40%, D30+ ▼30%) · `interstitial_placement` re-assigned players in the last 3 days (the test's ramp) · build 3.9.1 (28% of DAU; both arms on it, so it reads low too)
  ads $4,035/day (7d) · UA $5,478/day · DAU Android 6,626 / iOS 7,023 · data complete to 23:59 UTC
```

The hand-made read of the same incident (`~/Documents/experiment reads/arrows_ios_it_drop_2026-11-25/`)
found: 6.69 → 4.04 IT/DAU, 37,575 blocks ≈ one per win, variant placement 18,869 → 14, ≈ −$830/day.
Android that day: IT/DAU −11 % (z −1.2), not flagged — the manual read called it "watch, not alarm".

**Day 1.** Replaying Nov 23 (the break began mid-day) already gives SEV1 with the same cause: iOS ad
revenue ▼15 % (−$449/day), IT imps/DAU 6.6 → 5.41 (z −3.9), `TriggerPointNotConfigured` 18,167 = 2.57/DAU on
23 % of players, 100 % in `interstitial_placement` variant_a, 0.64 blocks per win. With the schedule on, this
would have been in Slack at 10:00 IST on Nov 24 — a day before the question was asked.

**Quiet day.** Replaying Nov 20 posts nothing (`NOTHING_TO_POST`): no metric on either platform was beyond
3σ in the wrong direction. The channel stays silent on ordinary days.

## 5b. Evening check — hard breaks the same day, not the next morning

The daily read of Nov 23 would have reached Slack at 10:00 IST on Nov 24, ~20 h after the win-ad
trigger stopped firing. `intraday.py` closes that gap for the one class of incident that is cheap to
fix and compounds by the hour: a break in the game's own ad configuration. It is deliberately narrow —
the user does not want a bombardment — so it reads only hard-break signals, and everything it fires is
SEV1. Ordinary moves (price, audience, slow slides) wait for the daily read.

**Cadence and window.** `intraday_monitor_job` runs **once a day in the evening**, at 12:45 UTC = 18:15 IST
by default (`MONITOR_INTRADAY_ENABLED=true`, `MONITOR_INTRADAY_HOUR_UTC`, `MONITOR_INTRADAY_MINUTE_UTC`).
It pulls one lightweight Redshift job (`sql/intraday.sql`: the last 8 days of the game by platform × UTC
hour, temp tables only, ~45 s) and reads the **last 12 complete UTC hours** (`window_hours`) — 00:00–12:00
UTC, i.e. 05:30–17:30 IST — against the **same 12 hours on each of the previous 7 days** (median). Together
with the 10:00 IST daily read of the previous UTC day there is no gap: a change made in the Indian morning
or afternoon is caught the same evening, a change made in the evening or overnight by the next morning's
read. Ingestion lag on game 4 is ~0 (the hour is complete as it ends), so the window ends at the last clock
hour. Rates are per **active player in the window** = distinct players with a `session`/`dau` event in those
hours; the comparison windows are computed the same way. A platform is not read when it has < 200 active
players or when the window's last hour carries < 40 % of its usual players (incomplete data must not look
like a collapse).

**Rules** (`games.toml` `[defaults.intraday]`; one break per platform per run, the first that fits wins):

| break | rule | attributed to |
|---|---|---|
| `config_block` | a config-class block reason (`[games.arrows.block_reasons]`) at **≥ 0.5 per active player** in the window, hitting **≥ 10 % of active players**, from ~0 before (≤ 0.05/player and ≤ a tenth of now in the comparison windows — `ManagerNotFound` sits at ~1/player chronically and never qualifies) | the experiment arm whose players carry the blocks (`diagnose.attribute_blocks_to_arm` on the window cuts: ≥ 80 % of the key's blocks in one arm at ≥ 5× the others), the placement that died in that arm (`diagnose.arm_dead_placement`), blocks per level win |
| `placement` | one interstitial placement at **≤ 20 %** of its usual imps per player while every other meaningful placement (usually ≥ 0.3/player, ≥ 100 imps per window) holds **≥ 65 %**, and no block reason rose | the arm at ≤ 25 % of the other arms' rate, if any |
| `volume` | IT or RV imps per player **≤ 60 %** of usual while level wins per player hold **≥ 85 %** (play held, the ads did not) | same arm contrast |

Each break is dated to the hour it began (the trailing run of broken hours, looked back 48 h) and
valued at −$/day: the lost imps per player × the window's active players, scaled to a day by the
window's usual share of the day's impressions, at the current eCPM.

**Posting and cooldown.** One Block Kit message per break, header `🔴 Arrows · intraday · HH:00–HH:00 UTC`,
`<!here>`, the same headline / *What moved* / *Why* / *Fix* shape as the daily message. The signature
(`arrows:<platform>:intraday:<kind>:<reason|placement|ad type>`) posts at most **once per 12 h**
(`cooldown_hours`), tracked in the same state file as the daily incidents (`state_<game>.json`,
`intraday`); a run that sees the break under cooldown logs it and stays quiet. The daily run's message
carries a `⏱ intraday alert <time>: <headline>` line for alerts posted on the read day or the morning
after, so the incident is on the record even when it was fixed before the day ended.

**The Nov 23 replay** (`--at 2026-11-23T18:00`, window 12:00–18:00 UTC, dry run on the real pull):

```
🔴 Arrows · intraday · 12:00–18:00 UTC
<!here> iOS config-class ad blocks/player 2.10, from 0.000 usually → -$504/day at this rate
iOS: the game refused its own ads — `TriggerPointNotConfigured` 2.10/player on 27% of players,
in `interstitial_placement` variant_a, since ~09:00 UTC Nov 23

What moved (window 12:00–18:00 UTC · per active player)
🔴 iOS config-class ad blocks/player: 2.10 (from 0.000 in the same hours, last 7 days) · `TriggerPointNotConfigured` on 27% of players
· iOS IT imps/player: 2.75 (▼27% vs the same hours, last 7 days)
· iOS RV imps/player: 1.26 (▲7%) · iOS level wins/player: 5.07 (▲7%)

Why — iOS
  • show_blocked TriggerPointNotConfigured: 5,656 in the window = 2.10 per active player, hitting 27% of players (0.000/player before)
  • 100% of them sit in interstitial_placement arm variant_a (48% of that test's players) — 5.17 per arm player,
    66% of the arm's players; the other arm(s) 0/player
  • placement level_win_next_button in variant_a: 0.154/player in the window vs other arms 2.75/player,
    this arm 3.20/player in the same hours before (▼94%)
  • ≈ 0.960 blocks per level win in that arm — every win asks for the ad and is refused
  Ruled out: by placement: level_win_next_button ▼36%, out_of_lives_restart_button ▲5%; play flat (wins/player ▲7%)
  Fix: Configure the trigger point(s) for interstitial_placement variant_a in remote config on iOS (or pause
  that arm). Treat 09:00 UTC Nov 23 → fix as a dead window in any read of this test.
_Mon Nov 23 · SEV1 — act now · active players in the window Android 2,714 / iOS 2,695 · data to 18:xx UTC_
```

The break began at ~09:00 UTC. Replaying the schedule's earlier slot (`--at 2026-11-23T12:10`, window
06:00–12:00 UTC, three broken hours) already fires the same SEV1 — `TriggerPointNotConfigured` 0.755/player
on 11 % of players, 100 % in `interstitial_placement` variant_a, `level_win_next_button` ▼81 % in that arm,
0.95 blocks per win — so with the job on it would have been in Slack at 12:10 UTC = 17:40 IST on Nov 23,
against 10:00 IST the next morning from the daily. **Nov 20** (`--at 2026-11-20T18:00`, a quiet day)
posts nothing on either platform (`NOTHING_TO_POST`). Tests: `backend/tests/test_monitor_intraday.py` (synthetic hourly cuts in the
`make_cuts` style: the break, a break only one hour old, a dead placement without a reason, a volume
collapse with and without play, a thin last hour, the cooldown, the message, the SQL guard).

## 6. Running it

```bash
# the SQL it would run (one Redshift session; temp tables only — the read-only guard refuses anything else)
docker compose exec -T backend python -m app.monitor render --game arrows --date 2026-11-24

# pull + analyse + print the message, post nothing, touch no state (~3.5 min on Redshift)
docker compose exec -T backend python -m app.monitor run --game arrows --dry-run --no-state

# every metric's reading, flagged or not, from a cached pull
docker compose exec -T backend python -m app.monitor readings --game arrows --date 2026-11-24 --from-cache /data/monitor/arrows/2026-11-24

# the real thing for yesterday: posts if the rules say so, updates state
docker compose exec -T backend python -m app.monitor run --game arrows

# the shared pre-gate for every configured game (what the scheduled run does first)
docker compose exec -T backend python -m app.monitor pregate

# intraday: the last 6 complete hours before now (or before --at), print the message, post nothing (~45 s)
docker compose exec -T backend python -m app.monitor intraday --game arrows --dry-run --no-state
docker compose exec -T backend python -m app.monitor intraday --game arrows --at 2026-11-23T18:00 --dry-run --no-state
# the same from the cached pull / the SQL it would run / machine-readable
docker compose exec -T backend python -m app.monitor intraday --game arrows --at 2026-11-23T18:00 --dry-run --no-state --from-cache /data/monitor/arrows/intraday/2026-11-23T18
docker compose exec -T backend python -m app.monitor intraday --game arrows --render
docker compose exec -T backend python -m app.monitor intraday --game arrows --dry-run --json
```

Pulls are cached per run under `MONITOR_DATA_DIR/<game>/<date>/` (daily) and
`MONITOR_DATA_DIR/<game>/intraday/<YYYY-MM-DDTHH>/` (intraday), CSV per cut, so any day or window can be
replayed with `--from-cache`. Tests: `docker compose exec -T backend python -m pytest tests/test_monitor.py
tests/test_monitor_intraday.py` (a synthetic replica of the Nov 23–24 break plus detector, severity,
posting-rule and format tests; the intraday rules, cooldown and message).

### Schedule
The worker (`app.backpop.scheduler`) runs the monitor daily at `MONITOR_HOUR_UTC:MONITOR_MINUTE_UTC`
(default 04:30 UTC = 10:00 IST) when `MONITOR_ENABLED=true`, and the intraday check every
`MONITOR_INTRADAY_EVERY_HOURS` hours at minute `MONITOR_INTRADAY_MINUTE` (default every 6 h at :10)
when `MONITOR_INTRADAY_ENABLED=true` — the two flags are independent, both off by default. The worker
has no hot reload: `docker compose restart worker` after changing `.env` or the code.

### Slack setup (one-time, by hand)
1. Create the channel (e.g. `#game-alerts`).
2. Slack → Apps → *Incoming Webhooks* → add to that channel → copy the webhook URL.
3. `.env`: `SLACK_MONITOR_WEBHOOK_URL=https://hooks.slack.com/services/…` and `MONITOR_ENABLED=true`;
   restart the worker. The URL is a secret: `.env` only, never in git or in a message.

### Adding a game
Copy a `[games.<key>]` block in `games.toml`: `game_id`, `adjust_app` as spelled in
`adjust.adjust_performance.app` (empty when the game is not in Adjust — UA then comes from attribution),
platforms, optional `market_control_games` (default: every other enabled game), optional
`[games.<key>.ad_type_map]` for labels not in the default map, optional `placement_aliases`. Check with
`python -m app.monitor pregate` — the gates decide whether it posts; a game under $500/day costs nothing
beyond its share of the 20-second pre-gate.

## 6b. Where it runs, and what happens when it cannot

Today the worker is the local Docker stack on the analyst's Mac. **A sleeping Mac runs nothing**: on
Nov 25–26 both scheduled runs fired on wake-up, before the network was back, and died on the SSH
tunnel to the Redshift bastion (`Could not establish session to SSH gateway`) — silently. Three
defences since:

- every Redshift job retries a transient connection failure after 60 s, 180 s and 300 s (`pull.with_retries`;
  SQL errors are never retried);
- a run that still fails posts one ⚠️ line to the channel naming the games it could not read, so a quiet
  morning is never mistaken for a healthy one (`run.notify_failures`, also for the evening check);
- the scheduler's misfire grace is 4 h: a job due at 04:30 UTC still runs if the machine wakes by 08:30 UTC.

The durable fix is to run the worker where the analytics tool already runs (the EC2 host behind
`analytics.internal.felicitytools.com`, `docker-compose.prod.yml`): the same `.env` keys
(`MONITOR_ENABLED`, `MONITOR_INTRADAY_ENABLED`, `SLACK_MONITOR_WEBHOOK_URL`) and nothing else to change.
Until then, keep the Mac awake across 10:00 and 18:15 IST, or run the catch-up by hand:

```bash
docker compose exec -T worker python -c "from datetime import date; from app.monitor.run import run_all; run_all(date(2026, 11, 25))"
```

## 6c. Deploy plan — moving the worker to the EC2 host (drafted 2026-11-26)

**Facts.** Prod is the EC2 host behind `analytics.internal.felicitytools.com`, run from
`docker-compose.prod.yml`. The worker image is BUILT from `./backend` (no code mount: shipping code
means a rebuild), `/data` is the persistent `duckdb_data` volume (monitor state and cache land there),
`./secrets/redshift_bastion.pem` is mounted read-only, `.env` lives on the host. The repo is
`github.com/abhirana13/v3`, `main` at `dabdc7f`; this checkout (`qa/push-candidate`) is one commit ahead
and carries ~65 unrelated uncommitted files (date-major backpop, access control, experiment console,
frontend) that must NOT ride along. The monitor touches only: `backend/app/monitor/**`,
`backend/tests/test_monitor*.py`, `MONITOR.md`, two job functions plus one block in
`backend/app/backpop/scheduler.py` (main's copy has the same shape), and a block in `.env.example`.
No new Python dependencies (stdlib + what the backend already has).

| step | who | what |
|---|---|---|
| 1 | Claude, on the user's word | `git worktree add ../v3-monitor origin/main -b monitor/slack-alerts`; copy the monitor files in; apply only the monitor hunks to `scheduler.py` and `.env.example`; run the 37 tests against that tree; commit `feat(monitor): Slack metric monitor (daily read + evening hard-break check)`; push; open a PR to `main` |
| 2 | user | merge the PR |
| 3 | user, on the host | `.env`: add `MONITOR_ENABLED=true`, `MONITOR_HOUR_UTC=4`, `MONITOR_MINUTE_UTC=30`, `MONITOR_INTRADAY_ENABLED=true`, `MONITOR_INTRADAY_HOUR_UTC=12`, `MONITOR_INTRADAY_MINUTE_UTC=45`, `SLACK_MONITOR_WEBHOOK_URL=` (the same webhook; secret, by hand). Check `df -h /` first — the box has filled up before; the monitor keeps ≤ ~0.7 GB (3 games × ~15 MB/day × 14 days, pruned by `MONITOR_CACHE_KEEP_DAYS`) |
| 4 | user, on the host | `git pull` then `docker compose -f docker-compose.prod.yml up -d --build worker` (rebuilds and recreates the worker only; the backend keeps its running image) |
| 5 | user, on the host | verify: `docker compose -f docker-compose.prod.yml logs --tail=20 worker \| grep scheduler` must read `metric monitor at 04:30 UTC, evening check at 12:45 UTC`; `docker compose -f docker-compose.prod.yml exec -T worker python -m app.monitor pregate` (~20 s: proves the tunnel from the host and prints the gates); `… exec -T worker python -m app.monitor run --game arrows --dry-run --no-state` (~3.5 min: proves the full path, posts nothing) |
| 6 | user, on the Mac | `.env`: `MONITOR_ENABLED=false`, `MONITOR_INTRADAY_ENABLED=false`; `docker compose up -d worker` — otherwise both machines post. State starts fresh on the host (the Arrows incident is already closed); nothing to copy |

**Timing.** Done before 18:15 IST, the host takes that evening's check; otherwise its first run is
10:00 IST the next morning. **Risks.** The host's tunnel is the one the nightly backpop already uses;
the monitor queues behind that backpop on the worker's single thread (04:30 UTC slips if the backpop
runs long; the 4 h misfire grace covers it); the first run has no cache and takes the same ~8 min as
every run; the next backend deploy will carry the monitor files harmlessly. **Rollback.** Set the two
`MONITOR_*_ENABLED` keys to false and recreate the worker; nothing else in the app changes.

## 7. Known limits
- The baseline is the median of the 7 days before; during a multi-day incident it drifts towards the
  broken level, so the $ figure shrinks day by day (the first day's figure is the honest one).
- Two heavy scans per run (`t_act` from Jul 1 for install days, `t_ad` for the 35-day window); ~3.5 min
  on Redshift. A local DuckDB store pulling only the last 4 days would cut this if the daily cost matters.
- The intraday check reads hard breaks only (§5b); a break that is real but gentle — one placement at
  40 % of usual, blocks on 5 % of players — waits for the daily read by design. Its −$/day figure scales a
  6-hour window to a day by the window's usual share of impressions, so it is an order of magnitude, not
  the daily run's number.
- Crash / ANR (Play vitals) and IAP are not watched; IAP is < 1 % of Arrows revenue.
- The Android `load_fail` retry storm (offline clients, ~2,900 players/day since ~Nov 18) is a known client
  bug and deliberately not alerted; `<type>_load_fails_per_dau` is in the series if a rule is wanted.
