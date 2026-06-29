import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import type { ChartRow, UISeries } from '../components/types'
import { HoverCard } from './HoverCard'
import type { HoverRow } from './HoverCard'
import type { ChartOptions } from '../components/types'

const DEFAULT_DISPLAY: ChartOptions = { showLegend: true, smooth: false, showPoints: false, connectNulls: false, gridlines: true, zeroBase: true, logScale: false }

/* ECharts line/area/bar time series with primary/secondary Y axes. The built-in
   tooltip content is disabled (axisPointer crosshair only); a React HoverCard
   shows per-series value + DoD/WoW deltas, positioned beside the cursor.

   Box-zoom: a "Box zoom" button arms a transparent capture overlay laid over the chart.
   While armed, the overlay (not ECharts, not the canvas) owns the drag — so nothing competes
   for the mousedown — draws the selection box, and on release maps the pixel box to data via
   convertFromPixel and applies it with dispatchAction on explicit `inside` dataZoom components
   (this exact path is verified to zoom x + the value axis, even with a zero-based y-axis). */

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const fmtDate = (iso: string) => {
  const d = new Date(iso + 'T00:00:00')
  return `${MON[d.getMonth()]} ${String(d.getDate()).padStart(2, '0')}'${String(d.getFullYear()).slice(2)}`
}
const fmtVal = (v: number, s: UISeries) => {
  const u = s.unit && s.unit !== 'None' ? s.unit : ''
  let dp = s.decimals ?? 0
  // a non-zero value must never collapse to "0": widen precision (up to 6 dp) until a
  // digit shows, so small ratio/formula metrics (e.g. 0.27) stay readable at low decimals
  if (v !== 0) while (dp < 6 && Number(v.toFixed(dp)) === 0) dp++
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

const clampN = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v))

type Hover = { x: number; y: number; rectLeft: number; rectTop: number; index: number }
type Drag = { x0: number; y0: number; x1: number; y1: number }

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
  const wrapRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<Drag | null>(null) // live box-zoom drag (mutated by window listeners)
  const [hover, setHover] = useState<Hover | null>(null)
  const [cardPos, setCardPos] = useState({ left: 0, top: 0 })
  const [zoomed, setZoomed] = useState(false) // box-zoomed in (shows the Zoom-out button)
  const [zoomArmed, setZoomArmed] = useState(false) // "Box zoom" button toggled on → overlay active
  const [sel, setSel] = useState<Drag | null>(null) // selection rectangle being drawn

  const hasSecondary = !percentStacked && series.some((s) => s.axis === 'secondary')
  // dynamic (non-zero-based) y-axis: pad above & below so the line never sits flush on the
  // floor/ceiling. Zero-base (default) uses scale:false; a value-axis dataZoom still zooms a
  // band even with scale:false (verified headless), so zero-base and box-zoom coexist.
  const dynY = !percentStacked && !d.logScale && !d.zeroBase
  const yPad: [string, string] = ['20%', '20%']

  const armZoom = (on: boolean) => { setZoomArmed(on); setSel(null); if (on) setHover(null) }

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

    // reflect box-zoom state so the "Zoom out" button shows only when zoomed in.
    // NOTE: the ECharts event is 'datazoom' (lowercase) — 'dataZoom' never fires.
    const onZoom = () => {
      const dz = ((chart.getOption() as any)?.dataZoom || []) as Array<{ start?: number; end?: number }>
      setZoomed(dz.some((z) => (z.start ?? 0) > 0.2 || (z.end ?? 100) < 99.8))
    }
    chart.on('datazoom', onZoom)

    return () => {
      cancelAnimationFrame(raf); clearTimeout(t); ro.disconnect()
      chart.off('datazoom', onZoom); chart.dispose()
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    setHover(null)

    // Primary-axis label precision adapts to the data span so tight/near-1 ranges
    // don't all collapse to the same label (0.95, 1.00, 1.05 → distinct, not all "1").
    const primaryVals: number[] = []
    for (const row of data) for (const s of series) {
      if (s.axis === 'secondary') continue
      const v = row[s.key]
      if (typeof v === 'number') primaryVals.push(v)
    }
    let axisDec = 1
    if (primaryVals.length) {
      let mn = Infinity, mx = -Infinity
      for (const v of primaryVals) { if (v < mn) mn = v; if (v > mx) mx = v }
      const ref = (mx - mn) || Math.abs(mx) || 1
      axisDec = ref >= 5 ? 0 : ref >= 0.5 ? 1 : 2
    }
    const compact = (v: number) => {
      if (v == null || isNaN(v)) return ''
      if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1).replace(/\.0$/, '') + 'k'
      const s = v.toFixed(axisDec)
      return axisDec >= 2 ? s : s.replace(/\.0$/, '') // keep 0.90 vs 1.00 distinct; tidy whole steps elsewhere
    }

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
      // explicit `inside` dataZoom components are the zoom TARGETS — the capture overlay writes
      // ranges to them via dispatchAction. Wheel/drag-pan are off, so they do nothing on their
      // own; at full range (0–100) they're a no-op, so zero-base still holds. Index order is
      // fixed: 0 = x, 1 = y-primary (omitted when percentStacked), then secondary.
      dataZoom: [
        { type: 'inside', xAxisIndex: 0, filterMode: 'none', zoomOnMouseWheel: false, moveOnMouseMove: false, moveOnMouseWheel: false },
        ...(percentStacked ? [] : [{ type: 'inside', yAxisIndex: 0, filterMode: 'none', zoomOnMouseWheel: false, moveOnMouseMove: false, moveOnMouseWheel: false }]),
        ...(hasSecondary ? [{ type: 'inside', yAxisIndex: 1, filterMode: 'none', zoomOnMouseWheel: false, moveOnMouseMove: false, moveOnMouseWheel: false }] : []),
      ],
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
          // zero-baseline comes from scale:false (when zeroBase on); a box-zoom still zooms a band.
          // dynY (auto-fit) uses scale:true + padding.
          min: percentStacked ? 0 : undefined,
          max: percentStacked ? 100 : undefined,
          scale: dynY,
          boundaryGap: dynY ? yPad : undefined,
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
          show: hasSecondary, scale: true, boundaryGap: yPad,
        },
      ],
      series: echartSeries,
    }, true)
    chart.resize()
    setZoomed(false) // a fresh (notMerge) render resets the dataZoom to the full extent
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

  /* ---------------------------------------------------------------- box-zoom */
  // window-level move/up so the drag keeps tracking even if the cursor leaves the overlay.
  function onWinMove(e: MouseEvent) {
    const drag = dragRef.current, el = wrapRef.current
    if (!drag || !el) return
    const r = el.getBoundingClientRect()
    drag.x1 = clampN(e.clientX - r.left, 0, r.width)
    drag.y1 = clampN(e.clientY - r.top, 0, r.height)
    setSel({ x0: drag.x0, y0: drag.y0, x1: drag.x1, y1: drag.y1 })
  }
  function onWinUp() {
    const chart = chartRef.current, drag = dragRef.current
    if (chart && drag) {
      const x0 = Math.min(drag.x0, drag.x1), x1 = Math.max(drag.x0, drag.x1)
      const y0 = Math.min(drag.y0, drag.y1), y1 = Math.max(drag.y0, drag.y1)
      // zoom whichever axis was actually dragged (a wide-flat box zooms only the dates, etc.)
      const wideX = x1 - x0 >= 6, tallY = y1 - y0 >= 6
      if (wideX || tallY) {
        const n = data.length
        const ia = clampN(Math.round(chart.convertFromPixel({ xAxisIndex: 0 }, x0) as number), 0, n - 1)
        const ib = clampN(Math.round(chart.convertFromPixel({ xAxisIndex: 0 }, x1) as number), 0, n - 1)
        const yA = percentStacked ? 0 : (chart.convertFromPixel({ yAxisIndex: 0 }, y0) as number)
        const yB = percentStacked ? 0 : (chart.convertFromPixel({ yAxisIndex: 0 }, y1) as number)
        const s2A = hasSecondary ? (chart.convertFromPixel({ yAxisIndex: 1 }, y0) as number) : 0
        const s2B = hasSecondary ? (chart.convertFromPixel({ yAxisIndex: 1 }, y1) as number) : 0
        if (wideX) chart.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0, startValue: Math.min(ia, ib), endValue: Math.max(ia, ib) } as any)
        if (tallY && !percentStacked && isFinite(yA) && isFinite(yB)) chart.dispatchAction({ type: 'dataZoom', dataZoomIndex: 1, startValue: Math.min(yA, yB), endValue: Math.max(yA, yB) } as any)
        if (tallY && hasSecondary && isFinite(s2A) && isFinite(s2B)) chart.dispatchAction({ type: 'dataZoom', dataZoomIndex: 2, startValue: Math.min(s2A, s2B), endValue: Math.max(s2A, s2B) } as any)
        setZoomed(true)
      }
    }
    dragRef.current = null
    setSel(null)
    window.removeEventListener('mousemove', onWinMove)
    window.removeEventListener('mouseup', onWinUp)
  }
  // mousedown on the (top-most) capture overlay — nothing else competes for it
  const onOverlayDown = (e: React.MouseEvent) => {
    const el = wrapRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const x = clampN(e.clientX - r.left, 0, r.width), y = clampN(e.clientY - r.top, 0, r.height)
    dragRef.current = { x0: x, y0: y, x1: x, y1: y }
    window.addEventListener('mousemove', onWinMove)
    window.addEventListener('mouseup', onWinUp)
    e.preventDefault()
  }

  // reset the box-zoom back to the full view (no dataZoomIndex → every dataZoom → 0..100)
  const resetZoom = () => {
    chartRef.current?.dispatchAction({ type: 'dataZoom', start: 0, end: 100 } as any)
    setZoomed(false)
  }

  // hover card (time mode): driven by the wrapper's own mouse move. Disabled while box-zoom is
  // armed (the capture overlay sits on top and owns the pointer then).
  const onHoverMove = (e: React.MouseEvent) => {
    if (zoomArmed || categorical) return
    const chart = chartRef.current, el = wrapRef.current
    if (!chart || !el) return
    const r = el.getBoundingClientRect()
    const ox = e.clientX - r.left, oy = e.clientY - r.top
    if (!chart.containPixel('grid', [ox, oy])) { setHover(null); return }
    const idx = Math.round(chart.convertFromPixel({ xAxisIndex: 0 }, ox) as number)
    setHover({ x: ox, y: oy, rectLeft: r.left, rectTop: r.top, index: idx })
  }
  const onHoverLeave = () => setHover(null)

  const selRect = sel
    ? { left: Math.min(sel.x0, sel.x1), top: Math.min(sel.y0, sel.y1), width: Math.abs(sel.x1 - sel.x0), height: Math.abs(sel.y1 - sel.y0) }
    : null

  return (
    <>
      <div ref={wrapRef} className="relative h-full w-full" onMouseMove={onHoverMove} onMouseLeave={onHoverLeave}>
        <div ref={elRef} className="h-full w-full" />

        {/* capture overlay: only present while armed, sits on top so the drag never competes
            with the canvas for the mousedown */}
        {zoomArmed && !categorical && (
          <div className="absolute inset-0 z-20" style={{ cursor: 'crosshair' }} onMouseDown={onOverlayDown}>
            {selRect && (
              <div className="pointer-events-none absolute border" style={{ left: selRect.left, top: selRect.top, width: selRect.width, height: selRect.height, background: 'rgba(14,165,233,0.10)', borderColor: '#0ea5e9' }} />
            )}
          </div>
        )}

        {!categorical && (
          <div className="absolute right-2 top-2 z-30 flex items-center gap-1.5">
            <button onClick={() => armZoom(!zoomArmed)}
              title={zoomArmed ? 'Box-zoom on — drag a region on the chart, or click to turn off' : 'Turn on box-zoom, then drag a region on the chart'}
              className={'flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[12px] font-medium shadow-sm transition-colors ' + (zoomArmed ? 'border-sky-500 bg-sky-500 text-white' : 'border-slate-200 bg-white text-slate-600 hover:border-sky-300 hover:text-sky-600')}>
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>
              {zoomArmed ? 'Drag to zoom' : 'Box zoom'}
            </button>
            {zoomed && (
              <button onClick={resetZoom} title="Reset to the full range"
                className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[12px] font-medium text-slate-600 shadow-sm transition-colors hover:border-sky-300 hover:text-sky-600">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3M8 11h6" /></svg>
                Zoom out
              </button>
            )}
          </div>
        )}
      </div>
      {!categorical && hover && hoverRows.length > 0 && (
        <div ref={cardRef} style={{ position: 'fixed', left: cardPos.left, top: cardPos.top, zIndex: 50, pointerEvents: 'none' }}>
          <HoverCard title={title} rows={hoverRows} shortLabel={dcfg.short.label} longLabel={dcfg.long.label} total={hoverTotal} />
        </div>
      )}
    </>
  )
}
