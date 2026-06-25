import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { BackpopRun, ChartSummary, ChartWriteBody } from '../../api/types'
import { ConfigView } from './ConfigView'
import type { ConfigColumn, VarRow } from './ConfigView'

const DR_DAYS: Record<string, number> = { 'Last 30 Days': 30, 'Last 60 Days': 60, 'Last 90 Days': 90, 'Last 120 Days': 120, 'Last 365 Days': 365 }
const DATE_FORMATS = ['%Y-%m-%d', '%Y%m%d', '%Y-%m', '%Y/%m/%d']
const daysToLabel = (n: number) => Object.entries(DR_DAYS).find(([, d]) => d === n)?.[0] || `Last ${n} Days`
const labelToDays = (l: string) => DR_DAYS[l] ?? (parseInt(l.match(/\d+/)?.[0] || '90', 10))
/* {CUR_DATE_HIPHEN} behaviour ↔ backend value. Daily = one query per day (pair
   with = '{CUR_DATE_HIPHEN}'); Batched = N-day windows (pair with BETWEEN). */
const CUR_DATE_OPTS: Record<string, string> = {
  'Daily (one query per day)': 'daily',
  'Batched (N-day windows)': 'batched',
}
const curDateLabel = (v: string) => Object.entries(CUR_DATE_OPTS).find(([, val]) => val === v)?.[0] || 'Daily (one query per day)'
const curDateValue = (label: string) => CUR_DATE_OPTS[label] ?? 'daily'
const CACHE_LABEL = 'Append (fill-missing)'

/* Flag a query whose date placeholder doesn't match the chosen run mode. */
function placeholderModeWarning(sql: string, mode: string): string | null {
  const hasCur = /\{CUR_DATE_(HIPHEN|UNDERSCORE)\}/.test(sql)
  const hasRange = /\{START_DATE\}/.test(sql) && /\{END_DATE\}/.test(sql)
  if (mode === 'batched' && hasCur && !hasRange)
    return "Batched mode runs multi-day windows, but {CUR_DATE_HIPHEN} resolves only to each window's last day — the earlier days won't be fetched. Use BETWEEN '{START_DATE}' AND '{END_DATE}', or switch to Daily."
  if (!hasCur && !hasRange)
    return "No date placeholder found — backpopulation can't target the selected range. Use = '{CUR_DATE_HIPHEN}' (Daily) or BETWEEN '{START_DATE}' AND '{END_DATE}' (Batched) instead of a hardcoded date."
  return null
}

/* Turn a raw backpop/introspect error into a plain-English cause, so a Redshift
   connectivity problem isn't mistaken for a bad query (and vice-versa). */
function explainRunError(msg: string): string {
  const m = msg || ''
  if (/Connection refused|BrokenPipe|communication error|timed out|could not connect|getaddrinfo|Name or service|socket closed|InterfaceError/i.test(m))
    return "Couldn't reach Redshift — the query never ran. Check the cluster is up and your network / security-group / VPN allows port 5439."
  if (/unresolved template variable|UnresolvedVariable|chart\.variables/i.test(m))
    return 'A {TEMPLATE} variable in the query is undefined — add it under Query Variables, then try again.'
  if (!m) return 'Backpopulation failed (no error detail was returned).'
  return 'Redshift rejected the query — the SQL looks wrong. Review the error below and fix the query.'
}

function varsToRows(v: Record<string, string | string[]>): VarRow[] {
  return Object.entries(v || {}).map(([name, val]) => ({ name, value: Array.isArray(val) ? val.join(', ') : String(val) }))
}
function rowsToVars(rows: VarRow[]): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {}
  for (const r of rows) {
    const name = r.name.trim()
    if (!name) continue
    const v = r.value.trim()
    out[name] = v.includes(',') ? v.split(',').map((x) => x.trim()).filter(Boolean) : v
  }
  return out
}
function isoDaysAgo(days: number): string {
  const d = new Date(); d.setDate(d.getDate() - days + 1)
  return d.toISOString().slice(0, 10)
}
const todayIso = () => new Date().toISOString().slice(0, 10)

/* Mirror of the backend's chart-number allocation, for an unsaved UI preview. */
function nextNumberPreview(certified: boolean, charts: ChartSummary[], currentId: number | null): number {
  const nums = charts.filter((c) => c.id !== currentId).map((c) => c.chart_number).filter((n): n is number => n != null)
  if (certified) {
    const s = nums.filter((n) => n >= 100 && n < 1000)
    return (s.length ? Math.max(...s) : 99) + 1
  }
  const s = nums.filter((n) => n >= 1000)
  return (s.length ? Math.max(...s) : 999) + 1
}

export function ConfigContainer({ target, onBack, onSaved, onDeleted, charts }: {
  target: number | 'new'
  onBack: () => void
  onSaved: (chartId: number) => void
  onDeleted: () => void
  charts: ChartSummary[]
}) {
  const [savedId, setSavedId] = useState<number | null>(typeof target === 'number' ? target : null)
  const [meta, setMeta] = useState<{ title: string; source: string; certified: boolean; number: number | null }>({ title: '', source: 'redshift', certified: false, number: null })
  const [variables, setVariables] = useState<VarRow[]>([])
  const [cache, setCache] = useState<Record<string, string>>({
    defaultDateRange: 'Last 90 Days', refreshInterval: 'Daily',
    curDateBehaviour: curDateLabel('daily'), chartCache: CACHE_LABEL,
    backpopDays: '7', backpopBatch: '30',
  })
  const [query, setQuery] = useState('')
  const [queryTheme, setQueryTheme] = useState('light')
  const [generated, setGenerated] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [dims, setDims] = useState({ xAxis: '', timeColumn: '', dateFormat: '%Y-%m-%d', axisOptions: [] as string[], timeOptions: [] as string[], dateFormatOptions: DATE_FORMATS })
  const [columns, setColumns] = useState<ConfigColumn[]>([])
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveOk, setSaveOk] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [runs, setRuns] = useState<BackpopRun[]>([])

  /* ---- prefill in edit mode ---- */
  useEffect(() => {
    if (typeof target !== 'number') return
    let alive = true
    ;(async () => {
      try {
        const c = await api.getChart(target)
        const dm = await api.getDimsMetrics(target)
        if (!alive) return
        setMeta({ title: c.name, source: c.source, certified: c.certified, number: c.chart_number })
        setVariables(varsToRows(c.variables))
        setCache({
          defaultDateRange: daysToLabel(c.default_date_range_days),
          refreshInterval: c.refresh_interval.charAt(0).toUpperCase() + c.refresh_interval.slice(1),
          curDateBehaviour: curDateLabel(c.cur_date_behavior), chartCache: CACHE_LABEL,
          backpopDays: String(c.default_backpop_days), backpopBatch: String(c.backpop_batch_size),
        })
        setQuery(c.query)
        const dimNames = dm.dimensions.map((d) => d.name)
        if (dm.dimensions.length || dm.metrics.length) {
          setColumns([
            ...dm.dimensions.filter((d) => !d.derived).map<ConfigColumn>((d) => ({ name: d.name, classification: 'Dimension', dataType: d.data_type || '—', independentOf: [], valueOrder: d.value_order || 'natural', included: true })),
            ...dm.metrics.map<ConfigColumn>((m) => ({ name: m.name, classification: 'Metric', dataType: m.data_type || '—', independentOf: m.independent_dimensions || [], formula: m.formula || null, decimals: m.decimals ?? 0, yAxis: m.y_axis || 'primary', unit: m.unit || null, included: true })),
          ])
          setDims((s) => ({ ...s, timeColumn: dm.time_column || '', xAxis: dm.time_column || dimNames[0] || '', dateFormat: dm.date_format || '%Y-%m-%d', axisOptions: [dm.time_column || '', ...dimNames].filter(Boolean), timeOptions: [dm.time_column || ''].filter(Boolean) }))
          setGenerated(true)
        }
        const rr = await api.backpopRuns(target).catch(() => [])
        if (alive) setRuns(rr)
      } catch (e: any) {
        if (alive) setLoadError(String(e.message || e))
      }
    })()
    return () => { alive = false }
  }, [target])

  // keep the backpop history live without a manual refresh — picks up runs started
  // here or from the chart view (poll while the tab is visible)
  useEffect(() => {
    const id = savedId ?? (typeof target === 'number' ? target : null)
    if (id == null) return
    const t = setInterval(() => {
      if (!document.hidden) api.backpopRuns(id).then(setRuns).catch(() => {})
    }, 4000)
    return () => clearInterval(t)
  }, [savedId, target])

  const buildBody = useCallback((): ChartWriteBody => ({
    name: meta.title.trim(), source: meta.source, certified: meta.certified, query,
    refresh_interval: cache.refreshInterval.toLowerCase(),
    default_backpop_days: Math.max(1, parseInt(cache.backpopDays || '7', 10)),
    backpop_batch_size: Math.max(1, parseInt(cache.backpopBatch || '30', 10)),
    default_date_range_days: labelToDays(cache.defaultDateRange),
    cur_date_behavior: curDateValue(cache.curDateBehaviour), cache_strategy: 'append',
    date_format: dims.dateFormat, variables: rowsToVars(variables),
  }), [meta, query, cache, dims.dateFormat, variables])

  /* ensure the chart exists/updated; returns its id (or null on error already surfaced) */
  const ensureSaved = async (): Promise<number | null> => {
    const body = buildBody()
    if (!body.name) { setGenerateError('Enter a title before generating.'); setSaveError('Enter a title first.'); return null }
    if (savedId == null) {
      const created = await api.createChart(body)
      setSavedId(created.id)
      setMeta((m) => ({ ...m, certified: created.certified, number: created.chart_number }))
      return created.id
    }
    const updated = await api.updateChart(savedId, body)
    setMeta((m) => ({ ...m, certified: updated.certified, number: updated.chart_number }))
    return savedId
  }

  const onGenerate = async () => {
    setGenerating(true); setGenerateError(null)
    try {
      const id = await ensureSaved()
      if (id == null) { setGenerating(false); return }
      const r = await api.introspect(id)
      const dimNames = r.dimensions.map((d) => d.name)
      const introspected = new Set([...r.dimensions.map((d) => d.name), ...r.metrics.map((m) => m.name)])
      // re-introspecting reads the query's columns; formula metrics aren't column-backed,
      // so keep the ones the user built in the chart view rather than dropping them
      const keptFormulas = columns.filter((c) => c.classification === 'Metric' && c.formula && !introspected.has(c.name))
      setColumns([
        ...r.dimensions.map<ConfigColumn>((d) => ({ name: d.name, classification: 'Dimension', dataType: d.data_type || '—', independentOf: [], valueOrder: 'natural', included: true })),
        ...r.metrics.map<ConfigColumn>((m) => ({ name: m.name, classification: 'Metric', dataType: m.data_type || '—', independentOf: [], included: true })),
        ...keptFormulas,
      ])
      setDims((s) => ({ ...s, timeColumn: r.time_column || '', xAxis: r.time_column || dimNames[0] || '', axisOptions: [r.time_column || '', ...dimNames].filter(Boolean), timeOptions: [r.time_column || ''].filter(Boolean) }))
      setGenerated(true)
    } catch (e: any) {
      const msg = String(e.message || e)
      setGenerateError(`${explainRunError(msg)} — ${msg.slice(0, 200)}`)
    } finally {
      setGenerating(false)
    }
  }

  const putDimsMetrics = async (id: number) => {
    const includedDims = columns.filter((c) => c.classification === 'Dimension' && c.included)
    const includedDimNames = new Set(includedDims.map((d) => d.name))
    const metricsPayload = columns.filter((c) => c.classification === 'Metric' && c.included).map((m) => ({
      name: m.name,
      // formula metrics are not column-backed (the backend requires exactly one of
      // column_name | formula); preserve everything edited in the chart view
      column_name: m.formula ? null : m.name,
      independent_dimensions: (m.independentOf || []).filter((n) => includedDimNames.has(n)),
      formula: m.formula || null,
      y_axis: m.yAxis || ('primary' as const),
      decimals: m.decimals ?? 0,
      unit: m.unit && m.unit !== 'None' ? m.unit : null,
    }))
    await api.putDimsMetrics(id, {
      time_column: dims.timeColumn || null,
      dimensions: includedDims.map((d) => ({ name: d.name, column_name: d.name, value_order: d.valueOrder || 'natural' })),
      metrics: metricsPayload,
    })
  }

  const onSaveDraft = async () => {
    setSaving(true); setSaveError(null); setSaveOk(null)
    try {
      const id = await ensureSaved()
      if (id == null) { setSaving(false); return }
      if (generated) await putDimsMetrics(id)
      onSaved(id) // refresh chart list so the new number/cert shows in the chart view
      setSaveOk('Saved.')
    } catch (e: any) {
      setSaveError(String(e.message || e))
    } finally { setSaving(false) }
  }

  const loadRuns = async (id: number) => {
    try { setRuns(await api.backpopRuns(id)) } catch { /* history is best-effort */ }
  }

  const onCancelRun = async (runId: number) => {
    const id = savedId ?? (typeof target === 'number' ? target : null)
    if (id == null) return
    setToast('Cancelling backpopulation…')
    try { await api.cancelBackpop(id, runId) } catch { /* the history poll will reflect it */ }
    await loadRuns(id)
  }

  const onSaveBackpopulate = async (range: { start: string; end: string }) => {
    setSaving(true); setSaveError(null); setSaveOk(null); setToast('Backpopulation started…')
    try {
      const id = await ensureSaved()
      if (id == null) { setSaving(false); setToast(null); return }
      if (generated) await putDimsMetrics(id)
      const run = await api.backpopulate(id, { from_date: range.start, to_date: range.end, batch_size: Math.max(1, parseInt(cache.backpopBatch || '30', 10)) })
      await loadRuns(id)
      onSaved(id) // refresh chart list in the background; stay on this page
      if (run.status === 'success') {
        setToast(`✓ Backpopulation complete · ${run.row_count.toLocaleString()} rows (${run.from_date} → ${run.to_date})`)
      } else if (run.status === 'cancelled') {
        setToast(`Backpopulation cancelled · ${run.row_count.toLocaleString()} rows kept`)
      } else {
        const why = explainRunError(run.error_message || '')
        setSaveError(`${why}${run.error_message ? ' — ' + run.error_message.slice(0, 200) : ''}`)
        setToast(`Backpopulation ${run.status}: ${why}`)
      }
    } catch (e: any) {
      const msg = String(e.message || e)
      const why = explainRunError(msg)
      setSaveError(`${why} — ${msg.slice(0, 200)}`)
      setToast(`Backpopulation failed: ${why}`)
    } finally {
      setSaving(false)
      window.setTimeout(() => setToast(null), 7000)
    }
  }

  const onColumnChange = useCallback((name: string, patch: Partial<ConfigColumn>) => {
    setColumns((cs) => cs.map((c) => c.name === name ? { ...c, ...patch } : c))
  }, [])

  // UI-only preview: toggling certified shows the number it WILL get; the backend
  // only re-numbers on save. The saved number's range encodes the saved cert state.
  const savedCert = meta.number != null ? meta.number < 1000 : null
  const previewNumber = savedCert != null && meta.certified !== savedCert
    ? nextNumberPreview(meta.certified, charts, savedId)
    : null

  const onDelete = async () => {
    if (savedId == null) return
    try {
      await api.deleteChart(savedId)
      onDeleted()
    } catch (e: any) {
      setSaveError(`Couldn't delete chart: ${String(e.message || e).slice(0, 160)}`)
    }
  }

  const backpopDefaults = useMemo(() => ({ start: isoDaysAgo(parseInt(cache.backpopDays || '7', 10)), end: todayIso() }), [cache.backpopDays])
  const queryModeWarning = useMemo(() => placeholderModeWarning(query, curDateValue(cache.curDateBehaviour)), [query, cache.curDateBehaviour])

  if (loadError) return <div className="flex h-full items-center justify-center text-[14px] text-rose-500">Failed to load chart: {loadError}</div>

  return (
    <ConfigView
      mode={typeof target === 'number' ? 'edit' : 'create'}
      chartTitleLabel={meta.title || (typeof target === 'number' ? `Chart ${target}` : 'New chart')}
      meta={meta} sourceOptions={['redshift']} onMetaChange={(patch) => setMeta((m) => ({ ...m, ...patch }))} previewNumber={previewNumber}
      variables={variables} onVariablesChange={setVariables}
      cache={cache} onCacheChange={(patch) => setCache((c) => ({ ...c, ...patch }))}
      cacheOptions={{
        dateRange: Array.from(new Set([...Object.keys(DR_DAYS), cache.defaultDateRange])),
        refresh: ['Hourly', 'Daily', 'Weekly', 'Manual'],
        curDate: Object.keys(CUR_DATE_OPTS), chartCache: [CACHE_LABEL],
      }}
      query={query} onQueryChange={setQuery} queryTheme={queryTheme} onQueryThemeChange={setQueryTheme} queryModeWarning={queryModeWarning}
      onGenerate={onGenerate} generated={generated} generating={generating} generateError={generateError}
      dims={dims} onAxisFieldChange={(patch) => setDims((d) => ({ ...d, ...patch }))} columns={columns} onColumnChange={onColumnChange}
      onBack={onBack} onDelete={savedId != null ? onDelete : undefined}
      onSaveDraft={onSaveDraft} onSaveBackpopulate={onSaveBackpopulate} backpopDefaults={backpopDefaults}
      saving={saving} saveError={saveError} saveOk={saveOk}
      runs={runs} onCancelRun={onCancelRun} toast={toast}
    />
  )
}
