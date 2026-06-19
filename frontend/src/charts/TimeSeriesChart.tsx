import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import type { ChartRow, UISeries } from '../components/types'
import { HoverCard } from './HoverCard'
import type { HoverRow } from './HoverCard'
import type { ChartOptions } from '../components/types'

const DEFAULT_DISPLAY: ChartOptions = { showLegend: true, smooth: false, showPoints: false, connectNulls: false, gridlines: true, zeroBase: false, logScale: false }

/* ECharts line/area/bar time series with primary/secondary Y axes. The built-in
   tooltip content is disabled (axisPointer crosshair only); a React HoverCard
   shows per-series value + DoD/WoW deltas, positioned beside the cursor. */

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const fmtDate = (iso: string) => {
  const d = new Date(iso + 'T00:00:00')
  return `${MON[d.getMonth()]} ${String(d.getDate()).padStart(2, '0')}'${String(d.getFullYear()).slice(2)}`
}
const fmtVal = (v: number, s: UISeries) => {
  const u = s.unit && s.unit !== 'None' ? s.unit : ''
  const dp = s.decimals ?? 0
  return (u === '$' ? '$' : '') + v.toLocaleString(undefined, { maximumFractionDigits: dp, minimumFractionDigits: 0 }) + (u === '%' ? '%' : '')
}
const numOr = (v: number | string | null | undefined): number | null => (typeof v === 'number' ? v : null)

const toISO = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const shiftISO = (iso: string, months: number, days: number) => {
  const d = new Date(iso + 'T00:00:00')
  if (months) d.setMonth(d.getMonth() - months)
  if (days) d.setDate(d.getDate() - days)
  return toISO(d)
}
// per-granularity delta columns; offsets land on bucket starts (week => Mondays, month => 1st)
const DELTA: Record<string, { short: { label: string; months?: number; days?: number }; long: { label: string; months?: number; days?: number } }> = {
  day: { short: { label: 'DoD', days: 1 }, long: { label: 'WoW', days: 7 } },
  week: { short: { label: 'WoW', days: 7 }, long: { label: '4W', days: 28 } },
  month: { short: { label: 'MoM', months: 1 }, long: { label: 'YoY', months: 12 } },
}

type Hover = { x: number; y: number; rectLeft: number; rectTop: number; index: number }

export function TimeSeriesChart({ data, series, xLabel = 'TIME', yLabelPrimary, seriesType = 'line', percentStacked = false, granularity = 'Day', display, categorical = false, pngRef }: {
  data: ChartRow[]
  series: UISeries[]
  xLabel?: string
  yLabelPrimary?: string
  seriesType?: 'line' | 'bar' | 'area'
  percentStacked?: boolean // 100%-stacked filled areas: y-axis 0–100, cuts stack to fill the window
  granularity?: string // drives the hover card's two delta columns (DoD/WoW, WoW/4W, MoM/YoY)
  display?: ChartOptions // Options-tab display toggles
  categorical?: boolean // X-axis is a dimension (raw category labels, no time hover card)
  pngRef?: { current: (() => string | null) | null } // lets the parent grab a PNG for export
}) {
  const d = display ?? DEFAULT_DISPLAY
  const elRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<Hover | null>(null)
  const [cardPos, setCardPos] = useState({ left: 0, top: 0 })

  useEffect(() => {
    if (!pngRef) return
    pngRef.current = () => (chartRef.current ? chartRef.current.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' }) : null)
    return () => { if (pngRef) pngRef.current = null }
  }, [pngRef])

  useEffect(() => {
    if (!elRef.current) return
    const chart = echarts.init(elRef.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(elRef.current)
    const raf = requestAnimationFrame(() => chart.resize())
    const t = setTimeout(() => chart.resize(), 120)

    // custom hover card: track the hovered index + cursor position; rows are
    // derived (below) from the latest data/series so this stays non-stale.
    const zr = chart.getZr()
    const onMove = (e: { offsetX: number; offsetY: number }) => {
      if (!chart.containPixel('grid', [e.offsetX, e.offsetY])) { setHover(null); return }
      const idx = Math.round(chart.convertFromPixel({ xAxisIndex: 0 }, e.offsetX) as number)
      const rect = elRef.current!.getBoundingClientRect()
      setHover({ x: e.offsetX, y: e.offsetY, rectLeft: rect.left, rectTop: rect.top, index: idx })
    }
    const onOut = () => setHover(null)
    zr.on('mousemove', onMove)
    zr.on('globalout', onOut)

    return () => {
      cancelAnimationFrame(raf); clearTimeout(t); ro.disconnect()
      zr.off('mousemove', onMove); zr.off('globalout', onOut); chart.dispose()
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    setHover(null)

    const compact = (v: number, decimals = 1) => {
      if (v == null) return ''
      if (Math.abs(v) >= 1000) return (v / 1000).toFixed(decimals).replace(/\.0$/, '') + 'k'
      return v.toFixed(decimals).replace(/\.0$/, '')
    }
    const hasSecondary = !percentStacked && series.some((s) => s.axis === 'secondary')

    const echartSeries = series.map((s) => ({
      name: s.label,
      type: (seriesType === 'bar' && !percentStacked) ? 'bar' : 'line',
      yAxisIndex: percentStacked ? 0 : (s.axis === 'secondary' ? 1 : 0),
      stack: percentStacked ? 'pct' : undefined,
      smooth: d.smooth,
      showSymbol: d.showPoints,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: { width: percentStacked ? 1 : 1.6, color: s.color },
      itemStyle: { color: s.color },
      areaStyle: percentStacked ? { opacity: 0.85, color: s.color } : (seriesType === 'area' ? { opacity: 0.12, color: s.color } : undefined),
      emphasis: { focus: 'series' as const },
      connectNulls: d.connectNulls,
      data: data.map((row) => row[s.key] ?? null),
    }))

    chart.setOption({
      animationDuration: 350,
      color: series.map((s) => s.color),
      grid: { left: 56, right: hasSecondary ? 60 : 24, top: 18, bottom: 56 },
      // time mode: content disabled (React HoverCard renders it); categorical: built-in tooltip
      tooltip: categorical
        ? {
            trigger: 'axis', backgroundColor: 'rgba(255,255,255,0.98)', borderColor: '#e2e8f0', borderWidth: 1,
            padding: [8, 12], textStyle: { color: '#0f172a', fontSize: 12 }, extraCssText: 'box-shadow:0 6px 20px rgba(15,23,42,.12);border-radius:8px;',
            formatter: (ps: any) => {
              if (!ps || !ps.length) return ''
              let out = `<div style="font-weight:600;margin-bottom:4px;color:#475569">${ps[0].axisValue}</div>`
              ps.forEach((pt: any) => {
                const cfg = series.find((s) => s.label === pt.seriesName) || ({} as UISeries)
                const val = pt.data == null ? '—' : fmtVal(Number(pt.data), cfg)
                out += `<div style="display:flex;align-items:center;gap:6px"><span style="width:8px;height:8px;border-radius:99px;background:${pt.color}"></span><span style="color:#475569">${pt.seriesName}</span><span style="margin-left:auto;font-weight:600;color:#0f172a">${val}</span></div>`
              })
              return out
            },
          }
        : { trigger: 'axis', showContent: false, axisPointer: { type: 'line', lineStyle: { color: '#cbd5e1' } } },
      xAxis: {
        type: 'category',
        data: data.map((d) => d.date),
        boundaryGap: categorical || seriesType === 'bar',
        name: xLabel,
        nameLocation: 'middle',
        nameGap: categorical ? 48 : 34,
        nameTextStyle: { color: '#94a3b8', fontSize: 11, fontWeight: 600 },
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
        axisLabel: categorical
          ? { color: '#94a3b8', fontSize: 11, interval: 0, rotate: 30, hideOverlap: true, formatter: (v: string) => v }
          : { color: '#94a3b8', fontSize: 11, interval: 'auto', hideOverlap: true, formatter: (v: string) => fmtDate(v) },
      },
      yAxis: [
        {
          type: (d.logScale && !percentStacked) ? 'log' : 'value',
          name: percentStacked ? '%' : yLabelPrimary, nameLocation: 'middle', nameGap: 44, nameRotate: 90,
          nameTextStyle: { color: '#94a3b8', fontSize: 11 },
          axisLine: { show: false }, axisTick: { show: false },
          splitLine: { show: d.gridlines, lineStyle: { color: '#f1f5f9' } },
          axisLabel: { color: '#94a3b8', fontSize: 11, formatter: percentStacked ? (v: number) => `${v}%` : (v: number) => compact(v) },
          min: percentStacked ? 0 : (d.logScale ? undefined : (d.zeroBase ? 0 : undefined)),
          max: percentStacked ? 100 : undefined,
          scale: !percentStacked && !d.logScale && !d.zeroBase,
        },
        {
          type: 'value', axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false },
          axisLabel: {
            color: '#94a3b8', fontSize: 11,
            formatter: (v: number) => {
              const sec = series.find((s) => s.axis === 'secondary')
              return (sec && sec.unit === '$' ? '$' : '') + Number(v).toLocaleString(undefined, { maximumFractionDigits: sec ? (sec.decimals ?? 1) : 1 })
            },
          },
          show: hasSecondary, scale: true,
        },
      ],
      series: echartSeries,
    }, true)
    chart.resize()
  }, [data, series, xLabel, yLabelPrimary, seriesType, percentStacked, display, categorical])

  const gran = (granularity || 'day').toLowerCase()
  const dcfg = DELTA[gran] || DELTA.day

  // date -> value per series, so deltas resolve by exact prior date (gaps => null)
  const byKey = useMemo(() => {
    const m = new Map<string, Map<string, number | null>>()
    for (const s of series) {
      const dm = new Map<string, number | null>()
      for (const row of data) dm.set(String(row.date), numOr(row[s.key]))
      m.set(s.key, dm)
    }
    return m
  }, [data, series])

  // per-series rows at the hovered point: value + two period-over-period deltas,
  // each compared against the value at the exact prior date (not array index).
  const hoverRows = useMemo<HoverRow[]>(() => {
    if (!hover || hover.index < 0 || hover.index >= data.length) return []
    const curDate = String(data[hover.index].date)
    const shortDate = shiftISO(curDate, dcfg.short.months || 0, dcfg.short.days || 0)
    const longDate = shiftISO(curDate, dcfg.long.months || 0, dcfg.long.days || 0)
    const pct = (now: number | null, then: number | null) =>
      now == null || then == null || then === 0 ? null : Math.round(((now - then) / then) * 100)
    return series.map((s) => {
      const dm = byKey.get(s.key)
      const cur = dm?.get(curDate) ?? null
      const short = dm?.get(shortDate) ?? null
      const long = dm?.get(longDate) ?? null
      return { name: s.label, color: s.color, value: cur == null ? '—' : fmtVal(cur, s), short: pct(cur, short), long: pct(cur, long) }
    })
  }, [hover, data, series, byKey, gran])

  // total across all series at the hovered point + its deltas. Only meaningful
  // when there are ≥2 series sharing a unit (else a mixed-unit sum is nonsense).
  const hoverTotal = useMemo(() => {
    if (!hover || hover.index < 0 || hover.index >= data.length) return null
    if (series.length < 2) return null
    const unit = series[0].unit
    if (!series.every((s) => s.unit === unit)) return null
    const curDate = String(data[hover.index].date)
    const shortDate = shiftISO(curDate, dcfg.short.months || 0, dcfg.short.days || 0)
    const longDate = shiftISO(curDate, dcfg.long.months || 0, dcfg.long.days || 0)
    const sumAt = (date: string) => {
      let any = false, sum = 0
      for (const s of series) { const v = byKey.get(s.key)?.get(date) ?? null; if (v != null) { sum += v; any = true } }
      return any ? sum : null
    }
    const cur = sumAt(curDate), short = sumAt(shortDate), long = sumAt(longDate)
    const pct = (now: number | null, then: number | null) =>
      now == null || then == null || then === 0 ? null : Math.round(((now - then) / then) * 100)
    return { value: cur == null ? '—' : fmtVal(cur, series[0]), short: pct(cur, short), long: pct(cur, long) }
  }, [hover, data, series, byKey, gran])

  const title = hover && data[hover.index] ? fmtDate(String(data[hover.index].date)) : ''

  // viewport-aware: prefer right of cursor, flip left near the right edge, clamp vertically
  useLayoutEffect(() => {
    if (!hover || !cardRef.current) return
    const card = cardRef.current.getBoundingClientRect()
    const vw = window.innerWidth, vh = window.innerHeight, gap = 16
    const cursorX = hover.rectLeft + hover.x, cursorY = hover.rectTop + hover.y
    let left = cursorX + gap
    if (left + card.width > vw - 8) left = cursorX - gap - card.width
    if (left < 8) left = 8
    let top = cursorY - card.height / 2
    if (top < 8) top = 8
    if (top + card.height > vh - 8) top = vh - 8 - card.height
    setCardPos({ left, top })
  }, [hover, hoverRows])

  return (
    <>
      <div ref={elRef} className="h-full w-full" />
      {!categorical && hover && hoverRows.length > 0 && (
        <div ref={cardRef} style={{ position: 'fixed', left: cardPos.left, top: cardPos.top, zIndex: 50, pointerEvents: 'none' }}>
          <HoverCard title={title} rows={hoverRows} shortLabel={dcfg.short.label} longLabel={dcfg.long.label} total={hoverTotal} />
        </div>
      )}
    </>
  )
}
