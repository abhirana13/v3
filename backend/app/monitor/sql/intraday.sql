-- Metric monitor, intraday hard-break check: one lightweight job per game per run, executed in ONE
-- Redshift session (temp tables are the CLAUDE.md exception: session-scoped CREATE TEMP TABLE ... AS
-- SELECT, nothing persistent). Same shape as daily.sql: statements are separated by a line holding
-- only ';;'; a statement headed '-- out: <name>' streams its result back as <name>; '-- out: none'
-- builds a temp table.
--
-- Placeholders (filled by pull.render_intraday): {{GAME_ID}} {{START_MS}} {{START_S}} {{END_MS}} {{END_S}}
-- {{START_HR}} {{END_HR}} {{WIN_HOURS}} {{ARM_START_MS}} {{ARM_START_S}} {{EXCLUDE_COUNTRIES}} {{AD_TYPE_EXPR}}
--
-- Grain: platform x UTC hour over the last 8 days up to now; hr = ts_ms / 3600000 (epoch hour), every
-- timestamp normalised to milliseconds first (events.event_date is corrupt). The ih_* cuts are hourly
-- counts. The iw_* cuts count DISTINCT players per window: w = 0 is the last {{WIN_HOURS}} complete
-- hours before {{END_HR}}, w = 1..7 the same hours on each of the previous seven days
-- (w = ({{END_HR}} - 1 - hr) / 24 for the hours with ({{END_HR}} - 1 - hr) % 24 < {{WIN_HOURS}}).
-- Impression = one ad_tracking_max row with c = 'revenue' after dedup on the natural key.

-- out: none
create temp table ti_act as
select fel_id, platform, hr
from (
  select fel_id, platform,
         (case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end) / 3600000 as hr
  from events
  where game_id = {{GAME_ID}} and k in ('session', 'dau') and platform in (1, 2)
    and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
    and (event_timestamp between {{START_MS}} and {{END_MS}} or event_timestamp between {{START_S}} and {{END_S}})
) z
where hr >= {{START_HR}}
group by 1, 2, 3
;;
-- out: ih_act
select platform, hr, count(*) as players
from ti_act
group by 1, 2
;;
-- out: iw_act
select platform, ({{END_HR}} - 1 - hr) / 24 as w, count(distinct fel_id) as players
from ti_act
where hr < {{END_HR}} and ({{END_HR}} - 1 - hr) % 24 < {{WIN_HOURS}}
group by 1, 2
;;
-- out: none
create temp table ti_ad as
select fel_id, platform, ad_type, placement, price, ts_ms / 3600000 as hr
from (
  select distinct fel_id, platform, coalesce(country, '') as country, app_version, c as step,
         split_part(coalesce(o, ''), '^', 1) as o1, f as unit,
         {{AD_TYPE_EXPR}} as ad_type, split_part(s, '^', 3) as placement, split_part(s, '^', 1) as lvl,
         case when replace(g, ',', '.') ~ '^[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?$' then cast(replace(g, ',', '.') as float) end as price,
         case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end as ts_ms
  from events
  where game_id = {{GAME_ID}} and k = 'ad_tracking_max' and c = 'revenue' and platform in (1, 2)
    and {{AD_TYPE_EXPR}} in ('interstitial', 'rewarded')
    and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
    and (event_timestamp between {{START_MS}} and {{END_MS}} or event_timestamp between {{START_S}} and {{END_S}})
) z
where ts_ms / 3600000 >= {{START_HR}}
;;
-- out: ih_ads
select platform, hr, ad_type, coalesce(placement, '') as placement,
       count(*) as imps, count(distinct fel_id) as players, sum(price) as rev
from ti_ad
group by 1, 2, 3, 4
;;
-- out: none
create temp table ti_blk as
select fel_id, platform, p as ad_type, split_part(coalesce(o, ''), '^', 1) as reason,
       (case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end) / 3600000 as hr
from events
where game_id = {{GAME_ID}} and k = 'ad_funnel_tracking' and c = 'show_blocked' and platform in (1, 2)
  and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
  and (event_timestamp between {{START_MS}} and {{END_MS}} or event_timestamp between {{START_S}} and {{END_S}})
;;
-- out: ih_blocked
select platform, hr, ad_type, reason, count(*) as n, count(distinct fel_id) as players
from ti_blk
where hr >= {{START_HR}}
group by 1, 2, 3, 4
;;
-- out: iw_blocked
select platform, ({{END_HR}} - 1 - hr) / 24 as w, reason, count(*) as n, count(distinct fel_id) as players
from ti_blk
where hr < {{END_HR}} and hr >= {{START_HR}} and ({{END_HR}} - 1 - hr) % 24 < {{WIN_HOURS}}
group by 1, 2, 3
;;
-- out: none
create temp table ti_play as
select fel_id, platform, c, coalesce(o, '') as o, ts_ms / 3600000 as hr
from (
  select fel_id, platform, c, o,
         case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end as ts_ms
  from events
  where game_id = {{GAME_ID}} and k = 'puzzle' and p = 'level_win' and platform in (1, 2)
    and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
    and (event_timestamp between {{START_MS}} and {{END_MS}} or event_timestamp between {{START_S}} and {{END_S}})
) z
where ts_ms / 3600000 >= {{START_HR}}
;;
-- out: ih_play
select platform, hr, count(distinct fel_id) as winners,
       count(distinct cast(fel_id as varchar) || '|' || coalesce(c, '') || '|' || o) as wins
from ti_play
group by 1, 2
;;
-- out: none
create temp table ti_arm as
select fel_id, p as exp_key, min(coalesce(c, '')) as arm
from (
  select fel_id, p, c
  from events
  where game_id = {{GAME_ID}} and k = 'experiment' and platform in (1, 2)
    and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
    and (event_timestamp between {{ARM_START_MS}} and {{END_MS}} or event_timestamp between {{ARM_START_S}} and {{END_S}})
) z
group by 1, 2
having count(distinct coalesce(c, '')) = 1
;;
-- out: iw_arm_act
select a.platform, ({{END_HR}} - 1 - a.hr) / 24 as w, r.exp_key, r.arm, count(distinct a.fel_id) as players
from ti_act a join ti_arm r on r.fel_id = a.fel_id
where a.hr < {{END_HR}} and ({{END_HR}} - 1 - a.hr) % 24 < {{WIN_HOURS}}
group by 1, 2, 3, 4
;;
-- out: iw_arm_ads
select a.platform, ({{END_HR}} - 1 - a.hr) / 24 as w, r.exp_key, r.arm, a.ad_type, coalesce(a.placement, '') as placement,
       count(*) as imps
from ti_ad a join ti_arm r on r.fel_id = a.fel_id
where a.hr < {{END_HR}} and ({{END_HR}} - 1 - a.hr) % 24 < {{WIN_HOURS}}
group by 1, 2, 3, 4, 5, 6
;;
-- out: iw_arm_blocked
select b.platform, ({{END_HR}} - 1 - b.hr) / 24 as w, r.exp_key, r.arm, b.ad_type, b.reason,
       count(*) as n, count(distinct b.fel_id) as players
from ti_blk b join ti_arm r on r.fel_id = b.fel_id
where b.hr < {{END_HR}} and b.hr >= {{START_HR}} and ({{END_HR}} - 1 - b.hr) % 24 < {{WIN_HOURS}}
group by 1, 2, 3, 4, 5, 6
;;
-- out: iw_arm_play
select p.platform, ({{END_HR}} - 1 - p.hr) / 24 as w, r.exp_key, r.arm,
       count(distinct cast(p.fel_id as varchar) || '|' || coalesce(p.c, '') || '|' || p.o) as wins
from ti_play p join ti_arm r on r.fel_id = p.fel_id
where p.hr < {{END_HR}} and ({{END_HR}} - 1 - p.hr) % 24 < {{WIN_HOURS}}
group by 1, 2, 3, 4
