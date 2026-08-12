import { useEffect, useMemo, useRef, useState } from 'react'
import RGL, { WidthProvider } from 'react-grid-layout'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import { DateRangePicker, type DateRange } from '../../components/DateRangePicker'
import type { ChartWidgetData, DashWidget, FilterValue, NumberWidgetData } from '../../api/types'
import { ChartWidgetCard, NumberWidget } from './widgets'

/* Dashboard read view, ported from the Claude Design handoff (dashboard.jsx):
   header + tab bar + controls row + global filter bar + react-grid-layout grid
   (static in read mode). Dumb — all data via props, actions via callbacks. */

const GridLayout = WidthProvider(RGL)

const Ic = {
  caret: (p: any) => (<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M4 6l4 4 4-4" /></svg>),
  funnel: (p: any) => (<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M3 5h18l-7 8v6l-4 2v-8z" /></svg>),
  copy: (p: any) => (<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>),
  home: (p: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M3 10.5L12 3l9 7.5" /><path d="M5.5 9.5V20h13V9.5" /></svg>),
}

export function Checkbox({ checked, indeterminate, onChange }: { checked: boolean; indeterminate?: boolean; onChange?: (v: boolean) => void }) {
  return (
    <span
      role="checkbox" aria-checked={indeterminate ? 'mixed' : checked}
      onClick={(e) => { e.stopPropagation(); onChange && onChange(!checked) }}
      className={'inline-flex h-4 w-4 shrink-0 cursor-pointer items-center justify-center rounded-[4px] border transition-colors ' +
        (checked || indeterminate ? 'border-sky-500 bg-sky-500 text-white' : 'border-slate-300 bg-white hover:border-slate-400')}>
      {checked && <svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M3.5 8.5l3 3 6-7" /></svg>}
      {indeterminate && !checked && <span className="h-[2px] w-2 rounded bg-white" />}
    </span>
  )
}

/* ---------- header ---------- */

export function DashboardHeader({ title, number, enabled, onReplicate, onGoHome, onEdit }: {
  title: string; number: number | null; enabled: boolean
  onReplicate: () => void; onGoHome: () => void; onEdit: () => void
}) {
  return (
    <div className="flex items-center gap-3 px-6 pt-4 pb-1">
      <button onClick={onGoHome} title="All dashboards" className="flex h-7 w-7 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-600"><Ic.home /></button>
      <h1 className="truncate text-[22px] font-bold tracking-tight text-slate-900">{title}</h1>
      {number != null && <span className="text-sm font-medium text-slate-400">#{number}</span>}
      {enabled && (
        <span className="inline-flex items-center gap-1.5 rounded bg-green-50 px-2 py-0.5 text-xs font-semibold text-green-600">
          <span className="h-1.5 w-1.5 rounded-full bg-green-500" />Enabled
        </span>
      )}
      <div className="ml-auto flex items-center gap-2">
        <button onClick={onReplicate} className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
          <Ic.copy />Replicate
        </button>
        <button onClick={onEdit} className="rounded-md bg-sky-600 px-3.5 py-1.5 text-sm font-medium text-white hover:bg-sky-700">
          Edit Dashboard
        </button>
      </div>
    </div>
  )
}

/* ---------- tabs ---------- */

export function TabBar({ tabs, activeId, onTabChange }: {
  tabs: { id: number; name: string }[]; activeId: number | null; onTabChange: (id: number) => void
}) {
  return (
    <div className="flex items-end gap-6 border-b border-slate-200 px-6">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onTabChange(t.id)}
          className={'-mb-px border-b-2 px-0.5 pb-2.5 pt-1 text-[13px] font-medium transition-colors ' +
            (t.id === activeId ? 'border-sky-600 text-sky-600' : 'border-transparent text-slate-500 hover:text-slate-700')}>
          {t.name}
        </button>
      ))}
    </div>
  )
}

/* ---------- controls row ---------- */

const GRAN_LABEL: Record<string, string> = { day: 'Day', week: 'Week', month: 'Month' }

const OFFSETS = [0, 1, 2, 3, 7, 14]

export function ControlsRow({ granularity, dateRange, movingAvg, endOffset, onGranularityChange, onDateRangeChange, onToggleMovingAvg, onEndOffsetChange }: {
  granularity: string; dateRange: DateRange; movingAvg: boolean; endOffset: number
  onGranularityChange: (g: string) => void; onDateRangeChange: (r: DateRange) => void; onToggleMovingAvg: (on: boolean) => void
  onEndOffsetChange: (n: number) => void
}) {
  const [open, setOpen] = useState(false)
  const [offOpen, setOffOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const offRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
      if (offRef.current && !offRef.current.contains(e.target as Node)) setOffOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  return (
    <div className="flex items-center px-6 pt-3">
      <button onClick={() => onToggleMovingAvg(!movingAvg)}
        title="Plot every chart as a trailing 7-day moving average"
        className={'inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[13px] font-medium transition-colors ' +
          (movingAvg ? 'border-sky-400 bg-sky-50 text-sky-700' : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300')}>
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 15c3 0 4-8 7-8s4 8 7 8 4-3 4-3" /></svg>
        7-day avg
      </button>
      <div className="ml-auto flex items-center gap-2">
        <div ref={ref} className="relative">
          <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-600 hover:border-slate-300">
            {GRAN_LABEL[granularity] || granularity}<Ic.caret className="text-slate-400" />
          </button>
          {open && (
            <div className="absolute right-0 top-full z-30 mt-1 w-32 rounded-md border border-slate-200 bg-white py-1 shadow-lg">
              {(['day', 'week', 'month'] as const).map((g) => (
                <button key={g} onClick={() => { setOpen(false); onGranularityChange(g) }}
                  className={'block w-full px-3 py-1.5 text-left text-[13px] hover:bg-slate-50 ' + (g === granularity ? 'font-semibold text-sky-600' : 'text-slate-600')}>
                  {GRAN_LABEL[g]}
                </button>
              ))}
            </div>
          )}
        </div>
        <DateRangePicker value={dateRange} onChange={onDateRangeChange} align="right" />
        {/* Recency cap, matching the chart view's control: data is never loaded more recently
            than today - offset, whatever the picked end date says. Seeded from the dashboard's
            default_end_offset_days; changing it here is view-only and not persisted. */}
        <div ref={offRef} className="relative">
          <button onClick={() => setOffOpen(!offOpen)}
            title="Load data up to this many days before today"
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-600 hover:border-slate-300">
            {endOffset}<Ic.caret className="text-slate-400" />
          </button>
          {offOpen && (
            <div className="absolute right-0 top-full z-30 mt-1 w-32 rounded-md border border-slate-200 bg-white py-1 shadow-lg">
              {OFFSETS.map((n) => (
                <button key={n} onClick={() => { setOffOpen(false); onEndOffsetChange(n) }}
                  className={'block w-full px-3 py-1.5 text-left text-[13px] hover:bg-slate-50 ' + (n === endOffset ? 'font-semibold text-sky-600' : 'text-slate-600')}>
                  {n === 0 ? 'today' : `${n} day${n === 1 ? '' : 's'} back`}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/* ---------- global filter bar ---------- */

export interface FilterChipState {
  dimension: string
  options: FilterValue[]
  // null  => All: no value constraint. Distinct from [] so that "everything" survives the
  //           async options fetch (a chip created before its options land is All, not None) and
  //           so a saved default_values of [] keeps meaning All rather than blanking widgets.
  // []    => None: explicitly no values. The backend turns this into an empty result set.
  // list  => a partial selection.
  selected: FilterValue[] | null
  split: boolean          // unchecked chip => split every chart widget by this dimension
}

// Horizontal drag-to-reorder wiring (edit mode only) — the whole chip is the
// drag source; a grip is shown as the affordance.
export interface ChipDrag {
  onDragStart: (e: React.DragEvent) => void
  onDragEnter: (e: React.DragEvent) => void
  onDragEnd: (e: React.DragEvent) => void
  dragging: boolean
}

// A dimension filter chip, mirroring the chart view's DimensionChip:
//  - leading checkbox = SPLIT toggle (checked = aggregated; UNCHECK to split all
//    chart widgets by this dimension), badge reads "Split" / "Split · N";
//  - value dropdown filters values (whole-row toggle, "All" clears). Empty = All,
//    so an All chip shows every value checked and unchecking one narrows.
export function FilterChip({ chip, onFilterChange, onToggleSplit, onRemove, drag }: {
  chip: FilterChipState
  onFilterChange: (dim: string, values: FilterValue[] | null) => void
  onToggleSplit: (dim: string) => void
  onRemove?: (dim: string) => void // edit mode: delete this chip from the dashboard
  drag?: ChipDrag // edit mode: drag-to-reorder
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  const total = chip.options.length
  // Mirrors the chart view's chip: every box ticked = All, none ticked = None. Ticks read from
  // `selected`, with null standing in for "all ticked".
  const isAll = chip.selected === null || (total > 0 && chip.selected.length === total)
  const isNone = chip.selected !== null && chip.selected.length === 0
  const sel = chip.selected === null ? total : chip.selected.length
  const badge = chip.split
    ? (isAll ? 'Split' : isNone ? 'Split · none' : `Split · ${sel}`)
    : (isAll ? 'All' : isNone ? 'None' : String(sel))
  const isSel = (v: FilterValue) => (chip.selected === null ? true : chip.selected.some((x) => x === v))
  const toggleVal = (v: FilterValue) => {
    const cur = chip.selected === null ? chip.options : chip.selected
    const has = cur.some((x) => x === v)
    const next = has ? cur.filter((x) => x !== v) : [...cur, v]
    // re-ticking everything collapses back to the All sentinel, so it persists as "no default"
    // rather than as an explicit list that would freeze today's value set into the dashboard
    onFilterChange(chip.dimension, total > 0 && next.length === total ? null : next)
  }
  return (
    <div ref={ref}
      draggable={!!drag}
      onDragStart={drag?.onDragStart}
      onDragEnter={drag?.onDragEnter}
      onDragOver={drag ? (e) => e.preventDefault() : undefined}
      onDragEnd={drag?.onDragEnd}
      className={'relative inline-flex items-center gap-2 rounded-md border bg-white py-[5px] pr-1.5 text-[13px] transition-opacity ' + (drag ? 'cursor-grab pl-1 active:cursor-grabbing ' : 'pl-2 ') + (drag?.dragging ? 'opacity-40 ' : '') + (open ? 'border-sky-400 ring-2 ring-sky-100' : chip.split ? 'border-violet-400 ring-2 ring-violet-100' : 'border-slate-200 hover:border-slate-300')}>
      {drag && (
        <span title="Drag to reorder" className="text-slate-300">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><circle cx="9" cy="6" r="1.4" /><circle cx="15" cy="6" r="1.4" /><circle cx="9" cy="12" r="1.4" /><circle cx="15" cy="12" r="1.4" /><circle cx="9" cy="18" r="1.4" /><circle cx="15" cy="18" r="1.4" /></svg>
        </span>
      )}
      <Checkbox checked={!chip.split} onChange={() => onToggleSplit(chip.dimension)} />
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex items-center gap-2">
        <span className="font-medium text-slate-700">{chip.dimension}</span>
        <span className={'rounded px-1.5 py-[1px] text-[11px] font-semibold ' + (chip.split ? 'bg-violet-100 text-violet-700' : isAll ? 'bg-slate-100 text-slate-500' : isNone ? 'bg-rose-100 text-rose-700' : 'bg-sky-100 text-sky-700')}>
          {badge}
        </span>
        <Ic.caret className="text-slate-400" />
      </button>
      {onRemove && (
        <button type="button" onClick={() => onRemove(chip.dimension)} title="Remove filter from dashboard"
          className="flex h-5 w-5 items-center justify-center rounded text-slate-300 hover:bg-rose-50 hover:text-rose-500">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      )}
      {open && (
        <div className="absolute left-0 top-[calc(100%+6px)] z-30 w-56 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Filter values</span>
            <div className="flex gap-2 text-[11px] font-semibold text-sky-600">
              <button type="button" className="hover:underline disabled:text-slate-300"
                disabled={isAll} onClick={() => onFilterChange(chip.dimension, null)}>All</button>
              <button type="button" className="hover:underline disabled:text-slate-300"
                disabled={isNone} onClick={() => onFilterChange(chip.dimension, [])}>None</button>
            </div>
          </div>
          <div className="max-h-60 overflow-y-auto py-1">
            {total === 0 && <div className="px-3 py-2 text-[12px] text-slate-400">No values in cache</div>}
            {chip.options.map((v) => (
              // whole row toggles
              <div key={String(v)} onClick={() => toggleVal(v)}
                className="flex cursor-pointer items-center gap-2.5 px-3 py-1.5 text-[13px] text-slate-700 hover:bg-slate-50">
                <Checkbox checked={isSel(v)} onChange={() => toggleVal(v)} />
                <span className="truncate">{String(v)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export function GlobalFilterBar({ chips, dirty, onFilterChange, onToggleSplit, onApply }: {
  chips: FilterChipState[]
  dirty: boolean
  onFilterChange: (dim: string, values: FilterValue[] | null) => void
  onToggleSplit: (dim: string) => void
  onApply: () => void
}) {
  if (chips.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-2 px-6 pt-3">
      <span className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-500"><Ic.funnel /></span>
      {chips.map((c) => (
        <FilterChip key={c.dimension} chip={c} onFilterChange={onFilterChange} onToggleSplit={onToggleSplit} />
      ))}
      <button onClick={dirty ? onApply : undefined} disabled={!dirty}
        className={'ml-1 rounded-md px-4 py-1.5 text-[13px] font-medium transition-colors ' +
          (dirty ? 'bg-sky-600 text-white hover:bg-sky-700' : 'cursor-default bg-slate-100 text-slate-400')}>
        Apply
      </button>
    </div>
  )
}

/* ---------- widget grid (static in read mode) ---------- */

export interface WidgetDataState {
  loading: boolean
  error: string | null
  chart?: ChartWidgetData
  number?: NumberWidgetData
}

export function WidgetGrid({ widgets, dataById, onOpenChart, movingAvgWindow }: {
  widgets: DashWidget[]
  dataById: Record<number, WidgetDataState | undefined>
  onOpenChart: (chartId: number) => void
  movingAvgWindow: number | null
}) {
  const layout = useMemo(
    () => widgets.map((w) => ({ i: String(w.id), ...w.layout, static: true })),
    [widgets],
  )
  if (widgets.length === 0) {
    return <div className="px-6 py-16 text-center text-[13px] text-slate-400">No widgets on this tab yet — add some in Edit Mode.</div>
  }
  return (
    <div className="px-6 pb-8 pt-2">
      <GridLayout layout={layout} cols={12} rowHeight={88} margin={[14, 14]} containerPadding={[0, 0]}
        isDraggable={false} isResizable={false} compactType="vertical" useCSSTransforms={true}>
        {widgets.map((w) => {
          const st = dataById[w.id] || { loading: true, error: null }
          return (
            <div key={String(w.id)}>
              {w.type === 'chart' ? (
                <ChartWidgetCard
                  title={w.name}
                  data={st.chart || null}
                  config={w.config}
                  loading={st.loading}
                  error={st.error}
                  onExpand={() => onOpenChart(w.source_chart_id)}
                  movingAvgWindow={movingAvgWindow}
                />
              ) : (
                <NumberWidget
                  title={w.name}
                  data={st.number || null}
                  loading={st.loading}
                  error={st.error}
                  onExpand={() => onOpenChart(w.source_chart_id)}
                />
              )}
            </div>
          )
        })}
      </GridLayout>
    </div>
  )
}
