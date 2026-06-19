import { useEffect, useMemo, useRef, useState } from 'react'
import { Dropdown, Ic } from '../../components/primitives'
import { DateRangePicker } from '../../components/DateRangePicker'
import type { ChartOptions, ChartRow, UIDimension, UIMetric, UISeries } from '../../components/types'
import type { Freshness } from '../../api/types'
import { TimeSeriesChart } from '../../charts/TimeSeriesChart'
import { ExportMenu } from './ExportMenu'
import { FreshnessChip } from './FreshnessChip'
import { downloadCsv, downloadPng } from './exportChart'
import { ChartPicker } from './ChartPicker'
import { DimensionFilterBar } from './DimensionFilterBar'
import { MetricsPanel } from './MetricsPanel'
import { MetricSettingsModal } from './MetricSettingsModal'
import type { MetricDraft } from './MetricSettingsModal'
import { MetricOrderModal } from './MetricOrderModal'
import { ChartToolsRail } from './ChartToolsRail'
import { DataTablePanel } from './DataTablePanel'
import { applyMovingAverage, applyPercentage, buildCategorical } from './transforms'

const DEFAULT_CHART_OPTIONS: ChartOptions = { showLegend: true, smooth: false, showPoints: false, connectNulls: false, gridlines: true, zeroBase: false, logScale: false }

/* ------------------------------------------------------------------ ChartView */
export interface ChartViewProps {
  title: string; chartId: number | string; status?: string
  chartType: string; onChartTypeChange: (v: string) => void
  granularity: string; onGranularityChange: (v: string) => void
  dateRange: { start: string; end: string }; onDateRangeChange: (s: string, e: string) => void
  dimensions: UIDimension[]; allToggle: boolean
  onDimensionToggleValue: (k: string, v: string) => void
  onDimensionSetAll: (k: string, on: boolean) => void
  onDimensionToggleSplit: (k: string) => void
  onAllToggle: (on: boolean) => void; onAddDimension: () => void
  splitNotice?: string | null
  metrics: UIMetric[]; metricSearch: string; onMetricSearchChange: (v: string) => void
  onMetricToggle: (id: string) => void; onMetricsToggleAll: (on: boolean) => void
  onOpenMetricSettings: (id: string) => void; onAddMetric: () => void
  onReorderMetrics: (orderedIds: string[]) => void
  hideZero: boolean; onHideZeroToggle: (on: boolean) => void
  chartData: ChartRow[]; chartSeries: UISeries[]
  onBackpopulate: () => void; backpopBusy?: boolean
  endOffset: number; onEndOffsetChange: (n: number) => void; onShare: () => void
  toast?: string | null
  metricsTab: string; onMetricsTabChange: (t: string) => void
  settingsMetric: MetricDraft | null; settingsOpen: boolean; settingsError?: string | null
  onCloseSettings: () => void; onApplySettings: (d: MetricDraft) => void; onSaveSettings: (d: MetricDraft) => void
  loading?: boolean; error?: string | null
  charts: { id: number; name: string; number?: number | null; certified?: boolean }[]; onSelectChart: (id: number) => void
  onGoHome: () => void
  onEditChart: (id: number) => void; onCreateChart: () => void
  freshness?: Freshness | null
}

export function ChartView(p: ChartViewProps) {
  const seriesType = p.chartType === 'Bar Chart' ? 'bar' : p.chartType === 'Area Chart' ? 'area' : 'line'
  const currentChart = p.charts.find((c) => String(c.id) === String(p.chartId))
  const pngRef = useRef<(() => string | null) | null>(null)
  const fileBase = `${currentChart?.number ?? p.chartId}-${p.title}`
  const [tableOpen, setTableOpen] = useState(false)
  const [pct, setPct] = useState(false)
  const [ma, setMa] = useState(false)
  const [maWindow, setMaWindow] = useState(7)
  const [fullscreen, setFullscreen] = useState(false)
  const [opts, setOpts] = useState<ChartOptions>(DEFAULT_CHART_OPTIONS)
  const [orderOpen, setOrderOpen] = useState(false)
  const [hidden, setHidden] = useState<Set<string>>(new Set()) // legend series hidden from the chart (visual only, no refetch)
  const maUnit = p.granularity === 'Week' ? 'weeks' : p.granularity === 'Month' ? 'months' : 'days'
  const [xDim, setXDim] = useState('') // '' = Time; else a dimension key on the X-axis
  const xDimObj = xDim ? p.dimensions.find((d) => d.key === xDim) : undefined
  const xActive = !!xDim && !!xDimObj?.split
  const xGuard = xDim && !xActive ? `Uncheck “${xDimObj?.label || xDim}” in the filter bar to use it as the X-axis.` : null
  const xAxisDims = p.dimensions.map((d) => ({ key: d.key, label: d.label }))

  useEffect(() => {
    if (!fullscreen) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') setFullscreen(false) }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [fullscreen])

  // switching charts clears any per-series hide state
  useEffect(() => { setHidden(new Set()) }, [p.chartId])

  // legendSeries = every series (drives the legend). The chart drops hidden ones
  // BEFORE the % / MA transforms, so a 100%-stack re-normalizes over what's visible.
  const { displayData, displaySeries, legendSeries } = useMemo(() => {
    const base = xActive ? buildCategorical(p.chartData, p.chartSeries) : { data: p.chartData, series: p.chartSeries }
    let data = base.data
    let series = base.series.filter((s) => !hidden.has(s.key))
    if (!xActive) {
      if (pct) { const o = applyPercentage(data, series); data = o.data; series = o.series }
      if (ma) data = applyMovingAverage(data, series, maWindow)
    }
    return { displayData: data, displaySeries: series, legendSeries: base.series }
  }, [p.chartData, p.chartSeries, pct, ma, maWindow, xActive, hidden])

  const toggleHidden = (key: string) => setHidden((prev) => { const n = new Set(prev); if (n.has(key)) n.delete(key); else n.add(key); return n })
  const showAllSeries = () => setHidden(new Set())
  const hideAllSeries = () => setHidden(new Set(legendSeries.map((s) => s.key)))
  const allLinesHidden = legendSeries.length > 0 && legendSeries.every((s) => hidden.has(s.key))
  const onToggleAllLines = () => (allLinesHidden ? showAllSeries() : hideAllSeries())

  const onExportPng = () => { const url = pngRef.current?.(); if (url) downloadPng(fileBase, url) }
  const onExportCsv = () => downloadCsv(fileBase, displayData, displaySeries)

  const legend = (
    <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5">
      {legendSeries.map((s) => {
        const off = hidden.has(s.key)
        return (
          <button key={s.key} type="button" onClick={() => toggleHidden(s.key)} title={off ? 'Click to show' : 'Click to hide'}
            className={'flex items-center gap-1.5 text-[12px] transition ' + (off ? 'text-slate-400 line-through opacity-50' : 'text-slate-600 hover:text-slate-900')}>
            <span className="h-[3px] w-4 rounded-full" style={{ background: off ? '#cbd5e1' : s.color }} />{s.label}
          </button>
        )
      })}
    </div>
  )
  const maControl = ma ? (
    <div className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-500 shadow-sm">
      <span className="font-medium">MA window</span>
      <input type="number" min={1} value={maWindow} onChange={(e) => setMaWindow(Math.max(1, Number(e.target.value) || 1))} className="w-12 bg-transparent text-[11px] font-semibold text-slate-700 outline-none" />
      <span>{maUnit}</span>
    </div>
  ) : null
  const chartBody = p.error ? (
    <div className="flex h-full items-center justify-center px-6 text-center text-[13px] text-rose-500">{p.error}</div>
  ) : xGuard ? (
    <div className="flex h-full items-center justify-center px-6 text-center text-[13px] text-amber-600">{xGuard}</div>
  ) : p.splitNotice ? (
    <div className="flex h-full items-center justify-center px-6 text-center text-[13px] text-amber-600">{p.splitNotice}</div>
  ) : p.chartSeries.length === 0 ? (
    <div className="flex h-full items-center justify-center text-[13px] text-slate-400">Select a metric to plot.</div>
  ) : p.chartData.length === 0 ? (
    <div className="flex h-full items-center justify-center text-[13px] text-slate-400">No data in this range.</div>
  ) : (
    <TimeSeriesChart data={displayData} series={displaySeries} seriesType={seriesType} percentStacked={pct && !xActive} granularity={p.granularity} display={opts} categorical={xActive} xLabel={xActive ? (xDimObj?.label || 'Dimension').toUpperCase() : 'TIME'} yLabelPrimary={displaySeries.find((s) => s.axis === 'primary')?.label} pngRef={pngRef} />
  )

  return (
    <div className="flex h-full flex-col bg-slate-50 font-sans text-slate-800">
      {/* slim brand strip + chart selector (replaces the design's multi-page nav) */}
      <header className="flex h-12 shrink-0 items-center gap-4 bg-slate-800 px-4 text-slate-200">
        <div className="flex h-7 w-7 items-center justify-center rounded bg-sky-500 text-xs font-bold text-white">FG</div>
        <span className="text-[13px] font-semibold text-white">Analytics</span>
        <button onClick={p.onGoHome} title="All charts" className="flex items-center rounded-md border border-slate-600 bg-slate-700/70 px-2 py-1.5 text-slate-200 hover:bg-slate-700"><Ic.home /></button>
        <ChartPicker charts={p.charts} currentId={p.chartId} onSelect={p.onSelectChart} />
        <button onClick={() => p.onCreateChart()} className="ml-auto rounded-md bg-sky-500 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-sky-600">+ New chart</button>
      </header>

      {/* chart title bar */}
      <div className="flex shrink-0 items-center gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
        <span className={'flex h-4 w-4 items-center justify-center rounded-full text-[10px] text-white ' + (p.error ? 'bg-rose-500' : 'bg-emerald-500')}>{p.error ? '!' : '✓'}</span>
        <h1 className="text-[15px] font-semibold">
          <button onClick={() => p.onEditChart(Number(p.chartId))} title="Open the query editor / configure" className="group inline-flex items-center gap-1.5 text-slate-800 hover:text-sky-600">
            <span className="text-slate-400 group-hover:text-sky-400">{currentChart?.number ?? p.chartId} :</span>
            <span className="group-hover:underline">{p.title}</span>
            <Ic.gear className="text-slate-400 opacity-0 transition-opacity group-hover:opacity-100" />
          </button>
        </h1>
        {currentChart?.certified && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-600" title="Certified chart">✓ Certified</span>}
        <span className={'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-medium ' + (p.error ? 'bg-rose-50 text-rose-600' : 'bg-emerald-50 text-emerald-600')}>
          <span className={'h-1.5 w-1.5 rounded-full ' + (p.error ? 'bg-rose-500' : 'bg-emerald-500')} /> {p.loading ? 'Loading…' : p.error ? 'Error' : (p.status || 'Success')}
        </span>
        {p.freshness && <FreshnessChip freshness={p.freshness} />}
        <div className="ml-auto flex items-center gap-2">
          <Dropdown value={p.chartType} onChange={p.onChartTypeChange} options={['Line Chart', 'Bar Chart', 'Area Chart']} icon={<Ic.line />} />
          <Dropdown value={p.granularity} onChange={p.onGranularityChange} options={['Day', 'Week', 'Month']} />
          <DateRangePicker value={p.dateRange} onChange={(r) => p.onDateRangeChange(r.start, r.end)} align="right" widthClass="w-[256px]" />
          <Dropdown value={String(p.endOffset)} onChange={(v) => p.onEndOffsetChange(Number(v))} options={['0', '1', '2', '3', '7', '14']} title="Load data up to this many days before today" />
          <ExportMenu onPng={onExportPng} onCsv={onExportCsv} />
          <button onClick={p.onShare} className="flex items-center gap-1.5 rounded-md bg-sky-500 px-3 py-[7px] text-[13px] font-semibold text-white hover:bg-sky-600" title="Copy a shareable link to this chart"><Ic.link /> Share</button>
        </div>
      </div>

      <DimensionFilterBar
        dimensions={p.dimensions} allToggle={p.allToggle}
        onToggleValue={p.onDimensionToggleValue} onSetAll={p.onDimensionSetAll}
        onToggleSplit={p.onDimensionToggleSplit}
        onAllToggle={p.onAllToggle} onAddDimension={p.onAddDimension}
      />

      <div className="flex min-h-0 flex-1">
        <main className="relative flex min-w-0 flex-1 flex-col bg-white p-4">
          {maControl && <div className="absolute left-4 top-3 z-10">{maControl}</div>}
          {opts.showLegend && <div className="mb-1 flex items-center justify-center">{legend}</div>}
          <div className="min-h-0 w-full flex-1">
            {fullscreen ? <div className="flex h-full items-center justify-center text-[13px] text-slate-400">Chart is in fullscreen.</div> : chartBody}
          </div>
          {tableOpen && !fullscreen && <DataTablePanel data={p.chartData} series={p.chartSeries} onClose={() => setTableOpen(false)} />}
        </main>

        <ChartToolsRail
          tableOpen={tableOpen} pct={pct} ma={ma} fullscreen={fullscreen} backpopBusy={p.backpopBusy}
          linesAllHidden={allLinesHidden} linesCount={legendSeries.length}
          onTable={() => setTableOpen((o) => !o)} onPct={() => setPct((o) => !o)} onMa={() => setMa((o) => !o)}
          onBackpop={p.onBackpopulate} onFullscreen={() => setFullscreen((o) => !o)} onToggleAllLines={onToggleAllLines}
        />

        <MetricsPanel
          metrics={p.metrics} search={p.metricSearch} onSearchChange={p.onMetricSearchChange}
          onToggleMetric={p.onMetricToggle} onToggleAll={p.onMetricsToggleAll}
          onOpenSettings={p.onOpenMetricSettings} onAddMetric={p.onAddMetric} onOpenOrder={() => setOrderOpen(true)}
          hideZero={p.hideZero} onHideZeroToggle={p.onHideZeroToggle}
          activeTab={p.metricsTab} onTabChange={p.onMetricsTabChange}
          chartOptions={opts} onChartOption={(k, v) => setOpts((o) => ({ ...o, [k]: v }))}
          ma={ma} maWindow={maWindow} onMaToggle={(v) => setMa(v)} onMaWindow={(n) => setMaWindow(n)} maUnit={maUnit}
          xDim={xDim} onXDim={setXDim} xAxisDims={xAxisDims} xHint={xGuard}
        />
      </div>

      {fullscreen && (
        <div className="fixed inset-0 z-[60] flex flex-col bg-white p-5">
          <div className="mb-2 flex items-center gap-3">
            <h2 className="text-[14px] font-semibold text-slate-700">{p.title}</h2>
            {maControl}
            <button onClick={() => setFullscreen(false)} className="ml-auto flex items-center gap-1.5 rounded-md border border-slate-200 px-3 py-1.5 text-[13px] font-medium text-slate-600 hover:bg-slate-50"><Ic.close /> Exit fullscreen</button>
          </div>
          {opts.showLegend && <div className="mb-1 flex justify-center">{legend}</div>}
          <div className="min-h-0 w-full flex-1">{chartBody}</div>
          {tableOpen && <DataTablePanel data={p.chartData} series={p.chartSeries} onClose={() => setTableOpen(false)} />}
        </div>
      )}

      {p.toast && (
        <div className="pointer-events-none fixed bottom-5 left-1/2 z-[70] -translate-x-1/2 rounded-lg bg-slate-800 px-4 py-2 text-[13px] font-medium text-white shadow-lg">{p.toast}</div>
      )}

      <MetricSettingsModal
        open={p.settingsOpen} metric={p.settingsMetric} dimensionNames={p.dimensions.map((d) => d.label)}
        error={p.settingsError} onClose={p.onCloseSettings} onApply={p.onApplySettings} onSave={p.onSaveSettings}
      />

      <MetricOrderModal open={orderOpen} metrics={p.metrics} onClose={() => setOrderOpen(false)} onSave={(ids) => { p.onReorderMetrics(ids); setOrderOpen(false) }} />
    </div>
  )
}
