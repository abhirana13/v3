import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { DimsMetrics, Freshness, MetricCfg } from '../../api/types'
import { ChartView } from './ChartView'
import type { MetricDraft } from './MetricSettingsModal'
import type { ChartRow, UIDimension, UIMetric, UISeries } from '../../components/types'

const PALETTE = ['#38bdf8', '#a855f7', '#16a34a', '#f59e0b', '#ef4444', '#14b8a6', '#6366f1', '#ec4899', '#0ea5e9', '#84cc16']
const GRAN: Record<string, string> = { Day: 'day', Week: 'week', Month: 'month' }
const SERIES_CAP = 20 // max series when splitting (per-dimension AND cross-dimension)
const todayMinus = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }
const stripBrackets = (f: string) => f.replace(/[[\]]/g, '').trim()

export function ChartViewContainer({ chartId, charts, onSelectChart, onGoHome, onEditChart, onCreateChart }: {
  chartId: number
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
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [backpopBusy, setBackpopBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [dataReloadKey, setDataReloadKey] = useState(0)
  const [endOffset, setEndOffset] = useState(2) // chart data ends this many days before today
  const [freshness, setFreshness] = useState<Freshness | null>(null)

  const [settingsId, setSettingsId] = useState<string | null>(null)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [newDraft, setNewDraft] = useState<MetricDraft | null>(null)

  const title = charts.find((c) => c.id === chartId)?.name || `Chart ${chartId}`
  const fetchToken = useRef(0)

  /* ---- load config + dimension values + date extent on chart change ---- */
  useEffect(() => {
    let alive = true
    setError(null); setCfg(null); setMetrics([]); setDimensions([]); setChartData([])
    ;(async () => {
      try {
        const dm = await api.getDimsMetrics(chartId)
        const dv = await api.getDimValues(chartId)
        if (!alive) return
        setCfg(dm)
        setDimensions(dm.dimensions.map((d) => ({
          key: d.name, label: d.name,
          values: dv.dimensions[d.name] || [],
          selected: dv.dimensions[d.name] || [],
          split: false,
        })))
        setMetrics(dm.metrics.map((m, i) => ({
          id: m.name, name: m.name, key: m.name, color: PALETTE[i % PALETTE.length],
          visible: i === 0, columnName: m.column_name,
          formula: m.formula || '', independentFields: m.independent_dimensions || [],
          axis: m.y_axis, decimals: m.decimals, unit: m.unit || 'None',
        })))
        setDateRange({ start: dv.date_min || '', end: todayMinus(endOffset) })
      } catch (e: any) {
        if (alive) setError(String(e.message || e))
      }
    })()
    return () => { alive = false }
  }, [chartId])

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

  /* ---- fetch data whenever the query inputs change ---- */
  useEffect(() => {
    if (!cfg || !cfg.time_column) return
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

    const splitDims = dimensions.filter((d) => d.split)
    const groupBy = splitDims.map((d) => d.key)

    // cross-dimension cap: product of each split dim's effective (post-filter) cardinality
    const cardOf = (d: UIDimension) => {
      const sel = d.selected.length
      return sel > 0 && sel < d.values.length ? sel : d.values.length
    }
    const combos = splitDims.reduce((acc, d) => acc * Math.max(1, cardOf(d)), 1)
    if (splitDims.length && combos > SERIES_CAP) {
      setSplitNotice(`Splitting by ${splitDims.map((d) => d.label).join(' × ')} would create ${combos} series — over the ${SERIES_CAP}-series limit. Filter values down or deselect a dimension.`)
      setChartData([]); setChartSeries([]); setError(null); setLoading(false)
      return
    }
    setSplitNotice(null)

    const token = ++fetchToken.current
    setLoading(true); setError(null)
    api.getData(chartId, {
      granularity: GRAN[granularity], from: dateRange.start || null, to: dateRange.end || null,
      metrics: names, groupBy, filters, hideZero,
    }).then((resp) => {
      if (token !== fetchToken.current) return
      const tc = cfg.time_column as string

      if (splitDims.length === 0) {
        // time-only aggregate: one series per visible metric (unchanged behavior)
        setChartSeries(visibleMetrics.map((m) => ({ key: m.key, label: m.name, color: m.color, axis: m.axis || 'primary', unit: m.unit, decimals: m.decimals, metricKey: m.key, metricLabel: m.name })))
        setChartData(resp.rows.map((r) => {
          const row: ChartRow = { date: r[tc] as string }
          for (const n of names) row[n] = r[n] ?? null
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

      const order: string[] = []
      const seen = new Set<string>()
      for (const r of resp.rows) { const k = comboOf(r); if (!seen.has(k)) { seen.add(k); order.push(k) } }

      const series: UISeries[] = []
      let ci = 0
      for (const combo of order) for (const m of visibleMetrics) {
        series.push({ key: sKey(m.key, combo), label: multi ? `${m.name} · ${combo}` : combo, color: PALETTE[ci++ % PALETTE.length], axis: m.axis || 'primary', unit: m.unit, decimals: m.decimals, metricKey: m.key, metricLabel: m.name, comboLabel: combo })
      }
      setChartSeries(series)

      const byDate = new Map<string, ChartRow>()
      for (const r of resp.rows) {
        const date = String(r[tc]); const combo = comboOf(r)
        let row = byDate.get(date)
        if (!row) { row = { date }; byDate.set(date, row) }
        for (const m of visibleMetrics) row[sKey(m.key, combo)] = (r[m.name] as number) ?? null
      }
      setChartData([...byDate.values()].sort((a, b) => (String(a.date) < String(b.date) ? -1 : 1)))
      setLoading(false)
    }).catch((e) => {
      if (token !== fetchToken.current) return
      setError(String(e.message || e)); setLoading(false)
    })
  }, [cfg, chartId, visibleMetrics, granularity, dateRange, filters, hideZero, splitKey, dataReloadKey])

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

  /* ---- backpopulation (runs the chart's default window immediately) ---- */
  const onBackpopulate = useCallback(async () => {
    setBackpopBusy(true); setToast('Backpopulation started…')
    try {
      const run = await api.backpopulate(chartId)
      setToast(`Backpopulation ${run.status} · ${run.row_count.toLocaleString()} rows (${run.from_date} → ${run.to_date})`)
      setDataReloadKey((k) => k + 1) // refetch data over the current range
    } catch (e: any) {
      setToast(`Backpopulation failed: ${String(e.message || e).slice(0, 160)}`)
    } finally {
      setBackpopBusy(false)
      window.setTimeout(() => setToast(null), 7000)
    }
  }, [chartId])

  /* ---- data recency offset: chart data ends `endOffset` days before today ---- */
  const onEndOffsetChange = useCallback((n: number) => {
    setEndOffset(n)
    setDateRange((r) => ({ ...r, end: todayMinus(n) }))
  }, [])

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
    dimensions: cfg!.dimensions.map((d) => ({ name: d.name, column_name: d.column_name })),
    metrics: uiMetrics.map<MetricCfg>((m) => ({
      name: m.name,
      column_name: m.formula ? null : (m.columnName ?? m.name),
      independent_dimensions: m.independentFields || [],
      formula: m.formula ? stripBrackets(m.formula) : null,
      y_axis: m.axis || 'primary',
      decimals: m.decimals ?? 0,
      unit: m.unit && m.unit !== 'None' ? m.unit : null,
    })),
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

  return (
    <ChartView
      title={title} chartId={chartId} charts={charts} onSelectChart={onSelectChart}
      onGoHome={onGoHome} onEditChart={onEditChart} onCreateChart={onCreateChart} freshness={freshness}
      chartType={chartType} onChartTypeChange={setChartType}
      granularity={granularity} onGranularityChange={setGranularity}
      dateRange={dateRange} onDateRangeChange={(s, e) => setDateRange({ start: s, end: e })}
      dimensions={dimensions} allToggle={allToggle}
      onDimensionToggleValue={onDimensionToggleValue} onDimensionSetAll={onDimensionSetAll}
      onDimensionToggleSplit={onDimensionToggleSplit} splitNotice={splitNotice}
      onAllToggle={onAllToggle} onAddDimension={() => alert('Add dimension is configured in the Query Editor (Phase 9).')}
      metrics={metrics} metricSearch={metricSearch} onMetricSearchChange={setMetricSearch}
      onMetricToggle={onMetricToggle} onMetricsToggleAll={onMetricsToggleAll}
      onOpenMetricSettings={onOpenMetricSettings} onAddMetric={onAddMetric} onReorderMetrics={onReorderMetrics}
      hideZero={hideZero} onHideZeroToggle={setHideZero}
      chartData={chartData} chartSeries={chartSeries}
      onBackpopulate={onBackpopulate} backpopBusy={backpopBusy}
      endOffset={endOffset} onEndOffsetChange={onEndOffsetChange} onShare={onShare} toast={toast}
      metricsTab={metricsTab} onMetricsTabChange={setMetricsTab}
      settingsMetric={settingsMetric} settingsOpen={settingsMetric != null} settingsError={settingsError}
      onCloseSettings={onCloseSettings}
      onApplySettings={(d) => applyDraft(d, false)} onSaveSettings={(d) => applyDraft(d, true)}
      loading={loading} error={error}
    />
  )
}
