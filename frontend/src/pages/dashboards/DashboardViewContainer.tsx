import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { ChartWidgetData, DashboardFull, DashWidget, FilterValue, GlobalFilters, NumberWidgetData, WidgetLayout, WidgetWriteBody } from '../../api/types'
import type { DateRange } from '../../components/DateRangePicker'
import { ControlsRow, DashboardHeader, GlobalFilterBar, TabBar, WidgetGrid, type FilterChipState, type WidgetDataState } from './DashboardView'
import { EditableWidgetGrid, EditHeader, EditTabBar, EditToolbar, QuickAddWidget } from './editmode'
import { EditWidgetModal, type WidgetPatch } from './EditWidgetModal'

/* Orchestrates the dashboard view in two modes.

   READ mode: loads the server tree, holds global controls (tab / date range /
   granularity / filter drafts + Apply), and fetches each widget's saved data.

   EDIT mode is a STAGED working copy: entering edit snapshots the tree into a
   local `draft` (temp negative ids for new tabs/widgets); every structural edit,
   layout drag, filter change and the Enabled toggle mutates `draft` only —
   NOTHING persists until "Save Dashboard", which diffs the draft against the
   snapshot and applies the minimal set of API calls. "Discard" drops the draft.
   While editing, widgets render via the /widget-preview endpoint (posted config)
   so staged/edited widgets show live cached data before they're saved.

   Presentation lives in DashboardView.tsx / editmode.tsx / EditWidgetModal.tsx. */

const toISO = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

function defaultRange(dash: DashboardFull): DateRange {
  const end = new Date()
  end.setDate(end.getDate() - dash.default_end_offset_days)
  const start = new Date(end)
  start.setDate(start.getDate() - (dash.default_date_range_days - 1))
  return { start: toISO(start), end: toISO(end) }
}

const sameLayout = (a: WidgetLayout, b: WidgetLayout) => a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h

// ---- draft (edit-mode working copy) ----
interface DraftWidget { id: number; type: 'chart' | 'number'; source_chart_id: number; name: string; layout: WidgetLayout; config: Record<string, unknown> }
interface DraftTab { id: number; name: string; widgets: DraftWidget[] }
interface Draft { enabled: boolean; tabs: DraftTab[] }

function cloneDraft(dash: DashboardFull): Draft {
  return {
    enabled: dash.enabled,
    tabs: dash.tabs.map((t) => ({
      id: t.id,
      name: t.name,
      widgets: t.widgets.map((w) => ({
        id: w.id,
        type: w.type,
        source_chart_id: w.source_chart_id,
        name: w.name,
        layout: { ...w.layout },
        config: { ...w.config },
      })),
    })),
  }
}

export function DashboardViewContainer({ dashboardId, onGoHome, onOpenDashboard }: {
  dashboardId: number
  onGoHome: () => void
  onOpenDashboard: (id: number) => void
}) {
  const [dash, setDash] = useState<DashboardFull | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [activeTabId, setActiveTabId] = useState<number | null>(null)
  const [granularity, setGranularity] = useState('day')
  const [dateRange, setDateRange] = useState<DateRange | null>(null)

  // filter bar: chips are the editable draft; appliedFilters is what widgets use.
  // Read mode commits via Apply; edit mode applies live and persists chip selections
  // (as the dashboard's default_values) on Save.
  const [chips, setChips] = useState<FilterChipState[]>([])
  const [appliedFilters, setAppliedFilters] = useState<GlobalFilters>({})
  const [appliedSplit, setAppliedSplit] = useState<string[]>([]) // committed split cuts
  const [dirty, setDirty] = useState(false)

  const [dataById, setDataById] = useState<Record<number, WidgetDataState | undefined>>({})
  const fetchToken = useRef(0)

  // ---- edit mode ----
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Draft | null>(null)
  const originalRef = useRef<DashboardFull | null>(null) // snapshot to diff on Save
  const [saveDirty, setSaveDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const tempIdRef = useRef(-1)
  const nextTempId = () => tempIdRef.current--
  const [quickAdd, setQuickAdd] = useState<'chart' | 'number' | null>(null)
  const [chartOptions, setChartOptions] = useState<{ id: number; name: string; number: number | null }[] | null>(null)
  const [quickAddMetrics, setQuickAddMetrics] = useState<string[] | null>(null)
  const [filterCandidates, setFilterCandidates] = useState<string[] | null>(null)
  const [allDimValues, setAllDimValues] = useState<Record<string, FilterValue[]>>({})
  const [settingsWidgetId, setSettingsWidgetId] = useState<number | null>(null)

  const fail = (e: any) => window.alert(String(e?.message || e))

  const chipsToFilters = (list: FilterChipState[]): GlobalFilters => {
    const out: GlobalFilters = {}
    for (const c of list) if (c.selected.length) out[c.dimension] = c.selected // empty = All (no constraint)
    return out
  }
  const chipsToSplit = (list: FilterChipState[]): string[] => list.filter((c) => c.split).map((c) => c.dimension)

  // load / reload the read tree (+ filter options). keepState preserves the current
  // tab across a refresh; otherwise everything resets to the saved defaults.
  const reload = useCallback(async (keepState: boolean) => {
    const [tree, fv] = await Promise.all([api.getDashboard(dashboardId), api.dashboardFilterValues(dashboardId)])
    setDash(tree)
    setActiveTabId((cur) => (keepState && cur != null && tree.tabs.some((t) => t.id === cur) ? cur : tree.tabs[0]?.id ?? null))
    if (!keepState) setDateRange(defaultRange(tree))
    const built = tree.filters.map((f) => ({
      dimension: f.dimension,
      options: fv.values[f.dimension] || [],
      selected: (f.default_values || []) as FilterValue[],
      split: false, // split is a live view toggle, not persisted
    }))
    setChips(built)
    setAppliedFilters(chipsToFilters(built))
    setAppliedSplit([])
    setDirty(false)
  }, [dashboardId])

  useEffect(() => {
    let alive = true
    setDash(null)
    setError(null)
    setEditing(false)
    setDraft(null)
    setSaveDirty(false)
    reload(false).catch((e: any) => alive && setError(String(e.message || e)))
    return () => { alive = false }
  }, [reload])

  const activeTab = useMemo(() => dash?.tabs.find((t) => t.id === activeTabId) ?? null, [dash, activeTabId])
  const draftActiveTab = useMemo(() => draft?.tabs.find((t) => t.id === activeTabId) ?? null, [draft, activeTabId])
  const draftActiveRef = useRef(draftActiveTab)
  useEffect(() => { draftActiveRef.current = draftActiveTab })

  // ---------- READ-mode data fetch ----------
  useEffect(() => {
    if (editing || !dash || !activeTab || !dateRange) return
    const token = ++fetchToken.current
    setDataById((prev) => {
      const next = { ...prev }
      for (const w of activeTab.widgets) next[w.id] = { ...(next[w.id] || { error: null }), loading: true, error: null }
      return next
    })
    for (const w of activeTab.widgets) {
      api.getWidgetData(dash.id, w.id, { from: dateRange.start, to: dateRange.end, granularity, filters: appliedFilters, split: appliedSplit })
        .then((body) => {
          if (fetchToken.current !== token) return
          setDataById((prev) => ({ ...prev, [w.id]: w.type === 'chart' ? { loading: false, error: null, chart: body as ChartWidgetData } : { loading: false, error: null, number: body as NumberWidgetData } }))
        })
        .catch((e: any) => { if (fetchToken.current === token) setDataById((prev) => ({ ...prev, [w.id]: { loading: false, error: String(e.message || e) } })) })
    }
  }, [editing, dash, activeTab, dateRange, granularity, appliedFilters, appliedSplit])

  // ---------- EDIT-mode data fetch (preview posted configs) ----------
  // key excludes layout so dragging doesn't refetch; config/source/filters/dates do.
  const editDataKey = useMemo(() => {
    if (!editing || !draftActiveTab || !dateRange) return null
    return JSON.stringify({
      ws: draftActiveTab.widgets.map((w) => ({ id: w.id, type: w.type, sc: w.source_chart_id, cfg: w.config })),
      f: appliedFilters, s: appliedSplit, from: dateRange.start, to: dateRange.end, g: granularity,
    })
  }, [editing, draftActiveTab, appliedFilters, appliedSplit, dateRange, granularity])

  useEffect(() => {
    if (!editing || editDataKey == null) return
    const tab = draftActiveRef.current
    if (!tab || !dateRange) return
    const token = ++fetchToken.current
    setDataById((prev) => {
      const next = { ...prev }
      for (const w of tab.widgets) next[w.id] = { ...(next[w.id] || { error: null }), loading: true, error: null }
      return next
    })
    for (const w of tab.widgets) {
      api.previewWidgetData(dashboardId, { type: w.type, source_chart_id: w.source_chart_id, config: w.config, from: dateRange.start, to: dateRange.end, granularity, filters: appliedFilters, split: appliedSplit })
        .then((body) => {
          if (fetchToken.current !== token) return
          setDataById((prev) => ({ ...prev, [w.id]: w.type === 'chart' ? { loading: false, error: null, chart: body as ChartWidgetData } : { loading: false, error: null, number: body as NumberWidgetData } }))
        })
        .catch((e: any) => { if (fetchToken.current === token) setDataById((prev) => ({ ...prev, [w.id]: { loading: false, error: String(e.message || e) } })) })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, editDataKey, dashboardId])

  // ---------- filter chips (read + edit) ----------
  const chipsRef = useRef<FilterChipState[]>([])
  useEffect(() => { chipsRef.current = chips }, [chips])

  const updateChips = useCallback((mutate: (prev: FilterChipState[]) => FilterChipState[]) => {
    const next = mutate(chipsRef.current)
    chipsRef.current = next
    setChips(next)
    if (editing) { setAppliedFilters(chipsToFilters(next)); setAppliedSplit(chipsToSplit(next)); setSaveDirty(true) } // edit: live
    else setDirty(true) // read: gated by Apply
  }, [editing])

  const onFilterChange = useCallback((dim: string, values: FilterValue[]) => {
    updateChips((prev) => prev.map((c) => (c.dimension === dim ? { ...c, selected: values } : c)))
  }, [updateChips])
  const onToggleSplit = useCallback((dim: string) => {
    updateChips((prev) => prev.map((c) => (c.dimension === dim ? { ...c, split: !c.split } : c)))
  }, [updateChips])
  const onApply = useCallback(() => { setAppliedFilters(chipsToFilters(chipsRef.current)); setAppliedSplit(chipsToSplit(chipsRef.current)); setDirty(false) }, [])

  const onReplicate = useCallback(() => {
    api.replicateDashboard(dashboardId).then((copy) => onOpenDashboard(copy.id)).catch(fail)
  }, [dashboardId, onOpenDashboard])
  const onOpenChart = useCallback((chartId: number) => {
    window.open(`${window.location.pathname}?chart=${chartId}`, '_blank')
  }, [])

  const ensureCharts = useCallback(() => {
    if (chartOptions === null) {
      api.listCharts().then((cs) => setChartOptions(cs.map((c) => ({ id: c.id, name: c.name, number: c.chart_number })))).catch(fail)
    }
  }, [chartOptions])

  // ---------- edit mode: enter / discard / save ----------
  const mutateDraft = useCallback((fn: (d: Draft) => Draft) => {
    setDraft((d) => (d ? fn(d) : d))
    setSaveDirty(true)
  }, [])

  const enterEdit = useCallback(() => {
    if (!dash) return
    originalRef.current = dash
    setDraft(cloneDraft(dash))
    setSaveDirty(false)
    setEditing(true)
    ensureCharts()
    // prefetch every candidate filter dimension + its cached values across the
    // dashboard's source charts, so the filter manager and new chips have options
    const chartIds = [...new Set(dash.tabs.flatMap((t) => t.widgets.map((w) => w.source_chart_id)))]
    setFilterCandidates(null)
    Promise.all(chartIds.map((id) => Promise.all([
      api.getDimsMetrics(id).catch(() => null),
      api.getDimValues(id).catch(() => null),
    ])))
      .then((results) => {
        const dimSet = new Set<string>()
        const values: Record<string, FilterValue[]> = {}
        for (const [dm, dv] of results) {
          if (dm) for (const d of dm.dimensions) dimSet.add(d.name)
          if (dv) for (const [k, vs] of Object.entries(dv.dimensions)) values[k] = [...new Set([...(values[k] || []), ...(vs as unknown as FilterValue[])])]
        }
        setFilterCandidates([...dimSet].sort())
        setAllDimValues(values)
        // enrich existing chips' option lists with the fuller value sets
        setChips((cur) => cur.map((c) => ({ ...c, options: values[c.dimension] || c.options })))
      })
      .catch(() => setFilterCandidates([]))
  }, [dash, ensureCharts])

  const onDiscard = useCallback(() => {
    setEditing(false)
    setDraft(null)
    setSaveDirty(false)
    reload(false).catch(fail) // nothing was persisted; just reset to saved state
  }, [reload])

  const onSave = useCallback(async () => {
    const orig = originalRef.current
    if (!draft || !orig) return
    setSaving(true)
    try {
      if (draft.enabled !== orig.enabled) await api.updateDashboard(dashboardId, { enabled: draft.enabled })

      // tabs: delete removed (cascades their widgets), create new, rename/reorder
      const draftRealTabIds = new Set(draft.tabs.filter((t) => t.id > 0).map((t) => t.id))
      for (const ot of orig.tabs) if (!draftRealTabIds.has(ot.id)) await api.deleteDashboardTab(dashboardId, ot.id)
      const tabRealId = new Map<number, number>()
      for (let i = 0; i < draft.tabs.length; i++) {
        const t = draft.tabs[i]
        if (t.id < 0) {
          const created = await api.addDashboardTab(dashboardId, t.name)
          tabRealId.set(t.id, created.id)
        } else {
          tabRealId.set(t.id, t.id)
          const ot = orig.tabs.find((x) => x.id === t.id)
          if (ot && (ot.name !== t.name || ot.display_order !== i)) await api.updateDashboardTab(dashboardId, t.id, { name: t.name, display_order: i })
        }
      }

      // widgets: delete removed (only on surviving tabs), create new, update changed
      const draftWidgetIds = new Set(draft.tabs.flatMap((t) => t.widgets).filter((w) => w.id > 0).map((w) => w.id))
      for (const ot of orig.tabs) {
        if (!draftRealTabIds.has(ot.id)) continue
        for (const ow of ot.widgets) if (!draftWidgetIds.has(ow.id)) await api.deleteDashboardWidget(dashboardId, ow.id)
      }
      const origWidgetById = new Map(orig.tabs.flatMap((t) => t.widgets).map((w) => [w.id, w]))
      for (const t of draft.tabs) {
        const realTab = tabRealId.get(t.id)!
        for (const w of t.widgets) {
          if (w.id < 0) {
            await api.addDashboardWidget(dashboardId, realTab, { type: w.type, source_chart_id: w.source_chart_id, name: w.name, layout: w.layout, config: w.config })
          } else {
            const ow = origWidgetById.get(w.id)
            if (!ow) continue
            const patch: WidgetWriteBody = {}
            if (ow.name !== w.name) patch.name = w.name
            if (ow.source_chart_id !== w.source_chart_id) patch.source_chart_id = w.source_chart_id
            if (JSON.stringify(ow.config) !== JSON.stringify(w.config)) patch.config = w.config
            if (!sameLayout(ow.layout, w.layout)) patch.layout = w.layout
            if (Object.keys(patch).length) await api.updateDashboardWidget(dashboardId, w.id, patch)
          }
        }
      }

      // filters: replace with the current chip set (dimension + selected defaults)
      await api.putDashboardFilters(dashboardId, chipsRef.current.map((c) => ({ dimension: c.dimension, default_values: c.selected })))

      setEditing(false)
      setDraft(null)
      setSaveDirty(false)
      await reload(false)
    } catch (e) {
      fail(e)
    } finally {
      setSaving(false)
    }
  }, [draft, dashboardId, reload])

  const onToggleDashEnabled = useCallback((on: boolean) => { mutateDraft((d) => ({ ...d, enabled: on })) }, [mutateDraft])

  // ---------- edit mode: layout ----------
  const onLayoutChange = useCallback((items: ({ widget_id: number } & WidgetLayout)[]) => {
    const tab = draftActiveRef.current
    if (!tab) return
    const changed = items.some((it) => {
      const w = tab.widgets.find((x) => x.id === it.widget_id)
      return w && !sameLayout(w.layout, it)
    })
    if (!changed) return // RGL echoes the current layout on mount — ignore
    const byId = new Map(items.map((it) => [it.widget_id, it]))
    mutateDraft((d) => ({
      ...d,
      tabs: d.tabs.map((t) => (t.id !== tab.id ? t : {
        ...t,
        widgets: t.widgets.map((w) => { const it = byId.get(w.id); return it ? { ...w, layout: { x: it.x, y: it.y, w: it.w, h: it.h } } : w }),
      })),
    }))
  }, [mutateDraft])

  // ---------- edit mode: tabs ----------
  const onAddTab = useCallback(() => {
    const name = window.prompt('Tab name:')
    if (!name || !name.trim()) return
    const id = nextTempId()
    mutateDraft((d) => ({ ...d, tabs: [...d.tabs, { id, name: name.trim(), widgets: [] }] }))
    setActiveTabId(id)
  }, [mutateDraft])
  const onRenameTab = useCallback((tabId: number) => {
    const cur = draft?.tabs.find((t) => t.id === tabId)
    const name = window.prompt('Rename tab:', cur?.name || '')
    if (!name || !name.trim()) return
    mutateDraft((d) => ({ ...d, tabs: d.tabs.map((t) => (t.id === tabId ? { ...t, name: name.trim() } : t)) }))
  }, [draft, mutateDraft])
  const onMoveTab = useCallback((tabId: number, dir: -1 | 1) => {
    mutateDraft((d) => {
      const idx = d.tabs.findIndex((t) => t.id === tabId)
      const j = idx + dir
      if (idx < 0 || j < 0 || j >= d.tabs.length) return d
      const tabs = [...d.tabs]
      ;[tabs[idx], tabs[j]] = [tabs[j], tabs[idx]]
      return { ...d, tabs }
    })
  }, [mutateDraft])
  const onDeleteTab = useCallback((tabId: number) => {
    if (!draft || draft.tabs.length <= 1) { window.alert('A dashboard must keep at least one tab.'); return }
    const t = draft.tabs.find((x) => x.id === tabId)
    if (!t) return
    if (!window.confirm(`Delete tab “${t.name}”${t.widgets.length ? ` and its ${t.widgets.length} widget(s)` : ''}?`)) return
    mutateDraft((d) => ({ ...d, tabs: d.tabs.filter((x) => x.id !== tabId) }))
    if (activeTabId === tabId) setActiveTabId(draft.tabs.find((x) => x.id !== tabId)?.id ?? null)
  }, [draft, activeTabId, mutateDraft])

  // ---------- edit mode: widgets ----------
  const maxY = (tab: DraftTab) => tab.widgets.reduce((m, w) => Math.max(m, w.layout.y + w.layout.h), 0)

  const openQuickAdd = useCallback((type: 'chart' | 'number') => { setQuickAdd(type); setQuickAddMetrics(null); ensureCharts() }, [ensureCharts])
  const onQuickAddPickChart = useCallback((chartId: number) => {
    setQuickAddMetrics(null)
    api.getDimsMetrics(chartId).then((dm) => setQuickAddMetrics(dm.metrics.map((m) => m.name))).catch(fail)
  }, [])
  const onQuickAddSubmit = useCallback((v: { source_chart_id: number; name: string; metric: string }) => {
    if (!quickAdd) return
    const isChart = quickAdd === 'chart'
    const id = nextTempId()
    mutateDraft((d) => ({
      ...d,
      tabs: d.tabs.map((t) => (t.id !== activeTabId ? t : {
        ...t,
        widgets: [...t.widgets, {
          id, type: quickAdd, source_chart_id: v.source_chart_id, name: v.name,
          layout: { x: 0, y: maxY(t), w: isChart ? 6 : 3, h: isChart ? 4 : 2 },
          config: isChart ? { metrics: [{ name: v.metric }] } : { metric: v.metric },
        }],
      })),
    }))
    setQuickAdd(null)
  }, [quickAdd, activeTabId, mutateDraft])

  const onWidgetDuplicate = useCallback((widgetId: number) => {
    mutateDraft((d) => ({
      ...d,
      tabs: d.tabs.map((t) => {
        if (t.id !== activeTabId) return t
        const src = t.widgets.find((w) => w.id === widgetId)
        if (!src) return t
        return { ...t, widgets: [...t.widgets, { ...src, id: nextTempId(), name: `${src.name} (copy)`, layout: { ...src.layout, x: 0, y: maxY(t) }, config: { ...src.config } }] }
      }),
    }))
  }, [activeTabId, mutateDraft])

  const onWidgetDelete = useCallback((widgetId: number) => {
    const w = draftActiveRef.current?.widgets.find((x) => x.id === widgetId)
    if (!w || !window.confirm(`Delete widget “${w.name}”?`)) return
    mutateDraft((d) => ({ ...d, tabs: d.tabs.map((t) => (t.id === activeTabId ? { ...t, widgets: t.widgets.filter((x) => x.id !== widgetId) } : t)) }))
  }, [activeTabId, mutateDraft])

  const onWidgetSettings = useCallback((widgetId: number) => { ensureCharts(); setSettingsWidgetId(widgetId) }, [ensureCharts])
  const onModalApply = useCallback((patch: WidgetPatch) => {
    const wid = settingsWidgetId
    mutateDraft((d) => ({
      ...d,
      tabs: d.tabs.map((t) => ({ ...t, widgets: t.widgets.map((w) => (w.id === wid ? { ...w, source_chart_id: patch.source_chart_id, name: patch.name, config: patch.config } : w)) })),
    }))
    setSettingsWidgetId(null)
  }, [settingsWidgetId, mutateDraft])

  // ---------- edit mode: add/remove a global filter chip (staged) ----------
  const onToggleFilterDim = useCallback((dim: string, on: boolean) => {
    updateChips((prev) => on
      ? (prev.some((c) => c.dimension === dim) ? prev : [...prev, { dimension: dim, options: allDimValues[dim] || [], selected: [], split: false }])
      : prev.filter((c) => c.dimension !== dim))
  }, [updateChips, allDimValues])

  if (error) return <div className="flex h-full items-center justify-center font-sans text-[14px] text-rose-500">Failed to load dashboard: {error}</div>
  if (!dash || !dateRange) return <div className="flex h-full items-center justify-center font-sans text-[14px] text-slate-400">Loading…</div>

  const barTabs = (editing ? draft?.tabs : dash.tabs)?.map((t) => ({ id: t.id, name: t.name })) ?? []
  const editWidgets = draftActiveTab?.widgets ?? []
  const layoutById: Record<number, WidgetLayout> = Object.fromEntries(editWidgets.map((w) => [w.id, w.layout]))
  const settingsWidget = editWidgets.find((w) => w.id === settingsWidgetId) ?? null

  return (
    <div className="flex h-full flex-col bg-slate-50 font-sans text-slate-900">
      <div className="shrink-0 bg-white">
        {editing && draft ? (
          <>
            <EditHeader title={dash.name} number={dash.number} enabled={draft.enabled} saveDirty={saveDirty && !saving} onToggleEnabled={onToggleDashEnabled} onSave={onSave} onDiscard={onDiscard} />
            <EditTabBar tabs={barTabs} activeId={activeTabId} onTabChange={setActiveTabId} onAddTab={onAddTab} onRenameTab={onRenameTab} onMoveTab={onMoveTab} onDeleteTab={onDeleteTab} />
          </>
        ) : (
          <>
            <DashboardHeader title={dash.name} number={dash.number} enabled={dash.enabled} onReplicate={onReplicate} onGoHome={onGoHome} onEdit={enterEdit} />
            <TabBar tabs={barTabs} activeId={activeTabId} onTabChange={setActiveTabId} />
          </>
        )}
      </div>
      <div className="shrink-0">
        <ControlsRow granularity={granularity} dateRange={dateRange} onGranularityChange={setGranularity} onDateRangeChange={setDateRange} />
        {editing ? (
          <EditToolbar chips={chips} filterCandidates={filterCandidates} onAddWidget={openQuickAdd} onToggleFilterDim={onToggleFilterDim} onFilterChange={onFilterChange} onToggleSplit={onToggleSplit} />
        ) : (
          <GlobalFilterBar chips={chips} dirty={dirty} onFilterChange={onFilterChange} onToggleSplit={onToggleSplit} onApply={onApply} />
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {editing ? (
          draftActiveTab && (
            <EditableWidgetGrid
              key={`${draftActiveTab.id}:${draftActiveTab.widgets.map((w) => w.id).join('.')}`}
              widgets={editWidgets as unknown as DashWidget[]}
              layoutById={layoutById}
              dataById={dataById}
              onLayoutChange={onLayoutChange}
              onWidgetSettings={onWidgetSettings}
              onWidgetDuplicate={onWidgetDuplicate}
              onWidgetDelete={onWidgetDelete}
            />
          )
        ) : (
          activeTab && <WidgetGrid widgets={activeTab.widgets} dataById={dataById} onOpenChart={onOpenChart} />
        )}
      </div>
      {quickAdd && (
        <QuickAddWidget type={quickAdd} charts={chartOptions || []} metricOptions={quickAddMetrics} onPickChart={onQuickAddPickChart} onCancel={() => setQuickAdd(null)} onSubmit={onQuickAddSubmit} />
      )}
      {settingsWidget && (
        <EditWidgetModal widget={settingsWidget} charts={chartOptions || []} onApply={onModalApply} onCancel={() => setSettingsWidgetId(null)} />
      )}
    </div>
  )
}
