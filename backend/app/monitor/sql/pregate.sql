-- Metric monitor pre-gate: ONE job over every configured game, run before the per-game pulls, so a
-- game that fails the gates (revenue, active UA) costs one shared scan instead of its own 3-minute
-- pull. Same session/guard rules as daily.sql. Placeholders: {{GAME_IDS}} {{ADJUST_APPS}}
-- {{AD_TYPE_EXPR}} {{START_MS}} {{START_S}} {{END_MS}} {{END_S}} {{START_DAY}} {{END_DAY}}
-- {{START_DATE}} {{END_DATE}} {{EXCLUDE_COUNTRIES}}

-- out: pg_revenue
select game_id, platform, ts_ms / 86400000 as day, ad_type, count(*) as imps, sum(price) as rev
from (
  select distinct game_id, fel_id, platform, c, o, f, s,
         {{AD_TYPE_EXPR}} as ad_type,
         case when replace(g, ',', '.') ~ '^[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?$' then cast(replace(g, ',', '.') as float) end as price,
         case when event_timestamp >= 100000000000 then event_timestamp else event_timestamp * 1000 end as ts_ms
  from events
  where game_id in ({{GAME_IDS}}) and k = 'ad_tracking_max' and c = 'revenue' and platform in (1, 2)
    and coalesce(country, '') not in ({{EXCLUDE_COUNTRIES}})
    and (event_timestamp between {{START_MS}} and {{END_MS}} or event_timestamp between {{START_S}} and {{END_S}})
) z
where ts_ms / 86400000 between {{START_DAY}} and {{END_DAY}}
group by 1, 2, 3, 4
;;
-- out: pg_adjust
select app, lower(os_name) as os_name, day, sum(installs) as installs, sum(cost) as cost
from adjust.adjust_performance
where app in ({{ADJUST_APPS}}) and day between date '{{START_DATE}}' and date '{{END_DATE}}'
group by 1, 2, 3
;;
-- out: pg_attrib
select u.game_id,
       datediff(day, date '1970-01-01', cast(a.created_at at time zone 'UTC' as date)) as day,
       case when lower(coalesce(nullif(btrim(a.network_name), ''), '')) in ('', 'organic', 'unattributed', '(none)') then 0 else 1 end as paid,
       count(*) as installs
from attribution.events a
join users.users u on u.fel_id = a.fel_id and u.game_id in ({{GAME_IDS}})
where a.source = 'client' and a.attribution_type = 'install'
  and a.created_at >= timestamp '{{START_DATE}}' and a.created_at < timestamp '{{END_DATE}}' + interval '1 day'
group by 1, 2, 3
