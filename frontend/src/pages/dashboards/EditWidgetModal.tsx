import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { FilterValue } from '../../api/types'

// Structural widget shape the modal edits — matches both a saved DashWidget and a
// staged draft widget (edit mode never persists directly; it emits via onApply).
type WidgetLike = { id: number; type: 'chart' | 'number'; source_chart_id: number; name: string; config: Record<string, unknown> }
export interface WidgetPatch { source_chart_id: number; name: string; config: Record<string, unknown> }

/* Edit Widget modal, ported from the Claude Design handoff (editwidget.jsx):
   left section nav (Basic / Metrics / Dimensions / Other Settings) + a scrolling
   right pane, with the field set switching on widget type. Unlike the design mock
   it's wired to the real backend — it loads the selected source chart's metrics /
   dimensions / filter values through the api-client, and maps its local draft to
   the backend ChartWidgetConfig / NumberWidgetConfig on Apply. Selections are
   pruned when the source chart changes so a save can't reference a metric/dim the
   new chart lacks (the backend would 400). */

const Ic = {
  caret: (p: any) => (<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M4 6l4 4 4-4" /></svg>),
  close: (p: any) => (<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" {...p}><path d="M18 6 6 18M6 6l12 12" /></svg>),
  search: (p: any) => (<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>),
  pencilBox: (p: any) => (<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6" /><path d="M17.5 3.5a2.1 2.1 0 0 1 3 3L11 16l-4 1 1-4z" /></svg>),
  line: (p: any) => (<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M4 16l4.5-5 3.5 2.5L19 7" /><circle cx="8.5" cy="11" r="1" /><circle cx="12" cy="13.5" r="1" /></svg>),
  bar: (p: any) => (<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" {...p}><path d="M5 20V12M10 20V6M15 20v-9M20 20V9" /></svg>),
}

function useOutside(ref: React.RefObject<HTMLElement>, cb: () => void) {
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) cb() }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
}

/* ---------- selects ---------- */

function Select({ value, options, placeholder, onChange, className = '', disabled }: {
  value: string; options: string[]; placeholder: string
  onChange: (v: string) => void; className?: string; disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useOutside(ref, () => setOpen(false))
  return (
    <div ref={ref} className={'relative ' + className}>
      <button type="button" onClick={() => !disabled && setOpen(!open)}
        className={'flex w-full items-center justify-between gap-2 rounded-md border px-3 py-2 text-left text-[13px] transition-colors ' +
          (disabled ? 'cursor-default border-slate-200 bg-slate-50 text-slate-400'
            : open ? 'border-sky-400 bg-white text-slate-700 ring-1 ring-sky-100' : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300')}>
        <span className={'truncate ' + (!value ? 'text-slate-300' : '')}>{value || placeholder}</span>
        <Ic.caret className="shrink-0 text-slate-300" />
      </button>
      {open && !disabled && (
        <div className="absolute left-0 top-full z-50 mt-1 max-h-52 w-full min-w-[140px] overflow-y-auto rounded-md border border-slate-200 bg-white py-1 shadow-lg">
          {options.length === 0 && <div className="px-3 py-2 text-[13px] text-slate-400">No options</div>}
          {options.map((o) => (
            <button type="button" key={o} onClick={() => { setOpen(false); onChange(o) }}
              className={'block w-full truncate px-3 py-1.5 text-left text-[13px] hover:bg-slate-50 ' + (o === value ? 'font-semibold text-sky-600' : 'text-slate-600')}>
              {o}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function SearchSelect({ value, options, placeholder, onChange }: {
  value: string; options: { key: string; label: string }[]; placeholder: string; onChange: (key: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  useOutside(ref, () => setOpen(false))
  const label = options.find((o) => o.key === value)?.label
  const filtered = options.filter((o) => o.label.toLowerCase().includes(q.toLowerCase()))
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => { setOpen(!open); setQ('') }}
        className={'flex w-full items-center justify-between gap-2 rounded-md border px-3 py-2 text-left text-[13px] text-slate-700 transition-colors ' +
          (open ? 'border-sky-400 bg-white ring-1 ring-sky-100' : 'border-slate-200 bg-white hover:border-slate-300')}>
        <span className="truncate">{label || <span className="text-slate-300">{placeholder}</span>}</span>
        <Ic.caret className="shrink-0 text-slate-300" />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-full rounded-md border border-slate-200 bg-white shadow-lg">
          <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2 text-slate-400">
            <Ic.search />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…"
              className="w-full bg-transparent text-[13px] text-slate-700 outline-none placeholder:text-slate-300" />
          </div>
          <div className="max-h-48 overflow-y-auto py-1">
            {filtered.length === 0 && <div className="px-3 py-2 text-[13px] text-slate-400">No matches</div>}
            {filtered.map((o) => (
              <button type="button" key={o.key} onClick={() => { setOpen(false); onChange(o.key) }}
                className={'block w-full truncate px-3 py-1.5 text-left text-[13px] hover:bg-slate-50 ' + (o.key === value ? 'font-semibold text-sky-600' : 'text-slate-600')}>
                {o.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function MultiSelect({ values, options, placeholder, onChange, maxSelect, className = '' }: {
  values: FilterValue[]; options: FilterValue[]; placeholder: string
  onChange: (v: FilterValue[]) => void; maxSelect?: number; className?: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useOutside(ref, () => setOpen(false))
  const has = (v: FilterValue) => values.some((x) => x === v)
  const toggle = (v: FilterValue) => {
    if (!has(v) && maxSelect && values.length >= maxSelect) return
    onChange(has(v) ? values.filter((x) => x !== v) : [...values, v])
  }
  const shown = values.slice(0, 1)
  const extra = values.length - shown.length
  const trunc = (s: string) => (s.length > 14 ? s.slice(0, 13) + '…' : s)
  return (
    <div ref={ref} className={'relative ' + className}>
      <button type="button" onClick={() => setOpen(!open)}
        className={'flex min-h-[36px] w-full items-center gap-1.5 rounded-md border px-2 py-1.5 text-left text-[13px] transition-colors ' +
          (open ? 'border-sky-400 bg-white ring-1 ring-sky-100' : 'border-slate-200 bg-white hover:border-slate-300')}>
        {values.length === 0 && <span className="px-1 text-slate-300">{placeholder}</span>}
        {shown.map((v) => (
          <span key={String(v)} className="inline-flex max-w-[150px] items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[12px] text-slate-600">
            <span className="truncate">{trunc(String(v))}</span>
            <span onClick={(e) => { e.stopPropagation(); onChange(values.filter((x) => x !== v)) }} className="text-slate-400 hover:text-slate-600"><Ic.close width="10" height="10" /></span>
          </span>
        ))}
        {extra > 0 && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[12px] text-slate-500">+{extra} …</span>}
        <Ic.caret className="ml-auto shrink-0 text-slate-300" />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 max-h-52 w-full overflow-y-auto rounded-md border border-slate-200 bg-white py-1 shadow-lg">
          {options.length === 0 && <div className="px-3 py-2 text-[13px] text-slate-400">No values in cache</div>}
          {options.map((o) => (
            <button type="button" key={String(o)} onClick={() => toggle(o)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] text-slate-600 hover:bg-slate-50">
              <span className={'inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-[4px] border ' + (has(o) ? 'border-sky-500 bg-sky-500 text-white' : 'border-slate-300 bg-white')}>
                {has(o) && <svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M3.5 8.5l3 3 6-7" /></svg>}
              </span>
              <span className="truncate">{String(o)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/* ---------- small pieces ---------- */

function FieldLabel({ children, required }: { children: React.ReactNode; required?: boolean }) {
  return <div className="mb-1.5 flex items-center gap-1 text-[13px] font-medium text-slate-600">{required && <span className="text-rose-500">*</span>}{children}</div>
}
function SectionHeading({ children }: { children: React.ReactNode }) {
  return <div className="mb-4 rounded bg-sky-50 px-3 py-2 text-[14px] font-semibold text-sky-700">{children}</div>
}
function Check({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-[13px] text-slate-600" onClick={() => onChange(!checked)}>
      <span className={'inline-flex h-4 w-4 items-center justify-center rounded-[4px] border transition-colors ' + (checked ? 'border-sky-500 bg-sky-500 text-white' : 'border-slate-300 bg-white hover:border-slate-400')}>
        {checked && <svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M3.5 8.5l3 3 6-7" /></svg>}
      </span>
      {label}
    </label>
  )
}

const AXIS_LABEL: Record<'primary' | 'secondary', string> = { primary: 'Primary Axis', secondary: 'Secondary Axis' }
const AXIS_FROM_LABEL: Record<string, 'primary' | 'secondary'> = { 'Primary Axis': 'primary', 'Secondary Axis': 'secondary' }
const UNIT_OPTIONS = ['(none)', '$', '%', 'K', 'min']

/* ---------- draft model ---------- */

interface MetricSel { name: string; y_axis: 'primary' | 'secondary' }
interface FilterRow { dimension: string; values: FilterValue[] }
interface Range { min: string; max: string }

interface Draft {
  sourceChartId: number
  name: string
  viz: 'line' | 'bar'
  metrics: MetricSel[]
  groupBy: string[]
  offsetDays: string
  yPrimary: Range
  ySecondary: Range
  metric: string // number widget
  decimals: string
  unit: string // '' or '(none)' => no unit
  comparePrevDay: boolean
  compareLastWeek: boolean
  filterBy: FilterRow[]
  target: string
}

const numOrEmpty = (v: unknown) => (v === null || v === undefined ? '' : String(v))

function initialDraft(widget: WidgetLike): Draft {
  const c: any = widget.config || {}
  const filterBy: FilterRow[] = Object.entries(c.filters || {}).map(([dimension, values]) => ({ dimension, values: values as FilterValue[] }))
  return {
    sourceChartId: widget.source_chart_id,
    name: widget.name,
    viz: c.viz === 'bar' ? 'bar' : 'line',
    metrics: (c.metrics && c.metrics.length ? c.metrics : [{ name: '', y_axis: 'primary' }]).map((m: any) => ({ name: m.name || '', y_axis: m.y_axis === 'secondary' ? 'secondary' : 'primary' })),
    groupBy: c.group_by || [],
    offsetDays: numOrEmpty(c.offset_days),
    yPrimary: { min: numOrEmpty(c.y_axis?.primary?.min), max: numOrEmpty(c.y_axis?.primary?.max) },
    ySecondary: { min: numOrEmpty(c.y_axis?.secondary?.min), max: numOrEmpty(c.y_axis?.secondary?.max) },
    metric: c.metric || '',
    decimals: numOrEmpty(c.decimals) || '0',
    unit: c.unit || '(none)',
    comparePrevDay: c.compares ? c.compares.includes('previous_day') : true,
    compareLastWeek: c.compares ? c.compares.includes('last_week') : true,
    filterBy,
    target: numOrEmpty(c.target),
  }
}

const rangeObj = (r: Range) => {
  const o: { min?: number; max?: number } = {}
  if (r.min !== '') o.min = Number(r.min)
  if (r.max !== '') o.max = Number(r.max)
  return o
}

function buildConfig(type: 'chart' | 'number', d: Draft, existing: any): Record<string, unknown> {
  const filters: Record<string, FilterValue[]> = {}
  for (const f of d.filterBy) if (f.dimension && f.values.length) filters[f.dimension] = f.values
  const target = d.target.trim() === '' ? null : Number(d.target)
  if (type === 'chart') {
    const yp = rangeObj(d.yPrimary)
    const ys = rangeObj(d.ySecondary)
    const y_axis: Record<string, unknown> = {}
    if (Object.keys(yp).length) y_axis.primary = yp
    if (Object.keys(ys).length) y_axis.secondary = ys
    return {
      viz: d.viz,
      metrics: d.metrics.filter((m) => m.name).map((m) => ({ name: m.name, y_axis: m.y_axis })),
      filters,
      group_by: d.groupBy,
      offset_days: d.offsetDays.trim() === '' ? null : Number(d.offsetDays),
      offset_mode: 'only_on_end_date',
      x_axis: 'time',
      y_axis,
      target,
    }
  }
  const compares: string[] = []
  if (d.comparePrevDay) compares.push('previous_day')
  if (d.compareLastWeek) compares.push('last_week')
  return {
    metric: d.metric,
    filters,
    decimals: d.decimals.trim() === '' ? 0 : Number(d.decimals),
    unit: d.unit === '(none)' || d.unit === '' ? null : d.unit,
    compares,
    // offset isn't exposed for number tiles in this modal — preserve what's set
    offset_days: existing?.offset_days ?? null,
    target,
  }
}

/* ---------- section nav ---------- */

const SECTIONS_CHART = [
  { id: 'basic', label: 'Basic' },
  { id: 'metrics', label: 'Metrics' },
  { id: 'dimensions', label: 'Dimensions' },
  { id: 'other', label: 'Other Settings' },
]
const SECTIONS_NUMBER = [
  { id: 'basic', label: 'Basic' },
  { id: 'metrics', label: 'Metric' },
  { id: 'dimensions', label: 'Filter By' },
  { id: 'other', label: 'Other Settings' },
]

function YAxisRange({ label, range, onChange }: { label: string; range: Range; onChange: (r: Range) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-b border-slate-100 last:border-0">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center justify-between px-3 py-2.5 text-[13px] text-slate-600 hover:bg-slate-50">
        {label}<Ic.caret className={'text-slate-400 transition-transform ' + (open ? 'rotate-180' : '')} />
      </button>
      {open && (
        <div className="flex items-center gap-2.5 px-3 pb-3">
          <input type="number" value={range.min} placeholder="Min" onChange={(e) => onChange({ ...range, min: e.target.value })}
            className="w-full rounded-md border border-slate-200 px-3 py-2 text-[13px] text-slate-700 outline-none placeholder:text-slate-300 focus:border-sky-400 focus:ring-1 focus:ring-sky-100" />
          <span className="text-slate-300">—</span>
          <input type="number" value={range.max} placeholder="Max" onChange={(e) => onChange({ ...range, max: e.target.value })}
            className="w-full rounded-md border border-slate-200 px-3 py-2 text-[13px] text-slate-700 outline-none placeholder:text-slate-300 focus:border-sky-400 focus:ring-1 focus:ring-sky-100" />
        </div>
      )}
    </div>
  )
}

/* ---------- the modal ---------- */

export function EditWidgetModal({ widget, charts, onApply, onCancel }: {
  widget: WidgetLike
  charts: { id: number; name: string; number: number | null }[]
  onApply: (patch: WidgetPatch) => void
  onCancel: () => void
}) {
  const type = widget.type
  const isChart = type === 'chart'
  const sections = isChart ? SECTIONS_CHART : SECTIONS_NUMBER

  const [draft, setDraft] = useState<Draft>(() => initialDraft(widget))
  const [dirty, setDirty] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  // options for the currently-selected source chart
  const [metricOptions, setMetricOptions] = useState<string[]>([])
  const [dimOptions, setDimOptions] = useState<string[]>([])
  const [valueOptions, setValueOptions] = useState<Record<string, FilterValue[]>>({})
  const [optsLoading, setOptsLoading] = useState(true)

  const [active, setActive] = useState('basic')
  const paneRef = useRef<HTMLDivElement>(null)
  const secRefs = useRef<Record<string, HTMLDivElement | null>>({})

  const set = (patch: Partial<Draft>) => { setDraft((d) => ({ ...d, ...patch })); setDirty(true) }

  // load metrics / dims / values for the selected source chart; prune now-invalid picks
  useEffect(() => {
    let alive = true
    setOptsLoading(true)
    Promise.all([api.getDimsMetrics(draft.sourceChartId), api.getDimValues(draft.sourceChartId)])
      .then(([dm, dv]) => {
        if (!alive) return
        const metrics = dm.metrics.map((m) => m.name)
        const dims = dm.dimensions.map((d) => d.name)
        const values = dv.dimensions as unknown as Record<string, FilterValue[]>
        setMetricOptions(metrics)
        setDimOptions(dims)
        setValueOptions(values)
        setOptsLoading(false)
        // prune selections the new chart can't satisfy (avoids a backend 400 on save)
        setDraft((d) => {
          const mset = new Set(metrics), dset = new Set(dims)
          return {
            ...d,
            metrics: d.metrics.map((m) => (m.name && !mset.has(m.name) ? { ...m, name: '' } : m)),
            groupBy: d.groupBy.filter((g) => dset.has(g)),
            metric: d.metric && !mset.has(d.metric) ? '' : d.metric,
            filterBy: d.filterBy
              .filter((f) => !f.dimension || dset.has(f.dimension))
              .map((f) => ({ ...f, values: f.values.filter((v) => (valueOptionsHas(values, f.dimension, v))) })),
          }
        })
      })
      .catch((e: any) => { if (alive) { setErr(String(e.message || e)); setOptsLoading(false) } })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.sourceChartId])

  const goTo = (id: string) => {
    setActive(id)
    const pane = paneRef.current, el = secRefs.current[id]
    if (pane && el) pane.scrollTop = el.offsetTop - 12
  }
  const onScroll = () => {
    const pane = paneRef.current
    if (!pane) return
    let cur = sections[0].id
    for (const s of sections) {
      const el = secRefs.current[s.id]
      if (el && el.offsetTop - 80 <= pane.scrollTop) cur = s.id
    }
    setActive(cur)
  }

  const filledMetrics = draft.metrics.filter((m) => m.name)
  const metricDup = new Set(filledMetrics.map((m) => m.name)).size !== filledMetrics.length
  const valid = useMemo(() => {
    if (!draft.name.trim() || !draft.sourceChartId) return false
    if (isChart) return filledMetrics.length >= 1 && filledMetrics.length <= 5 && !metricDup && draft.groupBy.length <= 5
    return !!draft.metric
  }, [draft, isChart, filledMetrics.length, metricDup])

  const applyEnabled = dirty && valid && !optsLoading

  const apply = () => {
    if (!applyEnabled) return
    // stage only — the container folds this into the draft; nothing persists until
    // the dashboard's Save. Validation still happens server-side on preview + save.
    onApply({
      source_chart_id: draft.sourceChartId,
      name: draft.name.trim(),
      config: buildConfig(type, draft, widget.config),
    })
  }

  const vizTitle = isChart ? (draft.viz === 'bar' ? 'Bar' : 'Line') : 'Number'
  const chartOpts = charts.map((c) => ({ key: String(c.id), label: `${c.number != null ? '#' + c.number + ' · ' : ''}${c.name}` }))

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/40 pt-[6vh]"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel() }}>
      {/* NOTE: do NOT stopPropagation here — it also stops the native mousedown from
          reaching the document, which is how the Select/MultiSelect dropdowns inside
          this modal detect an outside click to dismiss. The backdrop closes only on a
          direct hit (target===currentTarget above). */}
      <div className="flex max-h-[86vh] w-[720px] flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
        <div className="flex items-center gap-3 border-b border-slate-100 px-5 py-4">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-sky-50 text-sky-500"><Ic.pencilBox /></span>
          <h2 className="text-[17px] font-bold text-slate-900">Edit Widget : {vizTitle}</h2>
          <button type="button" onClick={onCancel} className="ml-auto flex h-7 w-7 items-center justify-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-600"><Ic.close width="16" height="16" /></button>
        </div>

        <div className="flex min-h-0 flex-1">
          <div className="w-40 shrink-0 border-r border-slate-100 py-5">
            {sections.map((s) => (
              <button type="button" key={s.id} onClick={() => goTo(s.id)}
                className={'block w-full px-5 py-2 text-left text-[13px] transition-colors ' + (s.id === active ? 'font-semibold text-sky-600' : 'text-slate-500 hover:text-slate-700')}>
                {s.label}
              </button>
            ))}
          </div>

          <div ref={paneRef} onScroll={onScroll} className="relative min-h-0 flex-1 overflow-y-auto scroll-smooth px-6 py-5">
            {/* Basic */}
            <div ref={(el) => (secRefs.current.basic = el)}>
              <SectionHeading>Basic</SectionHeading>
              <div className="flex flex-col gap-5 pb-6">
                <div>
                  <FieldLabel required>Source Chart</FieldLabel>
                  <SearchSelect value={String(draft.sourceChartId)} options={chartOpts} placeholder="Select a chart"
                    onChange={(key) => set({ sourceChartId: Number(key) })} />
                </div>
                <div>
                  <FieldLabel required>Widget Name</FieldLabel>
                  <input value={draft.name} onChange={(e) => set({ name: e.target.value })} placeholder="Widget name"
                    className="w-full rounded-md border border-slate-200 px-3 py-2 text-[13px] text-slate-700 outline-none placeholder:text-slate-300 focus:border-sky-400 focus:ring-1 focus:ring-sky-100" />
                </div>
                {isChart && (
                  <div>
                    <FieldLabel required>Visualization</FieldLabel>
                    <div className="flex items-center gap-1 rounded-md border border-slate-200 p-1.5">
                      {([['line', <Ic.line key="l" />], ['bar', <Ic.bar key="b" />]] as const).map(([k, ic]) => (
                        <button type="button" key={k} onClick={() => set({ viz: k })} title={k}
                          className={'flex h-8 w-9 items-center justify-center rounded border transition-colors ' +
                            (draft.viz === k ? 'border-sky-300 bg-sky-50 text-sky-600' : 'border-transparent text-slate-400 hover:bg-slate-50 hover:text-slate-600')}>
                          {ic}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Metrics */}
            <div ref={(el) => (secRefs.current.metrics = el)}>
              <SectionHeading>{isChart ? 'Metrics' : 'Metric'}</SectionHeading>
              <div className="flex flex-col gap-3 pb-6">
                {isChart ? (
                  <>
                    {draft.metrics.map((m, i) => (
                      <div key={i} className="flex items-center gap-2.5">
                        <Select className="flex-1" value={m.name} options={metricOptions} placeholder="Metric"
                          onChange={(name) => set({ metrics: draft.metrics.map((x, j) => (j === i ? { ...x, name } : x)) })} />
                        <Select className="w-36" value={AXIS_LABEL[m.y_axis]} options={['Primary Axis', 'Secondary Axis']} placeholder="Axis"
                          onChange={(a) => set({ metrics: draft.metrics.map((x, j) => (j === i ? { ...x, y_axis: AXIS_FROM_LABEL[a] } : x)) })} />
                        {draft.metrics.length > 1 && (
                          <button type="button" onClick={() => set({ metrics: draft.metrics.filter((_, j) => j !== i) })}
                            className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-600"><Ic.close /></button>
                        )}
                      </div>
                    ))}
                    <button type="button" disabled={draft.metrics.length >= 5}
                      onClick={() => draft.metrics.length < 5 && set({ metrics: [...draft.metrics, { name: '', y_axis: 'primary' }] })}
                      className={'w-full rounded-md border py-2 text-[13px] font-medium transition-colors ' +
                        (draft.metrics.length >= 5 ? 'cursor-default border-slate-100 text-slate-300' : 'border-slate-200 text-slate-600 hover:border-sky-300 hover:text-sky-600')}>
                      Add ({draft.metrics.length}/5)
                    </button>
                    {metricDup && <div className="text-[12px] text-rose-500">Each metric can only appear once.</div>}
                  </>
                ) : (
                  <Select value={draft.metric} options={metricOptions} placeholder="Metric" onChange={(metric) => set({ metric })} />
                )}
              </div>
            </div>

            {/* Dimensions / Filter By */}
            <div ref={(el) => (secRefs.current.dimensions = el)}>
              <SectionHeading>{isChart ? 'Dimensions' : 'Filter By'}</SectionHeading>
              <div className="flex flex-col gap-3 pb-6">
                <div className="text-[13px] font-medium text-slate-600">Filter By</div>
                {draft.filterBy.map((f, i) => (
                  <div key={i} className="flex items-center gap-2.5">
                    <Select className="w-36 shrink-0" value={f.dimension} options={dimOptions} placeholder="Dimension"
                      onChange={(dimension) => set({ filterBy: draft.filterBy.map((x, j) => (j === i ? { dimension, values: [] } : x)) })} />
                    <MultiSelect className="flex-1" values={f.values} options={valueOptions[f.dimension] || []} placeholder="Values"
                      onChange={(values) => set({ filterBy: draft.filterBy.map((x, j) => (j === i ? { ...x, values } : x)) })} />
                    <button type="button" onClick={() => set({ filterBy: draft.filterBy.filter((_, j) => j !== i) })}
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-600"><Ic.close /></button>
                  </div>
                ))}
                <button type="button" onClick={() => set({ filterBy: [...draft.filterBy, { dimension: '', values: [] }] })}
                  className="w-full rounded-md border border-slate-200 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-sky-300 hover:text-sky-600">
                  Add
                </button>
                {isChart && (
                  <div className="pt-2">
                    <div className="mb-1.5 text-[13px] font-medium text-slate-600">Group By <span className="font-normal text-slate-400">({draft.groupBy.length}/5)</span></div>
                    <MultiSelect values={draft.groupBy} options={dimOptions} placeholder="Dimension" maxSelect={5}
                      onChange={(v) => set({ groupBy: v as string[] })} />
                  </div>
                )}
              </div>
            </div>

            {/* Other Settings */}
            <div ref={(el) => (secRefs.current.other = el)} className="pb-4">
              <SectionHeading>Other Settings</SectionHeading>
              <div className="flex flex-col gap-5">
                {isChart ? (
                  <>
                    <div>
                      <FieldLabel>Offset <span className="font-normal text-slate-400">(days; blank = dashboard default)</span></FieldLabel>
                      <div className="flex items-center gap-2.5">
                        <input type="number" min={0} value={draft.offsetDays} placeholder="dashboard default" onChange={(e) => set({ offsetDays: e.target.value })}
                          className="flex-1 rounded-md border border-slate-200 px-3 py-2 text-[13px] text-slate-700 outline-none placeholder:text-slate-300 focus:border-sky-400 focus:ring-1 focus:ring-sky-100" />
                        <Select className="w-52" value="Only on end date" options={['Only on end date']} placeholder="Mode" onChange={() => {}} disabled />
                      </div>
                    </div>
                    <div>
                      <FieldLabel>X-Axis</FieldLabel>
                      <Select value="time" options={['time']} placeholder="X-Axis" onChange={() => {}} disabled />
                    </div>
                    <div>
                      <FieldLabel>Y-Axis Range</FieldLabel>
                      <div className="rounded-md border border-slate-200">
                        <YAxisRange label="Primary Y-Axis" range={draft.yPrimary} onChange={(r) => set({ yPrimary: r })} />
                        <YAxisRange label="Secondary Y-Axis" range={draft.ySecondary} onChange={(r) => set({ ySecondary: r })} />
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="flex items-start gap-2.5">
                      <div className="flex-1">
                        <FieldLabel>Decimals</FieldLabel>
                        <input type="number" min={0} max={10} value={draft.decimals} placeholder="0" onChange={(e) => set({ decimals: e.target.value })}
                          className="w-full rounded-md border border-slate-200 px-3 py-2 text-[13px] text-slate-700 outline-none placeholder:text-slate-300 focus:border-sky-400 focus:ring-1 focus:ring-sky-100" />
                      </div>
                      <div className="flex-1">
                        <FieldLabel>Unit</FieldLabel>
                        <Select value={draft.unit} options={UNIT_OPTIONS} placeholder="Unit" onChange={(unit) => set({ unit })} />
                      </div>
                    </div>
                    <div>
                      <FieldLabel>Compares</FieldLabel>
                      <div className="flex items-center gap-6 pt-1">
                        <Check checked={draft.comparePrevDay} label="vs previous day" onChange={(v) => set({ comparePrevDay: v })} />
                        <Check checked={draft.compareLastWeek} label="vs last week" onChange={(v) => set({ compareLastWeek: v })} />
                      </div>
                    </div>
                  </>
                )}
                <div>
                  <FieldLabel>Target <span className="font-normal text-slate-400">(optional)</span></FieldLabel>
                  <input type="number" value={draft.target} placeholder="e.g. 24000" onChange={(e) => set({ target: e.target.value })}
                    className="w-full rounded-md border border-slate-200 px-3 py-2 text-[13px] text-slate-700 outline-none placeholder:text-slate-300 focus:border-sky-400 focus:ring-1 focus:ring-sky-100" />
                </div>
              </div>
            </div>
          </div>
        </div>

        {err && <div className="border-t border-rose-100 bg-rose-50 px-6 py-2 text-[12px] text-rose-600">{err}</div>}
        <div className="flex items-center gap-3 border-t border-slate-100 px-6 py-4">
          <button type="button" onClick={onCancel} className="flex-1 rounded-md border border-slate-200 bg-white py-2 text-[13px] font-medium text-slate-600 hover:bg-slate-50">Cancel</button>
          <button type="button" onClick={apply} disabled={!applyEnabled}
            className={'flex-1 rounded-md py-2 text-[13px] font-medium transition-colors ' + (applyEnabled ? 'bg-sky-600 text-white hover:bg-sky-700' : 'cursor-default bg-slate-100 text-slate-400')}>
            Apply
          </button>
        </div>
      </div>
    </div>
  )
}

function valueOptionsHas(values: Record<string, FilterValue[]>, dim: string, v: FilterValue): boolean {
  const opts = values[dim]
  // keep the value if the dimension has no cached option list yet (don't discard
  // a valid saved selection just because the cache lookup came back empty)
  if (!opts || opts.length === 0) return true
  return opts.some((o) => o === v)
}
