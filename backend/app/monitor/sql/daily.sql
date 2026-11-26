-- Metric monitor: one job per game per run, executed in ONE Redshift session (temp tables are the
-- CLAUDE.md exception: session-scoped CREATE TEMP TABLE ... AS SELECT, nothing persistent).
-- Statements are separated by a line holding only ';;'. A statement headed '-- out: <name>'
-- streams its result back as <name>; '-- out: none' is a temp-table build.
--
-- Placeholders (filled by pull.py): {{GAME_ID}} {{ACT_START_MS}} {{ACT_START_S}} {{START_MS}}
-- {{START_S}} {{END_MS}} {{END_S}} {{START_DAY}} {{END_DAY}} {{ARM_START_DAY}} {{ARM_START_MS}} {{AD_TYPE_EXPR}}
-- {{ARM_START_S}} {{START_DATE}} {{END_DATE}} {{ADJUST_APP}} {{EXCLUDE_COUNTRIES}} {{MARKET_GAMES}}
--
-- Every date derives from event_timestamp normalised to milliseconds (events.event_date is corrupt);
-- day = ts_ms / 86400000 (UTC epoch day). Platform from events.platform (1 Android, 2 iOS).
-- Impression = one ad_tracking_max row with c = 'revenue' after dedup on the natural key.

-- out: none
create temp table t_act as
select fel_id, day, max(platform) as platform, max(coalesce(country, '')) as country, max(app_version) as app_version
from (
  select fel_id, platform, country, app_version,
         (case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end) / 86400000 as day
  from events
  where game_id = {{GAME_ID}} and k in ('session', 'dau') and platform in (1, 2)
    and (event_timestamp between {{ACT_START_MS}} and {{END_MS}} or event_timestamp between {{ACT_START_S}} and {{END_S}})
) z
group by 1, 2
;;
-- out: none
create temp table t_inst as
select f.fel_id, least(f.first_day, coalesce(u.u_day, 999999)) as inst_day
from (select fel_id, min(day) as first_day from t_act group by 1) f
left join (
  select fel_id, min(cast(extract(epoch from created_at) as bigint) / 86400) as u_day
  from users.users where game_id = {{GAME_ID}} group by 1
) u on u.fel_id = f.fel_id
;;
-- out: none
create temp table t_actw as
select a.fel_id, a.day, a.platform, a.country, a.app_version,
       case when i.inst_day is null then 'unknown'
            when a.day - i.inst_day <= 0 then 'D0'
            when a.day - i.inst_day <= 6 then 'D1-6'
            when a.day - i.inst_day <= 29 then 'D7-29'
            else 'D30+' end as age,
       case when a.country = 'US' then 'US'
            when a.country in ('GB','CA','AU','NZ','DE','FR','NL','SE','NO','DK','CH','AT','BE','IE','FI','JP','KR') then 'T1'
            else 'RoW' end as geo
from t_act a left join t_inst i on i.fel_id = a.fel_id
where a.day between {{START_DAY}} and {{END_DAY}} and a.country not in ({{EXCLUDE_COUNTRIES}})
;;
-- out: dau_cut
select platform, day, age, geo, app_version, count(*) as dau
from t_actw group by 1, 2, 3, 4, 5
;;
-- out: rolling_cut
select a.platform, a.day, count(*) as dau, sum(case when b.fel_id is not null then 1 else 0 end) as d1_returned
from t_actw a left join t_act b on b.fel_id = a.fel_id and b.day = a.day + 1
where a.day between {{START_DAY}} and {{END_DAY}} - 1
group by 1, 2
;;
-- out: none
create temp table t_ad as
select fel_id, platform, country, app_version, ad_type, step, o1, placement, lvl, price, ts_ms, ts_ms / 86400000 as day
from (
  select distinct fel_id, platform, coalesce(country, '') as country, app_version, c as step,
         split_part(coalesce(o, ''), '^', 1) as o1, f as unit,
         {{AD_TYPE_EXPR}} as ad_type, split_part(s, '^', 3) as placement,
         case when split_part(s, '^', 1) ~ '^[0-9]{1,6}$' then cast(split_part(s, '^', 1) as int) end as lvl,
         case when replace(g, ',', '.') ~ '^[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?$' then cast(replace(g, ',', '.') as float) end as price,
         case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end as ts_ms
  from events
  where game_id = {{GAME_ID}} and k = 'ad_tracking_max' and platform in (1, 2)
    and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
    and (({{AD_TYPE_EXPR}} in ('interstitial', 'rewarded') and c in ('request', 'load', 'load_fail', 'show_ad', 'view', 'revenue'))
         or ({{AD_TYPE_EXPR}} = 'banner' and c = 'revenue'))
    and (event_timestamp between {{START_MS}} and {{END_MS}} or event_timestamp between {{START_S}} and {{END_S}})
) z
;;
-- out: ad_cut
select a.platform, a.day, a.ad_type, coalesce(w.age, 'unknown') as age, coalesce(w.geo, 'unknown') as geo, a.app_version,
       coalesce(a.placement, '') as placement, a.o1 as network,
       case when a.lvl is null then 'unknown' when a.lvl < 16 then 'L0-15' when a.lvl < 80 then 'L16-79'
            when a.lvl < 500 then 'L80-499' when a.lvl < 1000 then 'L500-999' else 'L1000+' end as band,
       count(*) as imps, count(distinct a.fel_id) as ad_users, sum(a.price) as rev
from t_ad a left join t_actw w on w.fel_id = a.fel_id and w.day = a.day
where a.step = 'revenue' and a.ad_type in ('interstitial', 'rewarded') and a.day between {{START_DAY}} and {{END_DAY}}
group by 1, 2, 3, 4, 5, 6, 7, 8, 9
;;
-- out: banner_cut
select platform, day, o1 as network, app_version, count(*) as imps, count(distinct fel_id) as ad_users, sum(price) as rev
from t_ad
where step = 'revenue' and ad_type = 'banner' and day between {{START_DAY}} and {{END_DAY}}
group by 1, 2, 3, 4
;;
-- out: funnel_cut
select platform, day, ad_type, step, o1, count(*) as n, count(distinct fel_id) as players
from t_ad
where ad_type in ('interstitial', 'rewarded') and day between {{START_DAY}} and {{END_DAY}}
group by 1, 2, 3, 4, 5
;;
-- out: hour_cut
select platform, day, (ts_ms % 86400000) / 3600000 as hr, ad_type, count(*) as imps, count(distinct fel_id) as players
from t_ad
where step = 'revenue' and day between {{END_DAY}} - 7 and {{END_DAY}} + 1
group by 1, 2, 3, 4
;;
-- out: none
create temp table t_blk as
select fel_id, platform, p as ad_type, split_part(coalesce(o, ''), '^', 1) as reason,
       (case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end) / 86400000 as day
from events
where game_id = {{GAME_ID}} and k = 'ad_funnel_tracking' and c = 'show_blocked' and platform in (1, 2)
  and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
  and (event_timestamp between {{START_MS}} and {{END_MS}} or event_timestamp between {{START_S}} and {{END_S}})
;;
-- out: blocked_cut
select platform, day, ad_type, reason, count(*) as n, count(distinct fel_id) as players
from t_blk group by 1, 2, 3, 4
;;
-- out: exp_cut
select platform, day, k as kind, p as exp_key, coalesce(c, '') as arm, count(distinct fel_id) as players
from (
  select fel_id, platform, k, p, c,
         (case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end) / 86400000 as day
  from events
  where game_id = {{GAME_ID}} and k in ('experiment', 'config') and platform in (1, 2)
    and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
    and (event_timestamp between {{START_MS}} and {{END_MS}} or event_timestamp between {{START_S}} and {{END_S}})
) z
group by 1, 2, 3, 4, 5
;;
-- out: none
create temp table t_play as
select fel_id, platform, p, c, coalesce(o, '') as o, ts_ms, ts_ms / 86400000 as day
from (
  select fel_id, platform, p, c, o,
         case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end as ts_ms
  from events
  where game_id = {{GAME_ID}} and k = 'puzzle' and p in ('level_start', 'level_win') and platform in (1, 2)
    and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
    and (event_timestamp between {{START_MS}} and {{END_MS}} or event_timestamp between {{START_S}} and {{END_S}})
) z
;;
-- out: play_cut
select p.platform, p.day, coalesce(w.age, 'unknown') as age, coalesce(w.app_version, p_ver.app_version, '') as app_version,
       count(distinct case when p.p = 'level_start' then p.fel_id end) as starters,
       count(distinct case when p.p = 'level_start' then cast(p.fel_id as varchar) || '|' || coalesce(p.c, '') || '|' || cast(p.ts_ms as varchar) end) as starts,
       count(distinct case when p.p = 'level_win' then p.fel_id end) as winners,
       count(distinct case when p.p = 'level_win' then cast(p.fel_id as varchar) || '|' || coalesce(p.c, '') || '|' || p.o end) as wins
from t_play p
left join t_actw w on w.fel_id = p.fel_id and w.day = p.day
left join (select fel_id, day, max(app_version) as app_version from t_act group by 1, 2) p_ver on p_ver.fel_id = p.fel_id and p_ver.day = p.day
where p.day between {{START_DAY}} and {{END_DAY}}
group by 1, 2, 3, 4
;;
-- out: iap_cut
select platform, day, count(*) as purchases, count(distinct fel_id) as buyers
from (
  select fel_id, platform,
         (case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end) / 86400000 as day
  from events
  where game_id = {{GAME_ID}} and k = 'store' and p = 'purchase_success' and platform in (1, 2)
    and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
    and (event_timestamp between {{START_MS}} and {{END_MS}} or event_timestamp between {{START_S}} and {{END_S}})
) z
group by 1, 2
;;
-- out: market_cut
select game_id, platform, day, ad_type, case when country = 'US' then 'US' else 'other' end as geo, count(*) as imps, sum(price) as rev
from (
  select distinct game_id, fel_id, platform, coalesce(country, '') as country, c, o, f, s,
         {{AD_TYPE_EXPR}} as ad_type,
         case when replace(g, ',', '.') ~ '^[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?$' then cast(replace(g, ',', '.') as float) end as price,
         case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end as ts_ms,
         (case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end) / 86400000 as day
  from events
  where game_id in ({{MARKET_GAMES}}) and k = 'ad_tracking_max' and c = 'revenue' and platform in (1, 2)
    and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
    and (event_timestamp between {{START_MS}} and {{END_MS}} or event_timestamp between {{START_S}} and {{END_S}})
) z
where ad_type in ('banner', 'interstitial', 'rewarded') and day between {{START_DAY}} and {{END_DAY}}
group by 1, 2, 3, 4, 5
;;
-- out: adjust_cut
select day, lower(os_name) as os_name, sum(installs) as installs, sum(cost) as cost
from adjust.adjust_performance
where app = '{{ADJUST_APP}}' and day between date '{{START_DATE}}' and date '{{END_DATE}}'
group by 1, 2
;;
-- out: attrib_cut
-- In-game attribution (Adjust SDK callback, source = client): one install row per player. Platform
-- from the player's own activity rows (attribution carries none); install day = the row's creation
-- day (installed_at is null on client rows). paid = any network except organic / unattributed.
select coalesce(t.platform, 0) as platform,
       datediff(day, date '1970-01-01', cast(a.created_at at time zone 'UTC' as date)) as day,
       case when lower(coalesce(nullif(btrim(a.network_name), ''), '')) in ('', 'organic', 'unattributed', '(none)') then 0 else 1 end as paid,
       coalesce(nullif(btrim(a.network_name), ''), '(none)') as network,
       count(*) as installs
from attribution.events a
join users.users u on u.fel_id = a.fel_id and u.game_id = {{GAME_ID}}
left join (select fel_id, max(platform) as platform from t_act group by 1) t on t.fel_id = a.fel_id
where a.source = 'client' and a.attribution_type = 'install'
  and a.created_at >= timestamp '{{START_DATE}}' and a.created_at < timestamp '{{END_DATE}}' + interval '1 day'
group by 1, 2, 3, 4
;;
-- out: none
create temp table t_arm as
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
-- out: arm_dau
select w.platform, w.day, r.exp_key, r.arm, count(*) as dau
from t_actw w join t_arm r on r.fel_id = w.fel_id
where w.day between {{ARM_START_DAY}} and {{END_DAY}}
group by 1, 2, 3, 4
;;
-- out: arm_ads
select a.platform, a.day, r.exp_key, r.arm, a.ad_type, coalesce(a.placement, '') as placement, count(*) as imps, sum(a.price) as rev
from t_ad a join t_arm r on r.fel_id = a.fel_id
where a.step = 'revenue' and a.ad_type in ('interstitial', 'rewarded') and a.day between {{ARM_START_DAY}} and {{END_DAY}}
group by 1, 2, 3, 4, 5, 6
;;
-- out: arm_blocked
select b.platform, b.day, r.exp_key, r.arm, b.ad_type, b.reason, count(*) as n, count(distinct b.fel_id) as players
from t_blk b join t_arm r on r.fel_id = b.fel_id
where b.day between {{ARM_START_DAY}} and {{END_DAY}}
group by 1, 2, 3, 4, 5, 6
;;
-- out: arm_play
select p.platform, p.day, r.exp_key, r.arm,
       count(distinct case when p.p = 'level_win' then cast(p.fel_id as varchar) || '|' || coalesce(p.c, '') || '|' || p.o end) as wins
from t_play p join t_arm r on r.fel_id = p.fel_id
where p.day between {{ARM_START_DAY}} and {{END_DAY}}
group by 1, 2, 3, 4
