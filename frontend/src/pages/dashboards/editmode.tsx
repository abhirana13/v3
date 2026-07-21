import { useEffect, useRef, useState } from 'react'
import RGL, { WidthProvider, type Layout } from 'react-grid-layout'
import type { DashWidget, WidgetLayout } from '../../api/types'
import { Checkbox, FilterChip, type FilterChipState, type WidgetDataState } from './DashboardView'
import { ChartWidgetCard, NumberWidget } from './widgets'

/* Edit-mode components, ported from the Claude Design handoff (editmode.jsx):
   EditHeader (Enabled toggle, Discard / Save Dashboard), EditTabBar (rename /
   move / delete / add tabs), EditToolbar (Add Widget + Filter manager + chips),
   EditableWidgetGrid (drag handle + resize + gear + … menu), and the QuickAdd
   dialog (minimal widget creation until the Phase-7 settings modal). Dumb —
   data via props, actions via callbacks. */

const GridLayout = WidthProvider(RGL)

const Ic = {
  caret: (p: any) => (<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M4 6l4 4 4-4" /></svg>),
  gear: (p: any) => (<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>),
  dots: (p: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" {...p}><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></svg>),
  grip: (p: any) => (<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" {...p}><circle cx="9" cy="5" r="1.5" /><circle cx="15" cy="5" r="1.5" /><circle cx="9" cy="12" r="1.5" /><circle cx="15" cy="12" r="1.5" /><circle cx="9" cy="19" r="1.5" /><circle cx="15" cy="19" r="1.5" /></svg>),
  plus: (p: any) => (<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" {...p}><path d="M12 5v14M5 12h14" /></svg>),
  chart: (p: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M3 17l5-6 4 3 6-8" /><path d="M3 21h18" /></svg>),
  hash: (p: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" {...p}><path d="M9 4L7 20M17 4l-2 16M4 9h17M3 15h17" /></svg>),
  trash: (p: any) => (<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5" /></svg>),
  copy: (p: any) => (<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>),
  pencil: (p: any) => (<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M17 3l4 4L8 20l-5 1 1-5z" /></svg>),
  arrowL: (p: any) => (<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M19 12H5M11 6l-6 6 6 6" /></svg>),
  arrowR: (p: any) => (<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M5 12h14M13 6l6 6-6 6" /></svg>),
}

/* ---------- generic dropdown menu ---------- */

type MenuItem = { label: string; icon?: React.ReactNode; danger?: boolean; onClick: () => void } | '---'

export function Menu({ button, items, align = 'right' }: {
  button: (toggle: () => void, open: boolean) => React.ReactNode
  items: MenuItem[]
  align?: 'left' | 'right'
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  return (
    <div ref={ref} className="relative">
      {button(() => setOpen(!open), open)}
      {open && (
        <div className={'absolute top-full z-40 mt-1 w-44 rounded-md border border-slate-200 bg-white py-1 shadow-lg ' + (align === 'right' ? 'right-0' : 'left-0')}>
          {items.map((it, i) => it === '---'
            ? <div key={i} className="my-1 border-t border-slate-100" />
            : (
              <button key={i} onClick={() => { setOpen(false); it.onClick() }}
                className={'flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-slate-50 ' + (it.danger ? 'text-rose-600' : 'text-slate-600')}>
                {it.icon}{it.label}
              </button>
            ))}
        </div>
      )}
    </div>
  )
}

/* ---------- edit header ---------- */

export function EditHeader({ title, number, enabled, saveDirty, onToggleEnabled, onSave, onDiscard }: {
  title: string; number: number | null; enabled: boolean; saveDirty: boolean
  onToggleEnabled: (v: boolean) => void; onSave: () => void; onDiscard: () => void
}) {
  return (
    <div className="flex items-center gap-3 px-6 pt-4 pb-1">
      <h1 className="truncate text-[22px] font-bold tracking-tight text-slate-900">{title}</h1>
      {number != null && <span className="text-sm font-medium text-slate-400">#{number}</span>}
      <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-500">Edit Mode</span>
      <div className="ml-auto flex items-center gap-2">
        <button onClick={() => onToggleEnabled(!enabled)}
          className={'flex items-center gap-1.5 rounded-full py-1 pl-2.5 pr-1 text-[11px] font-semibold transition-colors ' +
            (enabled ? 'bg-sky-600 text-white' : 'flex-row-reverse bg-slate-200 pl-1 pr-2.5 text-slate-500')}>
          {enabled ? 'Enabled' : 'Disabled'}<span className="h-3.5 w-3.5 rounded-full bg-white shadow" />
        </button>
        <button onClick={onDiscard} className="rounded-md border border-slate-300 bg-white px-3.5 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">Discard</button>
        <button onClick={saveDirty ? onSave : undefined} disabled={!saveDirty}
          className={'rounded-md px-3.5 py-1.5 text-sm font-medium transition-colors ' +
            (saveDirty ? 'bg-sky-600 text-white hover:bg-sky-700' : 'cursor-default bg-slate-100 text-slate-400')}>
          Save Dashboard
        </button>
      </div>
    </div>
  )
}

/* ---------- edit tab bar ---------- */

export function EditTabBar({ tabs, activeId, onTabChange, onAddTab, onRenameTab, onMoveTab, onDeleteTab }: {
  tabs: { id: number; name: string }[]
  activeId: number | null
  onTabChange: (id: number) => void
  onAddTab: () => void
  onRenameTab: (id: number) => void
  onMoveTab: (id: number, dir: -1 | 1) => void
  onDeleteTab: (id: number) => void
}) {
  return (
    <div className="flex items-end border-b border-slate-200 px-6">
      {tabs.map((t) => (
        <div key={t.id} className="mr-5 flex items-end gap-0.5">
          <button onClick={() => onTabChange(t.id)}
            className={'-mb-px border-b-2 px-0.5 pb-2.5 pt-1 text-[13px] font-medium transition-colors ' +
              (t.id === activeId ? 'border-sky-600 text-sky-600' : 'border-transparent text-slate-500 hover:text-slate-700')}>
            {t.name}
          </button>
          {t.id === activeId && (
            <Menu align="left"
              button={(toggle) => (
                <button onClick={toggle} className="mb-1.5 flex h-5 w-5 items-center justify-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-600"><Ic.dots /></button>
              )}
              items={[
                { label: 'Rename tab', icon: <Ic.pencil />, onClick: () => onRenameTab(t.id) },
                { label: 'Move left', icon: <Ic.arrowL />, onClick: () => onMoveTab(t.id, -1) },
                { label: 'Move right', icon: <Ic.arrowR />, onClick: () => onMoveTab(t.id, 1) },
                '---',
                { label: 'Delete tab', icon: <Ic.trash />, danger: true, onClick: () => onDeleteTab(t.id) },
              ]} />
          )}
        </div>
      ))}
      <button onClick={onAddTab} title="Add tab" className="mb-1.5 flex h-5 w-5 items-center justify-center rounded text-slate-400 hover:bg-slate-100 hover:text-sky-600"><Ic.plus /></button>
    </div>
  )
}

/* ---------- add-widget menu + filter manager ---------- */

export function AddWidgetMenu({ onAddWidget }: { onAddWidget: (type: 'chart' | 'number') => void }) {
  return (
    <Menu align="left"
      button={(toggle, open) => (
        <button onClick={toggle} className={'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] font-medium text-white transition-colors ' + (open ? 'bg-sky-700' : 'bg-sky-600 hover:bg-sky-700')}>
          Add Widget<Ic.caret className="text-sky-200" />
        </button>
      )}
      items={[
        { label: 'Chart', icon: <Ic.chart />, onClick: () => onAddWidget('chart') },
        { label: 'Number', icon: <Ic.hash />, onClick: () => onAddWidget('number') },
      ]} />
  )
}

function FilterManager({ candidates, active, onToggle }: {
  candidates: string[] | null // null = loading
  active: string[]
  onToggle: (dim: string, on: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-600 hover:border-slate-300">
        <Ic.plus className="text-slate-400" />Filter
      </button>
      {open && (
        <div className="absolute left-0 top-full z-40 mt-1 w-56 rounded-md border border-slate-200 bg-white py-1 shadow-lg">
          <div className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Global filter dimensions</div>
          {candidates === null && <div className="px-3 py-2 text-[12px] text-slate-400">Loading dimensions…</div>}
          {candidates !== null && candidates.length === 0 && (
            <div className="px-3 py-2 text-[12px] text-slate-400">Add widgets first — filters come from their source charts.</div>
          )}
          {(candidates || []).map((dim) => {
            const on = active.includes(dim)
            return (
              <button key={dim} onClick={() => onToggle(dim, !on)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] text-slate-600 hover:bg-slate-50">
                <Checkbox checked={on} onChange={() => onToggle(dim, !on)} />
                <span className="truncate">{dim}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function EditToolbar({ chips, filterCandidates, onAddWidget, onToggleFilterDim, onFilterChange, onToggleSplit, onReorderFilter }: {
  chips: FilterChipState[]
  filterCandidates: string[] | null
  onAddWidget: (type: 'chart' | 'number') => void
  onToggleFilterDim: (dim: string, on: boolean) => void // add/remove a chip
  onFilterChange: (dim: string, values: FilterChipState['selected']) => void
  onToggleSplit: (dim: string) => void
  onReorderFilter: (from: number, to: number) => void
}) {
  // drag-to-reorder: live-swap as the dragged chip enters another's slot. dragIdx
  // lives in a ref (logic) + dragDim in state (the opacity cue).
  const dragIdx = useRef<number | null>(null)
  const [dragDim, setDragDim] = useState<string | null>(null)
  return (
    <div className="flex flex-wrap items-center gap-2 px-6 pt-3">
      <AddWidgetMenu onAddWidget={onAddWidget} />
      <FilterManager
        candidates={filterCandidates}
        active={chips.map((c) => c.dimension)}
        onToggle={onToggleFilterDim}
      />
      {chips.length > 0 && <span className="mx-1 h-5 w-px bg-slate-200" />}
      {chips.map((c, i) => (
        <FilterChip key={c.dimension} chip={c} onFilterChange={onFilterChange} onToggleSplit={onToggleSplit}
          onRemove={(dim) => onToggleFilterDim(dim, false)}
          drag={{
            onDragStart: (e) => { dragIdx.current = i; setDragDim(c.dimension); try { e.dataTransfer.effectAllowed = 'move' } catch { /* jsdom */ } },
            onDragEnter: () => { const from = dragIdx.current; if (from !== null && from !== i) { onReorderFilter(from, i); dragIdx.current = i } },
            onDragEnd: () => { dragIdx.current = null; setDragDim(null) },
            dragging: dragDim === c.dimension,
          }} />
      ))}
    </div>
  )
}

/* ---------- editable grid ---------- */

export function EditableWidgetGrid({ widgets, layoutById, dataById, onLayoutChange, onWidgetSettings, onWidgetDuplicate, onWidgetDelete, movingAvgWindow }: {
  widgets: DashWidget[]
  layoutById: Record<number, WidgetLayout>
  dataById: Record<number, WidgetDataState | undefined>
  onLayoutChange: (items: ({ widget_id: number } & WidgetLayout)[]) => void
  onWidgetSettings: (id: number) => void
  onWidgetDuplicate: (id: number) => void
  onWidgetDelete: (id: number) => void
  movingAvgWindow: number | null
}) {
  // Freeze the layout prop for the lifetime of this mount: RGL manages positions
  // internally while the user drags/resizes and reports changes via
  // onLayoutChange (which we only RECORD, for Save). Feeding the draft straight
  // back into the `layout` prop re-enters RGL's flushSync mid-interaction and
  // livelocks the renderer. The parent remounts this grid (key) when the tab or
  // the widget set changes, at which point the draft is baked in here.
  const [initialLayout] = useState(
    () => widgets.map((w) => ({ i: String(w.id), ...(layoutById[w.id] || w.layout) })),
  )
  const layout = initialLayout

  const chrome = (w: DashWidget) => ({
    leading: (
      <span className="drag-handle -ml-2 mt-0.5 flex h-6 w-5 shrink-0 cursor-grab items-center justify-center rounded text-slate-300 hover:bg-slate-100 hover:text-slate-500 active:cursor-grabbing">
        <Ic.grip />
      </span>
    ),
    trailing: (
      <>
        <button onClick={() => onWidgetSettings(w.id)} title="Widget settings" className="flex h-6 w-6 items-center justify-center rounded hover:bg-slate-100 hover:text-slate-600"><Ic.gear /></button>
        <Menu
          button={(toggle) => (
            <button onClick={toggle} className="flex h-6 w-6 items-center justify-center rounded hover:bg-slate-100 hover:text-slate-600"><Ic.dots /></button>
          )}
          items={[
            { label: 'Duplicate', icon: <Ic.copy />, onClick: () => onWidgetDuplicate(w.id) },
            '---',
            { label: 'Delete', icon: <Ic.trash />, danger: true, onClick: () => onWidgetDelete(w.id) },
          ]} />
      </>
    ),
  })

  if (widgets.length === 0) {
    return <div className="px-6 py-16 text-center text-[13px] text-slate-400">Empty tab — use “Add Widget” to place the first one.</div>
  }
  return (
    <div className="px-6 pb-8 pt-2">
      <GridLayout layout={layout} cols={12} rowHeight={88} margin={[14, 14]} containerPadding={[0, 0]}
        isDraggable={true} isResizable={true} draggableHandle=".drag-handle"
        resizeHandles={['se']} compactType="vertical" useCSSTransforms={true}
        onLayoutChange={(l: Layout[]) =>
          onLayoutChange(l.map((it) => ({ widget_id: Number(it.i), x: it.x, y: it.y, w: it.w, h: it.h })))
        }>
        {widgets.map((w) => {
          const st = dataById[w.id] || { loading: true, error: null }
          const { leading, trailing } = chrome(w)
          return (
            <div key={String(w.id)}>
              {w.type === 'chart' ? (
                <ChartWidgetCard title={w.name} data={st.chart || null} config={w.config}
                  loading={st.loading} error={st.error} leading={leading} trailing={trailing} movingAvgWindow={movingAvgWindow} />
              ) : (
                <NumberWidget title={w.name} data={st.number || null}
                  loading={st.loading} error={st.error} leading={leading} trailing={trailing} />
              )}
            </div>
          )
        })}
      </GridLayout>
    </div>
  )
}

/* ---------- quick-add dialog (until the Phase-7 settings modal) ---------- */

export function QuickAddWidget({ type, charts, metricOptions, onPickChart, onCancel, onSubmit }: {
  type: 'chart' | 'number'
  charts: { id: number; name: string; number: number | null }[]
  metricOptions: string[] | null // null = none loaded yet / loading
  onPickChart: (chartId: number) => void
  onCancel: () => void
  onSubmit: (v: { source_chart_id: number; name: string; metric: string }) => void
}) {
  const [chartId, setChartId] = useState<number | ''>('')
  const [metric, setMetric] = useState('')
  const [name, setName] = useState('')

  const chart = charts.find((c) => c.id === chartId)
  const canSubmit = chartId !== '' && !!metric
  const effectiveName = name.trim() || (chart && metric ? `${metric} — ${chart.name}` : '')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel() }}>
      <div className="w-[440px] rounded-lg bg-white p-5 shadow-xl">
        <h3 className="mb-4 text-[15px] font-bold text-slate-800">Add {type === 'chart' ? 'Chart' : 'Number'} widget</h3>

        <label className="mb-1 block text-[12px] font-semibold text-slate-500">Source chart</label>
        <select
          value={chartId}
          onChange={(e) => {
            const id = Number(e.target.value)
            setChartId(id)
            setMetric('')
            onPickChart(id)
          }}
          className="mb-3 w-full rounded-md border border-slate-200 bg-white px-2.5 py-2 text-[13px] text-slate-700 outline-none focus:border-sky-400">
          <option value="" disabled>Select a chart…</option>
          {charts.map((c) => (
            <option key={c.id} value={c.id}>{c.number != null ? `#${c.number} · ` : ''}{c.name}</option>
          ))}
        </select>

        <label className="mb-1 block text-[12px] font-semibold text-slate-500">Metric</label>
        <select
          value={metric}
          onChange={(e) => setMetric(e.target.value)}
          disabled={chartId === '' || metricOptions === null}
          className="mb-3 w-full rounded-md border border-slate-200 bg-white px-2.5 py-2 text-[13px] text-slate-700 outline-none focus:border-sky-400 disabled:bg-slate-50 disabled:text-slate-400">
          <option value="" disabled>{chartId === '' ? 'Pick a chart first' : metricOptions === null ? 'Loading metrics…' : 'Select a metric…'}</option>
          {(metricOptions || []).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>

        <label className="mb-1 block text-[12px] font-semibold text-slate-500">Widget name</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={effectiveName || 'Auto from metric + chart'}
          className="mb-4 w-full rounded-md border border-slate-200 px-2.5 py-2 text-[13px] text-slate-700 outline-none focus:border-sky-400"
        />

        <p className="mb-4 text-[11.5px] text-slate-400">Fine-tune (filters, group-by, axes, target…) via the widget’s ⚙ once added.</p>

        <div className="flex justify-end gap-2">
          <button onClick={onCancel} className="rounded-md border border-slate-300 bg-white px-3.5 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">Cancel</button>
          <button
            disabled={!canSubmit}
            onClick={() => canSubmit && onSubmit({ source_chart_id: chartId as number, name: effectiveName, metric })}
            className={'rounded-md px-3.5 py-1.5 text-sm font-medium ' + (canSubmit ? 'bg-sky-600 text-white hover:bg-sky-700' : 'cursor-default bg-slate-100 text-slate-400')}>
            Add widget
          </button>
        </div>
      </div>
    </div>
  )
}
