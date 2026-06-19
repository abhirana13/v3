import { Checkbox, Ic } from '../../components/primitives'
import { Switch } from './Switch'
import type { ChartOptions, UIMetric } from '../../components/types'

const OPTION_ROWS: { key: keyof ChartOptions; label: string }[] = [
  { key: 'showLegend', label: 'Show legend' },
  { key: 'smooth', label: 'Smooth lines' },
  { key: 'showPoints', label: 'Show data points' },
  { key: 'connectNulls', label: 'Connect gaps (nulls)' },
  { key: 'gridlines', label: 'Gridlines' },
  { key: 'zeroBase', label: 'Start Y-axis at zero' },
  { key: 'logScale', label: 'Logarithmic Y-axis' },
]

/* -------------------------------------------------------------- MetricsPanel */
export function MetricsPanel({ metrics, search, onSearchChange, onToggleMetric, onToggleAll, onOpenSettings, onAddMetric, onOpenOrder, hideZero, onHideZeroToggle, activeTab = 'Metrics', onTabChange, chartOptions, onChartOption, ma, maWindow, onMaToggle, onMaWindow, maUnit, xDim, onXDim, xAxisDims, xHint }: {
  metrics: UIMetric[]
  search: string
  onSearchChange: (v: string) => void
  onToggleMetric: (id: string) => void
  onToggleAll: (on: boolean) => void
  onOpenSettings: (id: string) => void
  onAddMetric: () => void
  onOpenOrder: () => void
  hideZero: boolean
  onHideZeroToggle: (on: boolean) => void
  activeTab?: string
  onTabChange?: (t: string) => void
  chartOptions: ChartOptions
  onChartOption: (k: keyof ChartOptions, v: boolean) => void
  ma: boolean
  maWindow: number
  onMaToggle: (v: boolean) => void
  onMaWindow: (n: number) => void
  maUnit: string
  xDim: string
  onXDim: (v: string) => void
  xAxisDims: { key: string; label: string }[]
  xHint: string | null
}) {
  const tabs = ['Metrics', 'Annotations', 'Options']
  const filtered = metrics.filter((m) => m.name.toLowerCase().includes((search || '').toLowerCase()))
  const allVisible = metrics.length > 0 && metrics.every((m) => m.visible)
  const someVisible = metrics.some((m) => m.visible)

  return (
    <aside className="flex w-[300px] shrink-0 flex-col border-l border-slate-200 bg-white">
      <div className="flex items-center gap-5 border-b border-slate-200 px-4">
        {tabs.map((t) => (
          <button key={t} onClick={() => onTabChange && onTabChange(t)}
            className={'relative py-3 text-[13px] font-medium transition-colors ' + (activeTab === t ? 'text-sky-600' : 'text-slate-500 hover:text-slate-700')}>
            {t}
            {activeTab === t && <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-sky-500" />}
          </button>
        ))}
      </div>
      {activeTab === 'Metrics' ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-2 px-4 pt-3">
            <button onClick={onAddMetric} className="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-sky-500 py-2 text-[13px] font-semibold text-white shadow-sm transition-colors hover:bg-sky-600"><Ic.plus /> Add</button>
            <button onClick={onOpenOrder} className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-slate-200 py-2 text-[13px] font-medium text-slate-600 hover:bg-slate-50"><Ic.sort /> Order</button>
          </div>
          <div className="px-4 pt-2.5">
            <div className="flex items-center gap-2 rounded-md border border-slate-200 px-2.5 py-1.5 focus-within:border-sky-400 focus-within:ring-2 focus-within:ring-sky-100">
              <Ic.search className="text-slate-400" />
              <input value={search} onChange={(e) => onSearchChange(e.target.value)} placeholder="Search metrics" className="w-full bg-transparent text-[13px] text-slate-700 outline-none placeholder:text-slate-400" />
            </div>
          </div>
          <div className="mt-2 flex items-center justify-between px-4 py-1.5">
            <label className="flex cursor-pointer items-center gap-2.5 text-[13px] font-medium text-slate-700">
              <Checkbox checked={allVisible} indeterminate={someVisible && !allVisible} onChange={onToggleAll} />All
            </label>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto pb-2">
            {filtered.map((m) => (
              <div key={m.id} className="group flex items-center gap-2.5 px-4 py-2 hover:bg-slate-50">
                <Checkbox checked={m.visible} onChange={() => onToggleMetric(m.id)} />
                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: m.visible ? m.color : '#cbd5e1' }} />
                <span className={'flex-1 truncate text-[13px] ' + (m.visible ? 'text-slate-800' : 'text-slate-500')}>{m.name}</span>
                {m.formula && <span className="rounded bg-slate-100 px-1.5 py-px text-[10px] font-medium text-slate-400" title={m.formula}>ƒ</span>}
                <button onClick={() => onOpenSettings(m.id)} title="Metric settings" className="text-sky-500 opacity-70 transition-opacity hover:opacity-100"><Ic.gear /></button>
              </div>
            ))}
            {filtered.length === 0 && <div className="px-4 py-6 text-center text-[13px] text-slate-400">No metrics match “{search}”.</div>}
          </div>
          <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3">
            <span className="text-[13px] text-slate-600">Hide zero values</span>
            <button role="switch" aria-checked={hideZero} onClick={() => onHideZeroToggle(!hideZero)} className={'relative h-5 w-9 rounded-full transition-colors ' + (hideZero ? 'bg-sky-500' : 'bg-slate-200')}>
              <span className={'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ' + (hideZero ? 'left-[18px]' : 'left-0.5')} />
            </button>
          </div>
        </div>
      ) : activeTab === 'Options' ? (
        <div className="min-h-0 flex-1 overflow-y-auto pb-2">
          <div className="px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Axes</div>
          <div className="flex items-center justify-between px-4 py-2.5">
            <span className="text-[13px] text-slate-700">X-axis</span>
            <select value={xDim} onChange={(e) => onXDim(e.target.value)} className="rounded-md border border-slate-200 px-2 py-1 text-[13px] text-slate-700 outline-none focus:border-sky-400 focus:ring-2 focus:ring-sky-100">
              <option value="">Time</option>
              {xAxisDims.map((dm) => <option key={dm.key} value={dm.key}>{dm.label}</option>)}
            </select>
          </div>
          {xHint && <div className="mx-4 mb-1 rounded-md bg-amber-50 px-3 py-1.5 text-[11px] text-amber-600">{xHint}</div>}

          <div className="border-t border-slate-100 px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Display</div>
          {OPTION_ROWS.map((r) => (
            <div key={r.key} className="flex items-center justify-between px-4 py-2.5 hover:bg-slate-50">
              <span className="text-[13px] text-slate-700">{r.label}</span>
              <Switch on={!!chartOptions[r.key]} onChange={(v) => onChartOption(r.key, v)} />
            </div>
          ))}

          <div className="border-t border-slate-100 px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Moving average</div>
          <div className="flex items-center justify-between px-4 py-2.5 hover:bg-slate-50">
            <span className="text-[13px] text-slate-700">Enabled</span>
            <Switch on={ma} onChange={onMaToggle} />
          </div>
          <div className="flex items-center justify-between px-4 pb-2.5">
            <span className="text-[13px] text-slate-700">Window</span>
            <div className="flex items-center gap-1.5">
              <input type="number" min={1} value={maWindow} onChange={(e) => onMaWindow(Math.max(1, Number(e.target.value) || 1))} className="w-16 rounded-md border border-slate-200 px-2 py-1 text-right text-[13px] text-slate-700 outline-none focus:border-sky-400 focus:ring-2 focus:ring-sky-100" />
              <span className="text-[12px] text-slate-400">{maUnit}</span>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center p-6 text-center text-[13px] text-slate-400">Annotations — out of scope for v1.</div>
      )}
    </aside>
  )
}
