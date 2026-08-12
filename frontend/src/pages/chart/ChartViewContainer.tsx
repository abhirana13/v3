import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { DimsMetrics, Freshness, MetricCfg } from '../../api/types'
import { ChartView } from './ChartView'
import type { MetricDraft } from './MetricSettingsModal'
import type { ChartRow, UIDimension, UIMetric, UISeries } from '../../components/types'
import { decodeSelection, encodeSelection, loadChartView, saveChartView } from './viewState'
import type { SavedChartView } from './viewState'
import type { ChartViewSeed } from './urlState'
import { naturalCompare } from './transforms'
import { SERIES_COLORS, maxSeries } from '../../charts/palette'

// Series colours live in charts/palette.ts — shared with dashboard widgets so one chart is the
// same colours wherever it's drawn. This view draws no target line, so the full 20 are available.
const PALETTE = SERIES_COLORS
const GRAN: Record<string, string> = { Day: 'day', Week: 'week', Month: 'month' }
// Max series DRAWN when splitting. Applied to the combos ACTUALLY PRESENT in the response,
// not to a pre-flight guess: this used to cap `∏ dimension.values.length`, i.e. every value the
// dimension has ever held, multiplied across split dims. Since the value lists are fetched once
// per chart mount with no date range, a dimension with 30 all-time values but 8 in the picked
// window still counted 30 — and two such dims multiplied to 900 against a real 20-odd series.
// Splits that would render fine were refused, and the notice's "filter values down" advice was
// impossible to act on, because the window had already done the filtering.
//
// Derived from the palette rather than written as 20, so the two cannot drift: the cap IS "one
// series per available colour", which is what keeps a colour from ever being reused in a chart.
const SERIES_CAP = maxSeries()
// Pre-flight bound on the SAME over-estimate, kept only to stop an absurd payload (splitting by
// something near-unique would pull every row before the frontend could count anything). High
// enough that no legitimate dimension reaches it; the real cap is applied post-response.
const COMBO_GUARD = 2000
const todayMinus = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }
const stripBrackets = (f: string) => f.replace(/[[\]]/g, '').trim()
const isoOf = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
// All bucket-start dates in [startISO, endISO] at the given granularity, matching the
// backend's date_trunc (week => Monday, month => 1st). Lets the x-axis span the SELECTED
// window even where there's no data (gaps, no line) instead of cropping to the data extent.
const dateBuckets = (startISO: string, endISO: string, gran: string): string[] => {
  if (!startISO || !endISO) return []
  const s = new Date(startISO + 'T00:00:00'), e = new Date(endISO + 'T00:00:00')
  if (isNaN(s.getTime()) || isNaN(e.getTime()) || s > e) return []
  const out: string[] = []
  if (gran === 'month') {
    const cur = new Date(s.getFullYear(), s.getMonth(), 1), last = new Date(e.getFullYear(), e.getMonth(), 1)
    while (cur <= last) { out.push(isoOf(cur)); cur.setMonth(cur.getMonth() + 1) }
  } else if (gran === 'week') {
    const monday = (d: Date) => { const x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x }
    const cur = monday(s), last = monday(e)
    while (cur <= last) { out.push(isoOf(cur)); cur.setDate(cur.getDate() + 7) }
  } else {
    const cur = new Date(s)
    while (cur <= e) { out.push(isoOf(cur)); cur.setDate(cur.getDate() + 1) }
  }
  return out
}


/* URL-carried cuts, expressed in the SAME shape as the remembered view state.
 *
 * Merging into that shape rather than threading the seed through every setter below means the
 * hydration block stays exactly as it was — the seed simply wins where it says something. A
 * dimension named in `split`/`filters` is fully described by the seed, so its remembered split
 * and selection are BOTH replaced; naming a split without a filter must not inherit a stale
 * narrowing from whatever the viewer last looked at.
 */
function seededView(saved: SavedChartView | null, seed?: ChartViewSeed | null): SavedChartView | null {
  if (!seed) return saved
  const base: SavedChartView = saved ?? { v: 1, ts: Date.now() }
  const dims = { ...(base.dims || {}) }
  const named = new Set([...(seed.split || []), ...Object.keys(seed.filters || {})])
  for (const d of named) {
    const vals = seed.filters?.[d]
    dims[d] = {
      split: (seed.split || []).includes(d),
      sel: vals && vals.length ? vals.map(String) : 'all',
    }
  }
  // every OTHER dimension is un-split: the seed describes the full set of cuts, so a leftover
  // split from the viewer's last visit would add a series the widget never showed
  if (seed.split) for (const d of Object.keys(dims)) if (!named.has(d)) dims[d] = { ...dims[d], split: false }
  return {
    ...base,
    dims,
    metrics: seed.metrics ?? base.metrics,
    from: seed.from ?? base.from,
    to: seed.to ?? base.to,
    granularity: seed.granularity ?? base.granularity,
    xAxis: seed.xAxis === undefined ? base.xAxis : (seed.xAxis === '' ? null : seed.xAxis),
  }
}

export function ChartViewContainer({ chartId, charts, seed, onSelectChart, onGoHome, onEditChart, onCreateChart }: {
  chartId: number
  // cuts carried in the URL (a dashboard widget's "open the source chart" link). Takes precedence
  // over the viewer's remembered state for this load — see seededView().
  seed?: ChartViewSeed | null
  charts: { id: number; name: string; number?: number | null; certified?: boolean }[]
  onSelectChart: (id: number) => void
  onGoHome: () => void
  onEditChart: (id: number) => void
  onCreateChart: () => void
}) {
  const [cfg, setCfg] = useState<DimsMetrics | null>(null)
  const [dimensions, setDimensions] = useState<UIDimension[]>([])
  const [metrics, setMetrics] = useState<UIMetric[]>([])
  const [granularity, setGranularity] = useState('Day')
  const [dateRange, setDateRange] = useState({ start: '', end: '' })
  const [hideZero, setHideZero] = useState(false)
  const [chartType, setChartType] = useState('Line Chart')
  const [metricSearch, setMetricSearch] = useState('')
  const [metricsTab, setMetricsTab] = useState('Metrics')

  const [chartData, setChartData] = useState<ChartRow[]>([])
  const [chartSeries, setChartSeries] = useState<UISeries[]>([])
  const [splitNotice, setSplitNotice] = useState<string | null>(null)
  // Set only when the chart rendered with some series trimmed off (see SERIES_CAP) — advisory,
  // so it must never take the place of the chart the way splitNotice does.
  const [splitInfo, setSplitInfo] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [backpopBusy, setBackpopBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [dataReloadKey, setDataReloadKey] = useState(0)
  const [endOffset, setEndOffset] = useState(2) // chart data ends this many days before today
  const [freshness, setFreshness] = useState<Freshness | null>(null)
  // Pivot x-axis: null => plot over the time column (normal time series). A dimension name
  // => the backend keys rows on that dimension instead (time collapses to a filter), so the
  // x-axis becomes e.g. install_date for a cohort view. Comes from the chart's saved x_axis.
  const [xAxisDim, setXAxisDim] = useState<string | null>(null)
  // Values for EVERY dimension, including excluded ones (which have no filter chip). Needed
  // to tell whether the x-axis dimension holds dates — an excluded date axis must still get
  // the date treatment, not be mistaken for unordered categories.
  const [allDimValues, setAllDimValues] = useState<Record<string, string[]>>({})

  const [settingsId, setSettingsId] = useState<string | null>(null)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [newDraft, setNewDraft] = useState<MetricDraft | null>(null)

  const title = charts.find((c) => c.id === chartId)?.name || `Chart ${chartId}`
  const fetchToken = useRef(0)
  // false until this chart's saved state has been applied, so the persist effect below
  // can't overwrite it with the freshly-mounted defaults before restore happens
  const hydrated = useRef(false)

  /* ---- load config + dimension values + date extent on chart change ---- */
  useEffect(() => {
    let alive = true
    hydrated.current = false // don't persist until this chart's state is restored
    setError(null); setCfg(null); setMetrics([]); setDimensions([]); setChartData([])
    ;(async () => {
      try {
        const dm = await api.getDimsMetrics(chartId)
        const dv = await api.getDimValues(chartId)
        if (!alive) return
        // Restore what the user last had on this chart (browser-only, see viewState.ts).
        // Everything below falls back to the chart's configured defaults when absent, and
        // anything naming a dimension/metric/value that no longer exists is dropped.
        const saved = seededView(loadChartView(chartId), seed)
        setCfg(dm)
        setAllDimValues(dv.dimensions || {})
        // `included: false` => configured but hidden here (the config page still lists it,
        // so it can be re-included). cfg keeps the full set for the save path below.
        setDimensions(dm.dimensions.filter((d) => d.included ?? true).map((d) => {
          const values = dv.dimensions[d.name] || []
          const sd = saved?.dims?.[d.name]
          return {
            key: d.name, label: d.name, values,
            selected: decodeSelection(sd, values),
            split: !!sd?.split,
          }
        }))
        const savedVisible = saved?.metrics?.length
          ? new Set(saved.metrics.filter((n) => dm.metrics.some((m) => m.name === n)))
          : null
        setMetrics(dm.metrics.filter((m) => m.included ?? true).map((m, i) => ({
          id: m.name, name: m.name, key: m.name, color: PALETTE[i % PALETTE.length],
          // restored visibility, else the default of showing the first metric
          visible: savedVisible && savedVisible.size ? savedVisible.has(m.name) : i === 0,
          columnName: m.column_name,
          formula: m.formula || '', independentFields: m.independent_dimensions || [],
          axis: m.y_axis, decimals: m.decimals, unit: m.unit || 'None',
        })))
        if (saved?.granularity) setGranularity(saved.granularity)
        if (saved?.chartType) setChartType(saved.chartType)
        if (saved?.hideZero != null) setHideZero(saved.hideZero)
        // open at the chart's saved default recency (falls back to 2)
        setEndOffset(saved?.endOffset ?? (dm.default_end_offset_days ?? 2))
        // saved x-axis: only a real dimension pivots (the time column means time series)
        const chartDefaultX = dm.x_axis && dm.x_axis !== dm.time_column ? dm.x_axis : null
        const restoredX = saved?.xAxis
        // a remembered pivot dimension that's since been dropped falls back to the default
        setXAxisDim(
          restoredX !== undefined && (restoredX === null || dm.dimensions.some((d) => d.name === restoredX))
            ? restoredX
            : chartDefaultX,
        )
        // window end = today; the recency offset caps it (recencyEnd) so the offset, not
        // a stale end, controls how recent the data is
        setDateRange({ start: saved?.from ?? (dv.date_min || ''), end: saved?.to ?? todayMinus(0) })
        hydrated.current = true
      } catch (e: any) {
        if (alive) setError(String(e.message || e))
      }
    })()
    return () => { alive = false }
  }, [chartId])

  /* ---- remember this chart's view state in the browser (see viewState.ts) ----
     Runs only after hydration, so a fresh mount can't clobber the saved state with
     defaults. Nothing here touches the backend. */
  useEffect(() => {
    if (!hydrated.current) return
    saveChartView(chartId, {
      granularity, chartType, hideZero, endOffset,
      from: dateRange.start, to: dateRange.end,
      xAxis: xAxisDim,
      dims: Object.fromEntries(
        dimensions.map((d) => [d.key, { split: d.split, sel: encodeSelection(d.selected, d.values) }]),
      ),
      metrics: metrics.filter((m) => m.visible).map((m) => m.name),
    })
  }, [chartId, granularity, chartType, hideZero, endOffset, dateRange.start, dateRange.end, xAxisDim, dimensions, metrics])

  /* ---- freshness (data recency + last backpop) for the header ---- */
  useEffect(() => {
    let alive = true
    setFreshness(null)
    api.freshness(chartId).then((f) => { if (alive) setFreshness(f) }).catch(() => {})
    return () => { alive = false }
  }, [chartId, dataReloadKey])

  /* ---- derived: filters (partial selections only), visible series ---- */
  const filters = useMemo(() => {
    const f: Record<string, string[]> = {}
    for (const d of dimensions) {
      if (d.selected.length > 0 && d.selected.length < d.values.length) f[d.key] = d.selected
    }
    return f
  }, [dimensions])

  const visibleMetrics = useMemo(() => metrics.filter((m) => m.visible), [metrics])
  // captures split membership + each split dim's filter cardinality, so the data
  // effect refetches/repivots when a split toggles (filters alone wouldn't change)
  const splitKey = useMemo(
    () => dimensions.filter((d) => d.split).map((d) => `${d.key}:${d.selected.length}/${d.values.length}`).join('|'),
    [dimensions],
  )
  // The recency offset is a hard cap on how recent data can be (today − offset). The
  // effective window end = min(picked end, cap), so changing the date can't load past
  // it. This is what's fetched, drawn, and shown in the date picker.
  const recencyEnd = useMemo(() => {
    const cap = todayMinus(endOffset)
    return dateRange.end && dateRange.end < cap ? dateRange.end : cap
  }, [dateRange.end, endOffset])

  // Is the pivot x-axis a DATE dimension (e.g. install_date)? Then its axis can be seeded
  // with the full picked window like a time axis, so filtering a series (e.g. one tenure)
  // never shrinks the axis. Read from the dimension's own values, so it's known even when
  // the current query returns no rows.
  const xAxisIsDate = useMemo(() => {
    if (!xAxisDim) return false
    // read from ALL dim values, not the (included-only) chip list — the x-axis dimension
    // is commonly excluded precisely because its values are dates
    const seen = (allDimValues[xAxisDim] ?? []).filter((v) => v != null && v !== '')
    return seen.length > 0 && seen.every((v) => /^\d{4}-\d{2}-\d{2}/.test(String(v)))
  }, [xAxisDim, allDimValues])

  /* ---- fetch data whenever the query inputs change ---- */
  useEffect(() => {
    if (!cfg || !cfg.time_column) return
    // Cleared on every path in: only a successful render that dropped series sets it, so a
    // stale "20 largest of 47" can't survive a refetch that no longer trims anything.
    setSplitInfo(null)
    const names = visibleMetrics.map((m) => m.name)
    if (names.length === 0) { setChartData([]); setChartSeries([]); setSplitNotice(null); setLoading(false); return }

    // A dimension with values but zero selected means every value is excluded → the
    // filter matches no rows. Render an empty chart rather than dropping the filter
    // (which would silently fall back to showing the full aggregate).
    const emptyDims = dimensions.filter((d) => d.values.length > 0 && d.selected.length === 0)
    if (emptyDims.length) {
      setChartData([]); setChartSeries([]); setError(null); setLoading(false)
      setSplitNotice(`No values selected for ${emptyDims.map((d) => d.label).join(', ')} — nothing to display. Select at least one value, or "All".`)
      return
    }

    // In pivot mode the x-axis dimension is the row key, not a series split — exclude it
    // from the split set so it doesn't also become a series.
    const splitDims = dimensions.filter((d) => d.split && d.key !== xAxisDim)
    const groupBy = splitDims.map((d) => d.key)

    // Payload guard only — an OVER-ESTIMATE by construction (all-time value lists, multiplied
    // across dims), so it must never be the thing that decides whether a chart renders. That
    // decision is made from the response, against SERIES_CAP. See COMBO_GUARD.
    const cardOf = (d: UIDimension) => {
      const sel = d.selected.length
      return sel > 0 && sel < d.values.length ? sel : d.values.length
    }
    const combos = splitDims.reduce((acc, d) => acc * Math.max(1, cardOf(d)), 1)
    if (splitDims.length && combos > COMBO_GUARD) {
      setSplitNotice(`Splitting by ${splitDims.map((d) => d.label).join(' × ')} could reach ${combos.toLocaleString()} combinations, too many to load at once. Narrow the date range, filter values down, or deselect a dimension.`)
      setChartData([]); setChartSeries([]); setError(null); setLoading(false)
      return
    }
    setSplitNotice(null)

    const token = ++fetchToken.current
    setLoading(true); setError(null)
    api.getData(chartId, {
      granularity: GRAN[granularity], from: dateRange.start || null, to: recencyEnd || null,
      // Always send an EXPLICIT axis: the chart's saved x_axis is applied when loading the
      // view (above), so from here the user's choice must win. Sending nothing would let the
      // backend re-apply the saved default, making the picker's "Time" option a no-op on a
      // chart that defaults to a pivot. The time column normalizes to a plain time series.
      metrics: names, groupBy, filters, hideZero, xAxis: xAxisDim || cfg.time_column,
    }).then((resp) => {
      if (token !== fetchToken.current) return
      // Row key: the pivot dimension's column when pivoting, else the time column.
      const colByNameAll = new Map(cfg.dimensions.map((d) => [d.name, d.column_name]))
      const tc = xAxisDim ? (colByNameAll.get(xAxisDim) || xAxisDim) : (cfg.time_column as string)
      // Seed the x-axis with the full selected window so it always spans the picked date
      // range (gaps where there's no data) instead of cropping to whatever rows came back.
      // Without this, filtering e.g. days_since_install=7 would shrink the axis to only the
      // cohorts that have a D7 row. A pivot on a DATE dimension (install_date) can be
      // generated the same way; the backend doesn't granularity-bucket a pivot dimension,
      // so its values are raw days. A non-date pivot dimension has no generable range —
      // its x-values come from the data.
      const buckets = !xAxisDim
        ? dateBuckets(dateRange.start, recencyEnd, GRAN[granularity])
        : xAxisIsDate
          ? dateBuckets(dateRange.start, recencyEnd, 'day')
          : []

      // Pivoting on a date dimension: the date filter applies to the chart's TIME column,
      // not the x-axis dimension, so rows outside the picked window can come back (e.g. a
      // cohort that installed before the window but was active inside it). Clamp to the
      // seeded window so the axis is exactly the picked range.
      const inWindow = new Set(buckets)
      const plotRows = (xAxisDim && xAxisIsDate && inWindow.size)
        ? resp.rows.filter((r) => inWindow.has(String(r[tc])))
        : resp.rows

      if (splitDims.length === 0) {
        // time-only aggregate: one series per visible metric (unchanged behavior)
        setChartSeries(visibleMetrics.map((m) => ({ key: m.key, label: m.name, color: m.color, axis: m.axis || 'primary', unit: m.unit, decimals: m.decimals, metricKey: m.key, metricLabel: m.name })))
        const byT = new Map(plotRows.map((r) => [String(r[tc]), r]))
        const dates = buckets.length
          ? buckets
          : [...new Set(plotRows.map((r) => String(r[tc])))].sort(naturalCompare)
        setChartData(dates.map((d) => {
          const r = byT.get(d)
          const row: ChartRow = { date: d }
          for (const n of names) row[n] = r ? (r[n] ?? null) : null
          return row
        }))
        setLoading(false)
        return
      }

      // split: backend returns dim columns (keyed by column_name) + metric columns.
      // Pivot into one series per (metric × dim-combo).
      const colByName = new Map(cfg.dimensions.map((d) => [d.name, d.column_name]))
      const splitCols = splitDims.map((d) => colByName.get(d.key) || d.key)
      const comboOf = (r: Record<string, unknown>) => splitCols.map((c) => String(r[c] ?? '∅')).join(' · ')
      const sKey = (mKey: string, combo: string) => `${mKey}${combo}`
      const multi = visibleMetrics.length > 1

      // Every combo that actually has a row in the window. This is the real series count — a
      // dimension value with no data in the picked range never appears here, so it costs
      // nothing against SERIES_CAP. With "hide zero" on it is stricter still: a combo whose
      // metrics are all 0 is dropped server-side and so is not a series either.
      const present: string[] = []
      const seen = new Set<string>()
      for (const r of plotRows) { const k = comboOf(r); if (!seen.has(k)) { seen.add(k); present.push(k) } }

      // Per-combo total of the primary visible metric. Ranks series for the cap below, and is
      // also the 'metric' value_order.
      const rank = visibleMetrics[0]
      const total = new Map<string, number>()
      if (rank) for (const r of plotRows) {
        const k = comboOf(r)
        total.set(k, (total.get(k) ?? 0) + (Number(r[rank.name]) || 0))
      }
      const byTotal = (a: string, b: string) =>
        (total.get(b) ?? 0) - (total.get(a) ?? 0) || naturalCompare(a, b)

      // Over the cap: keep the BIGGEST series rather than an arbitrary slice, so what is
      // dropped is what mattered least.
      const dropped = Math.max(0, present.length - SERIES_CAP)
      const order = (dropped ? [...present].sort(byTotal).slice(0, SERIES_CAP) : [...present])

      // Series ORDER is a separate concern from which series survive: the kept set is sorted by
      // the dimension's own value_order, so a 'natural' legend still reads D0, D1, D2-D7 ...
      // rather than "whichever 20 were largest, largest first". This used to be
      // order-of-first-appearance in the rows, i.e. the backend's (time bucket, dim) ordering —
      // so the legend was governed by whichever cohorts happened to show up on the earliest
      // day, e.g. "D2-D7, D8-D14, D0, D1, D15-D30, D360+, D31-D60". Now it follows the same
      // setting the config page exposes, so legend, x-axis and filter dropdowns all agree:
      //   'natural' (default) -> D0, D1, D2-D7, D8-D14, D15-D30 ... (naturalCompare)
      //   'metric'            -> biggest series first
      // With several split dims the FIRST one is the primary grouping, so its setting wins.
      const primaryOrder = cfg.dimensions.find((d) => d.name === splitDims[0]?.key)?.value_order
      order.sort(primaryOrder === 'metric' ? byTotal : naturalCompare)
      setSplitInfo(
        dropped
          ? `Showing the ${SERIES_CAP} largest of ${present.length} series${rank ? ` by ${rank.name}` : ''} — ${dropped} smaller ${dropped === 1 ? 'one is' : 'ones are'} hidden. Filter values down or deselect a dimension to see them.`
          : null,
      )

      const series: UISeries[] = []
      let ci = 0
      for (const combo of order) for (const m of visibleMetrics) {
        series.push({ key: sKey(m.key, combo), label: multi ? `${m.name} · ${combo}` : combo, color: PALETTE[ci % PALETTE.length], axis: m.axis || 'primary', unit: m.unit, decimals: m.decimals, metricKey: m.key, metricLabel: m.name, comboLabel: combo })
        ci++
      }
      setChartSeries(series)

      // Only the kept combos get written onto the row objects. A dropped combo has no series,
      // so its keys would be dead weight on the render path — and at the cardinalities this cap
      // exists for (country × days_since_install is 3,146 present combos) that is thousands of
      // unused keys per date, handed to ECharts, to display 20.
      const keptCombos = dropped ? new Set(order) : null
      const byDate = new Map<string, ChartRow>()
      for (const d of buckets) byDate.set(d, { date: d }) // seed the full window (gaps where no data)
      for (const r of plotRows) {
        const date = String(r[tc]); const combo = comboOf(r)
        if (keptCombos && !keptCombos.has(combo)) continue
        let row = byDate.get(date)
        if (!row) { row = { date }; byDate.set(date, row) }
        for (const m of visibleMetrics) row[sKey(m.key, combo)] = (r[m.name] as number) ?? null
      }
      setChartData([...byDate.values()].sort((a, b) => naturalCompare(a.date, b.date)))
      setLoading(false)
    }).catch((e) => {
      if (token !== fetchToken.current) return
      setError(String(e.message || e)); setLoading(false)
    })
  }, [cfg, chartId, visibleMetrics, granularity, dateRange.start, recencyEnd, filters, hideZero, splitKey, dataReloadKey, xAxisDim, xAxisIsDate])

  /* ---- dimension callbacks ---- */
  const onDimensionToggleValue = useCallback((key: string, val: string) => {
    setDimensions((ds) => ds.map((d) => d.key !== key ? d : { ...d, selected: d.selected.includes(val) ? d.selected.filter((x) => x !== val) : [...d.selected, val] }))
  }, [])
  const onDimensionSetAll = useCallback((key: string, on: boolean) => {
    setDimensions((ds) => ds.map((d) => d.key !== key ? d : { ...d, selected: on ? [...d.values] : [] }))
  }, [])
  const onDimensionToggleSplit = useCallback((key: string) => {
    setDimensions((ds) => ds.map((d) => d.key !== key ? d : { ...d, split: !d.split }))
  }, [])
  // master "All": checked when nothing is split; toggling re-aggregates (clears every split)
  const allToggle = dimensions.length > 0 && dimensions.every((d) => !d.split)
  const onAllToggle = useCallback(() => {
    setDimensions((ds) => ds.map((d) => ({ ...d, split: false })))
  }, [])

  /* ---- metric callbacks ---- */
  const onMetricToggle = useCallback((id: string) => setMetrics((ms) => ms.map((m) => m.id === id ? { ...m, visible: !m.visible } : m)), [])
  const onMetricsToggleAll = useCallback((on: boolean) => setMetrics((ms) => ms.map((m) => ({ ...m, visible: on }))), [])

  /* ---- backpopulation (queues the chart's default window; the worker runs it) ---- */
  const onBackpopulate = useCallback(async () => {
    setBackpopBusy(true); setToast('Backpopulation started…')
    try {
      const run = await api.backpopulate(chartId)
      // async backpop: the endpoint returns a queued run; the worker executes it. Don't
      // report "0 rows" as if it finished, and don't refetch yet (data isn't ready).
      if (run.status === 'queued' || run.status === 'running') {
        setToast(`Backpopulation queued (${run.from_date} → ${run.to_date}) — running in the background; reload in a bit to see new data.`)
      } else {
        setToast(`Backpopulation ${run.status} · ${run.row_count.toLocaleString()} rows (${run.from_date} → ${run.to_date})`)
        setDataReloadKey((k) => k + 1) // refetch data over the current range
      }
    } catch (e: any) {
      setToast(`Backpopulation failed: ${String(e.message || e).slice(0, 160)}`)
    } finally {
      setBackpopBusy(false)
      window.setTimeout(() => setToast(null), 7000)
    }
  }, [chartId])

  /* ---- data recency offset: caps how recent data can be (today − offset). Just sets
     the offset; recencyEnd derives the effective end, so it can't be clobbered by the
     date picker (and lowering the offset re-extends toward today). ---- */
  const onEndOffsetChange = useCallback((n: number) => setEndOffset(n), [])

  /* ---- share: copy a link that reopens this chart ---- */
  const onShare = useCallback(async () => {
    const link = `${window.location.origin}${window.location.pathname}?chart=${chartId}`
    try { await navigator.clipboard.writeText(link); setToast('Shareable link copied to clipboard') }
    catch { setToast(`Copy this link: ${link}`) }
    window.setTimeout(() => setToast(null), 5000)
  }, [chartId])

  /* ---- persist dims/metrics config to the backend (PUT replaces all) ---- */
  const buildPayload = (uiMetrics: UIMetric[]) => ({
    time_column: cfg!.time_column,
    // Preserve each dimension's saved value_order, and skip backend-derived dims
    // (e.g. country_tier) — otherwise a metric edit here would reset value ordering
    // to the default and persist a derived dim as a real one.
    // PUT replaces everything, so carry the excluded dims/metrics through untouched —
    // they aren't shown here, and omitting them would silently delete them.
    dimensions: cfg!.dimensions
      .filter((d) => !d.derived)
      .map((d) => ({ name: d.name, column_name: d.column_name, value_order: d.value_order, included: d.included ?? true })),
    metrics: [
      ...uiMetrics.map<MetricCfg>((m) => ({
        name: m.name,
        column_name: m.formula ? null : (m.columnName ?? m.name),
        independent_dimensions: m.independentFields || [],
        formula: m.formula ? stripBrackets(m.formula) : null,
        y_axis: m.axis || 'primary',
        decimals: m.decimals ?? 0,
        unit: m.unit && m.unit !== 'None' ? m.unit : null,
        included: true,
      })),
      ...cfg!.metrics.filter((m) => !(m.included ?? true)),
    ],
  })

  const persist = async (uiMetrics: UIMetric[]) => {
    setSettingsError(null)
    try {
      await api.putDimsMetrics(chartId, buildPayload(uiMetrics))
      setMetrics(uiMetrics)
      fetchToken.current++ // force refetch via effect deps (metrics changed)
      return true
    } catch (e: any) {
      setSettingsError(String(e.message || e).slice(0, 400))
      return false
    }
  }

  const settingsMetric: MetricDraft | null = useMemo(() => {
    if (newDraft) return newDraft
    const m = metrics.find((x) => x.id === settingsId)
    if (!m) return null
    return { id: m.id, name: m.name, formula: m.formula, independentFields: m.independentFields, axis: m.axis, decimals: m.decimals, unit: m.unit }
  }, [settingsId, newDraft, metrics])

  const onOpenMetricSettings = useCallback((id: string) => { setNewDraft(null); setSettingsError(null); setSettingsId(id) }, [])
  const onAddMetric = useCallback(() => {
    setSettingsError(null); setSettingsId(null)
    setNewDraft({ id: '__new__', name: '', formula: '', independentFields: [], axis: 'primary', decimals: 2, unit: 'None', isNew: true })
  }, [])
  const onCloseSettings = useCallback(() => { setSettingsId(null); setNewDraft(null); setSettingsError(null) }, [])

  // reorder: persist metrics in the new sequence (backend stores display_order by position)
  const onReorderMetrics = (orderedIds: string[]) => {
    const byId = new Map(metrics.map((m) => [m.id, m]))
    const ordered = orderedIds.map((id) => byId.get(id)).filter((m): m is UIMetric => !!m)
    for (const m of metrics) if (!orderedIds.includes(m.id)) ordered.push(m)
    persist(ordered.map((m, i) => ({ ...m, color: PALETTE[i % PALETTE.length] })))
  }

  const applyDraft = (draft: MetricDraft, close: boolean) => {
    if (draft.isNew) {
      if (!draft.name.trim()) { setSettingsError('Metric name is required.'); return }
      const next: UIMetric = {
        id: draft.name, name: draft.name, key: draft.name, color: PALETTE[metrics.length % PALETTE.length],
        visible: true, columnName: null, formula: draft.formula || '', independentFields: draft.independentFields || [],
        axis: draft.axis, decimals: draft.decimals, unit: draft.unit,
      }
      persist([...metrics, next]).then((ok) => { if (ok && close) onCloseSettings() })
    } else {
      const next = metrics.map((m) => m.id === draft.id ? { ...m, name: draft.name, key: m.key, formula: draft.formula, independentFields: draft.independentFields, axis: draft.axis, decimals: draft.decimals, unit: draft.unit } : m)
      persist(next).then((ok) => { if (ok && close) onCloseSettings() })
    }
  }

  // delete a metric (from the settings modal) — drop it and persist the rest
  const deleteMetric = (draft: MetricDraft) => {
    const next = metrics.filter((m) => m.id !== draft.id)
    persist(next).then((ok) => { if (ok) onCloseSettings() })
  }

  return (
    <ChartView
      title={title} chartId={chartId} charts={charts} onSelectChart={onSelectChart}
      onGoHome={onGoHome} onEditChart={onEditChart} onCreateChart={onCreateChart} freshness={freshness}
      chartType={chartType} onChartTypeChange={setChartType}
      granularity={granularity} onGranularityChange={setGranularity}
      dateRange={{ start: dateRange.start, end: recencyEnd }} onDateRangeChange={(s, e) => setDateRange({ start: s, end: e })}
      dimensions={dimensions} allToggle={allToggle}
      onDimensionToggleValue={onDimensionToggleValue} onDimensionSetAll={onDimensionSetAll}
      onDimensionToggleSplit={onDimensionToggleSplit} splitNotice={splitNotice} splitInfo={splitInfo}
      onAllToggle={onAllToggle} onAddDimension={() => alert('Add dimension is configured in the Query Editor (Phase 9).')}
      metrics={metrics} metricSearch={metricSearch} onMetricSearchChange={setMetricSearch}
      onMetricToggle={onMetricToggle} onMetricsToggleAll={onMetricsToggleAll}
      onOpenMetricSettings={onOpenMetricSettings} onAddMetric={onAddMetric} onReorderMetrics={onReorderMetrics}
      hideZero={hideZero} onHideZeroToggle={setHideZero}
      xDim={xAxisDim || ''} onXDim={(v) => setXAxisDim(v || null)} xAxisIsDate={xAxisIsDate}
      chartData={chartData} chartSeries={chartSeries}
      onBackpopulate={onBackpopulate} backpopBusy={backpopBusy}
      endOffset={endOffset} onEndOffsetChange={onEndOffsetChange} onShare={onShare} toast={toast}
      metricsTab={metricsTab} onMetricsTabChange={setMetricsTab}
      settingsMetric={settingsMetric} settingsOpen={settingsMetric != null} settingsError={settingsError}
      onCloseSettings={onCloseSettings}
      onApplySettings={(d) => applyDraft(d, false)} onSaveSettings={(d) => applyDraft(d, true)} onDeleteSettings={deleteMetric}
      loading={loading} error={error}
    />
  )
}
