import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import type { ChartWidgetData, ChartWidgetConfig, NumberWidgetData, WidgetMetricSel } from '../../api/types'
import { naturalCompare } from '../chart/transforms'
import { TARGET_LINE_COLOR, maxSeries, seriesColor } from '../../charts/palette'
import { axisDecimals, compactAxis, deltaAffix, formatValue, unitAffix, unitCarriesMagnitude } from '../../charts/format'
import { buildChartLink } from '../chart/urlState'

/* Widget cards for the dashboard grid, ported from the Claude Design handoff
   (dashboard.jsx): WidgetChrome + NumberWidget + ChartWidgetCard. Dumb — data
   via props, actions via callbacks. */

const Ic = {
  expand: (p: any) => (<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M15 4h5v5M9 20H4v-5M20 4l-6 6M4 20l6-6" /></svg>),
  info: (p: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 7.5v.5" /></svg>),
  up: (p: any) => (<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M2 11l4-4 3 2.5L14 4" /><path d="M10 4h4v4" /></svg>),
  down: (p: any) => (<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M2 5l4 4 3-2.5L14 12" /><path d="M10 12h4V8" /></svg>),
}

/* ---------- formatting ---------- */

export function compactNum(v: number, decimals = 2, magnitude = true): string {
  const a = Math.abs(v)
  // `magnitude` off when the metric's UNIT already carries one (see unitCarriesMagnitude) —
  // otherwise a metric measured in 'k' rendered "1.23Kk".
  if (magnitude && a >= 1e6) return (v / 1e6).toFixed(2) + 'M'
  if (magnitude && a >= 1e3) return (v / 1e3).toFixed(2) + 'K'
  if (Number.isInteger(v)) return String(v)
  return v.toFixed(Math.min(decimals, 2))
}


// trailing mean over `window` buckets — same view transform as the chart view
function movingAverage(data: (number | null)[], window: number): (number | null)[] {
  return data.map((_, i) => {
    let sum = 0, n = 0
    for (let j = Math.max(0, i - window + 1); j <= i; j++) {
      const v = data[j]
      if (typeof v === 'number') { sum += v; n++ }
    }
    return n > 0 ? sum / n : null
  })
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
// compact axis label: "2026-07-14" -> "Jul 14" (full ISO clutters + clips at the edge)
function fmtAxisDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  return m ? `${MONTHS[+m[2] - 1]} ${+m[3]}` : iso
}

/* ---------- chrome ---------- */

export function WidgetChrome({ title, asOf, onExpand, infoText, titleHref, leading, trailing, children }: {
  title: string; asOf?: string; onExpand?: () => void; infoText?: string
  // when set, the widget NAME is a link to the source chart on this widget's cuts. A real <a>
  // rather than an onClick so middle-click / cmd-click open a new tab the way a link should.
  titleHref?: string
  leading?: React.ReactNode // edit mode: drag handle
  trailing?: React.ReactNode // edit mode: gear + … menu (replaces the default icons)
  children: React.ReactNode
}) {
  return (
    <div className="flex h-full w-full flex-col rounded-lg border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <div className="flex items-start gap-2 px-4 pt-3">
        {leading}
        <div className="min-w-0">
          {titleHref ? (
            <a href={titleHref} target="_blank" rel="noopener noreferrer"
              title="Open the source chart on these cuts"
              className="block truncate text-[14px] font-bold text-slate-800 hover:text-sky-600 hover:underline">
              {title}
            </a>
          ) : (
            <div className="truncate text-[14px] font-bold text-slate-800">{title}</div>
          )}
          {asOf && <div className="mt-0.5 text-[11px] text-slate-400">{asOf}</div>}
        </div>
        <div className="ml-auto flex items-center gap-0.5 text-slate-400">
          {trailing || (
            <>
              {onExpand && (
                <button onClick={onExpand} title="Open source chart" className="flex h-6 w-6 items-center justify-center rounded hover:bg-slate-100 hover:text-slate-600"><Ic.expand /></button>
              )}
              {infoText && (
                <span title={infoText} className="flex h-6 w-6 cursor-help items-center justify-center rounded hover:bg-slate-100 hover:text-slate-600"><Ic.info /></span>
              )}
            </>
          )}
        </div>
      </div>
      {children}
    </div>
  )
}

function BodyState({ loading, error }: { loading: boolean; error: string | null }) {
  return (
    <div className="flex flex-1 items-center justify-center px-4 pb-4">
      {error
        ? <span className="text-center text-[12px] text-rose-500">{error}</span>
        : <span className="text-[12px] text-slate-300">{loading ? 'Loading…' : 'No data'}</span>}
    </div>
  )
}

/* ---------- number tile ---------- */

const COMPARE_LABELS: Record<string, string> = {
  previous_day: 'vs previous day',
  last_week: 'vs last week',
}

export function NumberWidget({ title, data, loading, error, onExpand, leading, trailing }: {
  title: string; data: NumberWidgetData | null; loading: boolean; error: string | null
  onExpand?: () => void; leading?: React.ReactNode; trailing?: React.ReactNode
}) {
  const asOf = data ? `as of ${data.as_of_date}` : undefined
  return (
    <WidgetChrome title={title} asOf={asOf} onExpand={onExpand} leading={leading} trailing={trailing}>
      {!data || data.value == null ? (
        <BodyState loading={loading} error={error} />
      ) : (
        <>
          <div className="flex min-h-0 flex-1 items-center justify-center px-4">
            {/* The unit is its own element so it can be sized down — rendering it into the
                string forced it to the full 34px, and `withUnit` also PREFIXED every unit, so a
                percentage metric read "% 49.57". Placement now follows unitAffix(). */}
            <span className="text-[34px] font-semibold leading-none tracking-tight text-slate-900">
              {unitAffix(data.unit).prefix}
              {compactNum(data.value, data.decimals, !unitCarriesMagnitude(data.unit))}
              {unitAffix(data.unit).suffix && (
                <span className={'text-[22px] font-semibold ' + (unitAffix(data.unit).spaced ? 'ml-1' : 'ml-[1px]')}>
                  {unitAffix(data.unit).suffix}
                </span>
              )}
            </span>
          </div>
          <div className="flex flex-col gap-1.5 px-4 pb-3.5">
            {Object.entries(data.compares).map(([key, delta]) => {
              const label = COMPARE_LABELS[key] || key
              if (!delta) {
                return (
                  <div key={key} className="flex items-center gap-2 text-[12px]">
                    <span className="rounded bg-slate-50 px-1.5 py-0.5 font-medium text-slate-300">—</span>
                    <span className="text-slate-400">{label}</span>
                  </div>
                )
              }
              const up = (delta.pct ?? delta.abs) >= 0
              return (
                <div key={key} className="flex items-center gap-2 text-[12px]">
                  <span className={'inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-semibold ' + (up ? 'bg-emerald-50 text-emerald-600' : 'bg-rose-50 text-rose-600')}>
                    {up ? <Ic.up /> : <Ic.down />}
                    {delta.pct == null ? 'n/a' : `${Math.abs(delta.pct).toFixed(2)}%`}
                  </span>
                  <span className="text-slate-500">{label}</span>
                  {/* The ABSOLUTE delta wears the metric's unit — it was bare, so a currency
                      tile showed "+17.25" with no indication of what. For a percentage metric the
                      affix is 'pp', not '%': this sits beside a pill that already shows the
                      RELATIVE change, and two adjacent percentages meaning different things is
                      how you get a 17-point move read as a 17% one. Sign leads the prefix so
                      currency reads "+$17.25". */}
                  <span className={'ml-auto font-medium tabular-nums ' + (up ? 'text-emerald-600' : 'text-rose-600')}>
                    {delta.abs >= 0 ? '+' : '-'}
                    {deltaAffix(data.unit).prefix}
                    {compactNum(Math.abs(delta.abs), data.decimals, !unitCarriesMagnitude(data.unit))}
                    {deltaAffix(data.unit).suffix
                      ? (deltaAffix(data.unit).spaced ? ' ' : '') + deltaAffix(data.unit).suffix
                      : ''}
                  </span>
                </div>
              )
            })}
          </div>
        </>
      )}
    </WidgetChrome>
  )
}

/* ---------- chart card ---------- */

// Colours come from charts/palette.ts, shared with the chart view — these used to be a DIFFERENT
// ten-colour list, so the same chart was coloured one way here and another way on its own page.
const SEP = ''

interface BuiltSeries { name: string; metric: string; data: (number | null)[] }

function buildSeries(data: ChartWidgetData, cap: number): { dates: string[]; series: BuiltSeries[]; truncated: boolean } {
  // ROW KEY: the pivot column when the widget is pivoted, else the time column. A pivoted
  // widget used to be impossible — ChartWidgetConfig.x_axis was Literal["time"] — so this always
  // read time_column and a cohort chart flattened to dates inside a dashboard.
  const timeCol = data.x_axis_column || data.time_column
  // The pivot dimension is the AXIS, not a series split, so it must not also become part of the
  // series combo — otherwise every x-value would spawn its own single-point series.
  const dimCols = (data.dimension_columns || []).filter((c) => c !== data.x_axis_column)
  // value_order of each grouped dimension (aligned with dimension_columns) and the metric
  // order the widget requested — both drive DISPLAY order below.
  // value_order aligned with the SPLIT dims (pivot column dropped, same as dimCols above)
  const valueOrder = (data.dimension_value_order || []).filter(
    (_, i) => (data.dimension_columns || [])[i] !== data.x_axis_column,
  )
  const metricOrder = data.metrics
  // naturalCompare, not .sort(): a widget on a PIVOTED chart puts a dimension value in
  // the x slot, and string order gives "0, 1, 10, 11 ... 19, 2, 20" for a level number
  // and scrambles the D2-D7 / D8-D14 / D15-D30 cohort buckets. ISO dates are unaffected.
  const dates = [...new Set(data.rows.map((r) => String(r[timeCol])))].sort(naturalCompare)
  const dateIdx = new Map(dates.map((d, i) => [d, i]))

  // one series per metric × dimension-value combo
  const byKey = new Map<string, { metric: string; combo: string; values: (number | null)[] }>()
  for (const row of data.rows) {
    const combo = dimCols.map((c) => String(row[c])).join(' · ')
    for (const m of data.metrics) {
      const key = m + SEP + combo
      let s = byKey.get(key)
      if (!s) {
        s = { metric: m, combo, values: dates.map(() => null) }
        byKey.set(key, s)
      }
      const v = row[m]
      s.values[dateIdx.get(String(row[timeCol]))!] = v == null ? null : Number(v)
    }
  }
  const all = [...byKey.values()]

  // Rank by total so the CAP keeps the biggest series, not whichever arrived first. `all` is in
  // row-arrival order, i.e. the backend's (time bucket, dim) ordering, so slicing it directly
  // dropped series essentially at random — a widget could hide its largest cut and plot 20
  // minor ones. Nulls count as 0; a series that is entirely null ranks last.
  const total = (s: { values: (number | null)[] }): number =>
    s.values.reduce<number>((acc, v) => acc + (typeof v === 'number' ? v : 0), 0)

  // `cap` is one series per AVAILABLE colour (see charts/palette.ts) — 19 rather than 20 when a
  // target line has claimed the reserved amber. Truncating is the honest failure here: the widget
  // surfaces it via infoText, whereas wrapping the palette would draw two series identically.
  const truncated = all.length > cap
  const kept = truncated ? [...all].sort((a, b) => total(b) - total(a)).slice(0, cap) : all

  // DISPLAY order is a separate concern from which series survive: order the kept set the way
  // the chart page does, by the PRIMARY grouped dimension's value_order, so a widget legend
  // reads D0, D1, D2-D7 ... instead of the order rows happened to arrive in. 'metric' means
  // biggest-first; anything else is a number-aware label sort. Series are keyed by
  // "metric · combo", so with several metrics the metric name leads and cuts group under it —
  // matching the chart's `multi` labelling.
  const primaryOrder = (valueOrder && valueOrder[0]) || 'natural'
  const ordered = [...kept].sort((a, b) =>
    a.metric !== b.metric
      ? metricOrder.indexOf(a.metric) - metricOrder.indexOf(b.metric)
      : primaryOrder === 'metric'
        ? total(b) - total(a) || naturalCompare(a.combo, b.combo)
        : naturalCompare(a.combo, b.combo),
  )

  const series = ordered.map((s) => ({
    name: s.combo ? `${s.metric} · ${s.combo}` : s.metric,
    metric: s.metric,
    data: s.values,
  }))
  return { dates, series, truncated }
}

export function ChartWidgetCard({ title, data, config, loading, error, onExpand, leading, trailing, movingAvgWindow }: {
  title: string
  data: ChartWidgetData | null
  config: Partial<ChartWidgetConfig>
  loading: boolean
  error: string | null
  onExpand?: () => void
  leading?: React.ReactNode
  trailing?: React.ReactNode
  movingAvgWindow?: number | null // when set, plot each series as its trailing mean
}) {
  const elRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  // bumped when the instance is (re)created so the setOption effect re-runs —
  // init is async (waits for the grid cell to get a real size), so without this
  // a chart whose data arrived first would stay blank
  const [inited, setInited] = useState(0)

  // A target line claims the reserved amber, so it also lowers how many series may be drawn.
  const hasTarget = config.target != null
  const seriesCap = maxSeries(hasTarget)
  const built = useMemo(
    () => (data && data.rows.length ? buildSeries(data, seriesCap) : null),
    [data, seriesCap],
  )
  const asOf = data && data.from_date ? `${data.from_date} → ${data.to_date}` : undefined

  // Link to the source chart showing THE SAME cuts. Built from the response rather than from
  // `config` so it reflects what was actually served — the global filter cascade, the inherited
  // x-axis and the offset-capped window are all resolved server-side and only appear here.
  const chartHref = useMemo(() => {
    if (!data) return undefined
    return buildChartLink(data.chart_id, {
      metrics: data.metrics,
      // the pivot dimension is the AXIS, not a split — passing it as a split would add a series
      split: (data.dimensions || []).filter((d) => d !== data.x_axis),
      filters: data.filters_effective,
      from: data.from_date || undefined,
      to: data.to_date || undefined,
      granularity: data.granularity,
      // '' forces a plain time series, so a chart whose own default is a pivot still opens the
      // way this widget drew it
      xAxis: data.x_axis ?? '',
    })
  }, [data])

  // init + keep sized to the grid cell (react-grid-layout resizes the cell).
  // Keyed on !!built because the target div only MOUNTS once data exists — an
  // effect that ran only at first mount would find elRef null (data arrives
  // after mount on the normal read path) and the chart would stay blank forever.
  useEffect(() => {
    const el = elRef.current
    if (!el) return
    const ensure = () => {
      const w = el.clientWidth, h = el.clientHeight
      if (w <= 0 || h <= 0) return
      if (!chartRef.current) {
        chartRef.current = echarts.init(el, undefined, { renderer: 'canvas', width: w, height: h })
        setInited((n) => n + 1)
      } else {
        chartRef.current.resize({ width: w, height: h })
      }
    }
    ensure()
    const ro = new ResizeObserver(ensure)
    ro.observe(el)
    return () => { ro.disconnect(); chartRef.current?.dispose(); chartRef.current = null }
  }, [!!built])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !built) return
    const plotted = movingAvgWindow && movingAvgWindow > 1
      ? built.series.map((s) => ({ ...s, data: movingAverage(s.data, movingAvgWindow) }))
      : built.series
    const metricAxis = new Map<string, 'primary' | 'secondary'>()
    for (const m of (config.metrics || []) as WidgetMetricSel[]) metricAxis.set(m.name, m.y_axis)
    const hasSecondary = plotted.some((s) => metricAxis.get(s.metric) === 'secondary')
    const yCfg = config.y_axis || {}

    // Label precision per axis, from the values actually on it (charts/format.ts). Computed per
    // axis rather than once, because a secondary axis usually lives on a different scale.
    const valsOn = (which: 'primary' | 'secondary') => {
      const out: number[] = []
      for (const s of plotted) {
        if ((metricAxis.get(s.metric) ?? 'primary') !== which) continue
        for (const v of s.data) if (typeof v === 'number') out.push(v)
      }
      return out
    }
    const axisDecOf: Record<'primary' | 'secondary', number> = {
      primary: axisDecimals(valsOn('primary')),
      secondary: axisDecimals(valsOn('secondary')),
    }
    // ECharts' axis tooltip hands the formatter each point but not its series' metric, so map
    // the series NAME back to its metric to pick up that metric's unit/decimals.
    const metricOfSeries = new Map(plotted.map((s) => [s.name, s.metric]))
    const fmtFor = (seriesName: string) =>
      (data?.metric_format || {})[metricOfSeries.get(seriesName) ?? ''] || {}

    const axis = (which: 'primary' | 'secondary') => ({
      type: 'value' as const,
      scale: true,
      min: yCfg[which]?.min,
      max: yCfg[which]?.max,
      splitLine: { show: which === 'primary', lineStyle: { color: '#f1f5f9' } },
      axisLabel: {
        color: '#94a3b8', fontSize: 10,
        // Shared with the chart view (charts/format.ts). This was `v / 1000 + 'K'`, which
        // never rounded — 1234.5678 rendered as "1.2345678K" — and had fixed precision, so a
        // tight range near 1.0 collapsed every gridline to "1".
        formatter: (v: number) => compactAxis(v, axisDecOf[which]),
      },
    })

    chart.setOption({
      animationDuration: 200,
      // extra right margin so the last x-axis date label (centered on the final
      // point) isn't flush against the card edge
      grid: { left: 46, right: hasSecondary ? 54 : 30, top: 12, bottom: built.series.length > 1 ? 42 : 24, containLabel: false },
      tooltip: {
        trigger: 'axis', backgroundColor: '#fff', borderColor: '#e2e8f0',
        textStyle: { color: '#334155', fontSize: 12 },
        axisPointer: { label: { formatter: (p: any) => fmtAxisDate(String(p.value)) } },
        // A full formatter, not valueFormatter: the latter receives the value without its
        // series, so it cannot pick the right metric's unit/decimals. Mirrors the chart view's
        // categorical tooltip. Without this the default printed a raw float — a revenue widget
        // showed no '$' and ignored the metric's decimals entirely.
        formatter: (ps: any) => {
          if (!ps || !ps.length) return ''
          let out = `<div style="font-weight:600;margin-bottom:4px;color:#475569">${fmtAxisDate(String(ps[0].axisValue))}</div>`
          // biggest first, missing last — matching the chart view's tooltips
          const sorted = [...ps].sort((a: any, b: any) => {
            const av = typeof a.data === 'number' ? a.data : null
            const bv = typeof b.data === 'number' ? b.data : null
            if (av == null || bv == null) return (av == null ? 1 : 0) - (bv == null ? 1 : 0)
            return bv - av || naturalCompare(String(a.seriesName), String(b.seriesName))
          })
          for (const p of sorted) {
            const val = typeof p.data === 'number' ? formatValue(p.data, fmtFor(p.seriesName)) : '—'
            out += `<div style="display:flex;align-items:center;gap:6px"><span style="width:8px;height:8px;border-radius:99px;background:${p.color}"></span><span style="color:#475569">${p.seriesName}</span><span style="margin-left:auto;font-weight:600;color:#0f172a">${val}</span></div>`
          }
          return out
        },
      },
      legend: built.series.length > 1
        ? { bottom: 0, type: 'scroll', icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 10, color: '#64748b' } }
        : undefined,
      xAxis: {
        type: 'category', boundaryGap: config.viz === 'bar',
        data: built.dates,
        axisLine: { lineStyle: { color: '#e2e8f0' } }, axisTick: { show: false },
        axisLabel: { color: '#94a3b8', fontSize: 10, interval: Math.max(0, Math.floor(built.dates.length / 7) - 1), hideOverlap: true, formatter: (v: string) => fmtAxisDate(v) },
      },
      yAxis: hasSecondary ? [axis('primary'), axis('secondary')] : [axis('primary')],
      series: plotted.map((s, i) => ({
        name: s.name,
        type: config.viz === 'bar' ? 'bar' : 'line',
        data: s.data,
        yAxisIndex: hasSecondary && metricAxis.get(s.metric) === 'secondary' ? 1 : 0,
        symbol: 'circle', symbolSize: 3, showSymbol: false,
        lineStyle: { width: 1.6, color: seriesColor(i, hasTarget) },
        itemStyle: { color: seriesColor(i, hasTarget) },
        // 2.6px on hover-focus, matching the chart view — with 20 similar hues on one plot,
        // thickening the focused line is how you confirm which series the tooltip describes.
        emphasis: { focus: 'series' as const, lineStyle: { width: 2.6 } },
        connectNulls: false,
        ...(i === 0 && config.target != null
          ? {
              markLine: {
                silent: true, symbol: 'none',
                lineStyle: { color: TARGET_LINE_COLOR, type: 'dashed', width: 1.2 },
                label: { formatter: compactNum(config.target), position: 'insideEndTop', color: '#d97706', fontSize: 10 },
                data: [{ yAxis: config.target }],
              },
            }
          : {}),
      })),
    }, true)
  }, [built, config, inited, movingAvgWindow])

  return (
    <WidgetChrome
      title={title + (config.target != null ? ` (target: ${compactNum(config.target)})` : '')}
      titleHref={chartHref}
      asOf={asOf}
      onExpand={onExpand}
      infoText={built?.truncated ? `showing first ${seriesCap} series` : undefined}
      leading={leading}
      trailing={trailing}
    >
      {!built ? (
        <BodyState loading={loading} error={error} />
      ) : (
        <div ref={elRef} className="min-h-0 flex-1 px-1 pb-2" />
      )}
    </WidgetChrome>
  )
}
