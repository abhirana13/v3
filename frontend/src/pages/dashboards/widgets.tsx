import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import type { ChartWidgetData, ChartWidgetConfig, NumberWidgetData, WidgetMetricSel } from '../../api/types'

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

export function compactNum(v: number, decimals = 2): string {
  const a = Math.abs(v)
  if (a >= 1e6) return (v / 1e6).toFixed(2) + 'M'
  if (a >= 1e3) return (v / 1e3).toFixed(2) + 'K'
  if (Number.isInteger(v)) return String(v)
  return v.toFixed(Math.min(decimals, 2))
}

const withUnit = (v: string, unit: string | null) => (unit ? `${unit} ${v}` : v)

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

export function WidgetChrome({ title, asOf, onExpand, infoText, leading, trailing, children }: {
  title: string; asOf?: string; onExpand?: () => void; infoText?: string
  leading?: React.ReactNode // edit mode: drag handle
  trailing?: React.ReactNode // edit mode: gear + … menu (replaces the default icons)
  children: React.ReactNode
}) {
  return (
    <div className="flex h-full w-full flex-col rounded-lg border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <div className="flex items-start gap-2 px-4 pt-3">
        {leading}
        <div className="min-w-0">
          <div className="truncate text-[14px] font-bold text-slate-800">{title}</div>
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
            <span className="text-[34px] font-semibold leading-none tracking-tight text-slate-900">
              {withUnit(compactNum(data.value, data.decimals), data.unit)}
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
                  <span className={'ml-auto font-medium tabular-nums ' + (up ? 'text-emerald-600' : 'text-rose-600')}>
                    {(delta.abs >= 0 ? '+' : '-') + compactNum(Math.abs(delta.abs), data.decimals)}
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

const PALETTE = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444', '#0ea5e9', '#84cc16', '#f97316', '#14b8a6', '#a855f7']
const SEP = ''
const MAX_SERIES = 20

interface BuiltSeries { name: string; metric: string; data: (number | null)[] }

function buildSeries(data: ChartWidgetData): { dates: string[]; series: BuiltSeries[]; truncated: boolean } {
  const timeCol = data.time_column
  const dimCols = data.dimension_columns
  const dates = [...new Set(data.rows.map((r) => String(r[timeCol])))].sort()
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
  const truncated = all.length > MAX_SERIES
  const series = all.slice(0, MAX_SERIES).map((s) => ({
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

  const built = useMemo(() => (data && data.rows.length ? buildSeries(data) : null), [data])
  const asOf = data && data.from_date ? `${data.from_date} → ${data.to_date}` : undefined

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

    const axis = (which: 'primary' | 'secondary') => ({
      type: 'value' as const,
      scale: true,
      min: yCfg[which]?.min,
      max: yCfg[which]?.max,
      splitLine: { show: which === 'primary', lineStyle: { color: '#f1f5f9' } },
      axisLabel: {
        color: '#94a3b8', fontSize: 10,
        formatter: (v: number) => (Math.abs(v) >= 1000 ? v / 1000 + 'K' : String(v)),
      },
    })

    chart.setOption({
      animationDuration: 200,
      // extra right margin so the last x-axis date label (centered on the final
      // point) isn't flush against the card edge
      grid: { left: 46, right: hasSecondary ? 54 : 30, top: 12, bottom: built.series.length > 1 ? 42 : 24, containLabel: false },
      tooltip: { trigger: 'axis', backgroundColor: '#fff', borderColor: '#e2e8f0', textStyle: { color: '#334155', fontSize: 12 }, axisPointer: { label: { formatter: (p: any) => fmtAxisDate(String(p.value)) } } },
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
        lineStyle: { width: 1.6, color: PALETTE[i % PALETTE.length] },
        itemStyle: { color: PALETTE[i % PALETTE.length] },
        connectNulls: false,
        ...(i === 0 && config.target != null
          ? {
              markLine: {
                silent: true, symbol: 'none',
                lineStyle: { color: '#f59e0b', type: 'dashed', width: 1.2 },
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
      asOf={asOf}
      onExpand={onExpand}
      infoText={built?.truncated ? `showing first ${MAX_SERIES} series` : undefined}
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
