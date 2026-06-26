import { useState } from 'react'
import type { BackpopRun } from '../../api/types'
import { ConfigField, ConfigInput, ConfigSelect, SectionHeader, CIc } from './fields'
import { VariablesEditor } from './VariablesEditor'
import type { VarRow } from './VariablesEditor'
import { QueryBox } from './QueryBox'
import { DimsMetricsTable } from './DimsMetricsTable'
import type { ConfigColumn } from './DimsMetricsTable'
import { BackpopulateModal } from './BackpopulateModal'
import { BackpopHistory } from './BackpopHistory'
import { DeleteConfirm } from './DeleteConfirm'

export type { ConfigColumn } from './DimsMetricsTable'
export type { VarRow } from './VariablesEditor'

/* ------------------------------------------------------------------ ConfigView */
export interface ConfigViewProps {
  mode: 'create' | 'edit'
  chartTitleLabel: string
  meta: { title: string; source: string; certified: boolean; number: number | null }
  sourceOptions: string[]
  previewNumber?: number | null
  onMetaChange: (patch: Partial<{ title: string; source: string; certified: boolean }>) => void
  variables: VarRow[]; onVariablesChange: (rows: VarRow[]) => void
  cache: Record<string, string>; cacheOptions: Record<string, string[]>; onCacheChange: (patch: Record<string, string>) => void
  query: string; onQueryChange: (v: string) => void; queryTheme: string; onQueryThemeChange: (t: string) => void
  queryModeWarning?: string | null
  onGenerate: () => void; generated: boolean; generating: boolean; generateError?: string | null
  dims: { xAxis: string; timeColumn: string; dateFormat: string; axisOptions: string[]; timeOptions: string[]; dateFormatOptions: string[] }
  onAxisFieldChange: (patch: Record<string, string>) => void
  columns: ConfigColumn[]; onColumnChange: (name: string, patch: Partial<ConfigColumn>) => void
  onBack: () => void
  onDelete?: () => void
  onSaveDraft: () => void; onSaveBackpopulate: (r: { start: string; end: string; force: boolean }) => void
  backpopDefaults: { start: string; end: string }
  saving?: boolean; saveError?: string | null; saveOk?: string | null
  runs?: BackpopRun[]
  onCancelRun?: (runId: number) => void
  toast?: string | null
}

export function ConfigView(p: ConfigViewProps) {
  const [backpopOpen, setBackpopOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  return (
    <div className="flex h-full flex-col bg-slate-100 font-sans text-slate-800">
      <header className="flex h-12 shrink-0 items-center gap-4 bg-slate-800 px-4 text-slate-200">
        <div className="flex h-7 w-7 items-center justify-center rounded bg-sky-500 text-xs font-bold text-white">FG</div>
        <span className="text-[13px] font-semibold text-white">Configurations</span>
        <button onClick={p.onBack} className="ml-2 flex items-center gap-1.5 rounded-md border border-slate-600 bg-slate-700/70 px-2.5 py-1.5 text-[12px] text-slate-200 hover:bg-slate-700"><CIc.back /> Back to chart</button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1320px] px-6 py-5">
          <div className="mb-5 flex items-center gap-3">
            <h1 className="text-[22px] font-bold text-slate-800">{p.chartTitleLabel}</h1>
            <span className="rounded bg-sky-50 px-2 py-0.5 text-[11px] font-semibold text-sky-600">{p.mode === 'create' ? 'New chart' : 'Editing'}</span>
          </div>

          <div className="mb-4 rounded-xl bg-white p-5 shadow-sm">
            <div className="grid grid-cols-3 gap-x-6 gap-y-3">
              <ConfigField label="Title" required><ConfigInput value={p.meta.title} onChange={(v) => p.onMetaChange({ title: v })} placeholder="Chart name" /></ConfigField>
              <ConfigField label="Source" required><ConfigSelect value={p.meta.source} onChange={(v) => p.onMetaChange({ source: v })} options={p.sourceOptions} center /></ConfigField>
              <ConfigField label="Certified" hint="Certified charts are numbered from 100, drafts from 1000. Toggling re-numbers a saved chart immediately; a new chart gets its number on first save.">
                <div className="flex items-center gap-3 py-1.5">
                  <button role="switch" aria-checked={p.meta.certified} onClick={() => p.onMetaChange({ certified: !p.meta.certified })}
                    className={'relative inline-block h-5 w-9 shrink-0 rounded-full transition-colors ' + (p.meta.certified ? 'bg-emerald-500' : 'bg-slate-200')}>
                    <span className={'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ' + (p.meta.certified ? 'left-[18px]' : 'left-0.5')} />
                  </button>
                  <span className="text-[12px] text-slate-500">
                    {p.meta.certified ? 'Certified' : 'Draft'}
                    {p.previewNumber != null ? (
                      <> · <span className="font-mono text-amber-600">#{p.previewNumber}</span> <span className="text-[11px] text-amber-500">(unsaved)</span></>
                    ) : p.meta.number != null ? (
                      <> · <span className="font-mono text-slate-600">#{p.meta.number}</span></>
                    ) : null}
                  </span>
                </div>
              </ConfigField>
            </div>
          </div>

          <div className="mb-4 rounded-xl bg-white p-5 shadow-sm">
            <SectionHeader>Query Variables</SectionHeader>
            <VariablesEditor rows={p.variables} onChange={p.onVariablesChange} />
          </div>

          <div className="mb-4 rounded-xl bg-white p-5 shadow-sm">
            <SectionHeader>Chart Cache and Backpopulation</SectionHeader>
            <div className="grid grid-cols-4 gap-x-6 gap-y-3">
              <ConfigField label="Default Date Range" required><ConfigSelect value={p.cache.defaultDateRange} onChange={(v) => p.onCacheChange({ defaultDateRange: v })} options={p.cacheOptions.dateRange} center /></ConfigField>
              <ConfigField label="Refresh Interval" required><ConfigSelect value={p.cache.refreshInterval} onChange={(v) => p.onCacheChange({ refreshInterval: v })} options={p.cacheOptions.refresh} center /></ConfigField>
              <ConfigField label="{CUR_DATE_HIPHEN} Behaviour" required hint="Daily: one query per day — pair with = '{CUR_DATE_HIPHEN}'. Batched: N-day windows — pair with BETWEEN '{START_DATE}' AND '{END_DATE}'."><ConfigSelect value={p.cache.curDateBehaviour} onChange={(v) => p.onCacheChange({ curDateBehaviour: v })} options={p.cacheOptions.curDate} center /></ConfigField>
              <ConfigField label="Chart Cache" required hint="Appends (fill-missing): older cached days are skipped, but the last 4 days are always re-pulled so late-arriving data is caught."><ConfigSelect value={p.cache.chartCache} options={p.cacheOptions.chartCache} disabled center /></ConfigField>
              <ConfigField label="Default Backpopulation Days" required><ConfigInput type="number" value={p.cache.backpopDays} onChange={(v) => p.onCacheChange({ backpopDays: v })} /></ConfigField>
              <ConfigField label="Backpopulation Batch Size" required><ConfigInput type="number" value={p.cache.backpopBatch} onChange={(v) => p.onCacheChange({ backpopBatch: v })} /></ConfigField>
              <ConfigField label="Default Data Recency" hint="The chart opens showing data up to this many days before today (the default for the in-chart recency selector)."><ConfigSelect value={p.cache.dataRecency} onChange={(v) => p.onCacheChange({ dataRecency: v })} options={['0', '1', '2', '3', '7', '14']} center /></ConfigField>
            </div>
          </div>

          <div className="mb-4 rounded-xl bg-white p-5 shadow-sm">
            <SectionHeader>Query Box</SectionHeader>
            <QueryBox value={p.query} onChange={p.onQueryChange} theme={p.queryTheme} onThemeChange={p.onQueryThemeChange} maxHeight={360} />
            {p.generateError && <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-600">{p.generateError}</div>}
            {p.queryModeWarning && <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-700"><span className="mt-0.5 shrink-0"><CIc.info /></span>{p.queryModeWarning}</div>}
            <div className="mt-4 flex justify-center">
              <button onClick={p.onGenerate} disabled={p.generating} className={'flex items-center gap-2 rounded-md px-5 py-2.5 text-[13px] font-semibold text-white shadow-sm transition-colors ' + (p.generating ? 'cursor-wait bg-sky-400' : 'bg-sky-500 hover:bg-sky-600')}>
                <CIc.spark /> {p.generating ? 'Generating…' : 'Generate Dims And Metrics'}
              </button>
            </div>
          </div>

          {p.generated && (
            <div className="mb-4 rounded-xl bg-white p-5 shadow-sm">
              <SectionHeader>Dims And Metrics</SectionHeader>
              <DimsMetricsTable
                xAxis={p.dims.xAxis} timeColumn={p.dims.timeColumn} dateFormat={p.dims.dateFormat}
                axisOptions={p.dims.axisOptions} timeOptions={p.dims.timeOptions} dateFormatOptions={p.dims.dateFormatOptions}
                onAxisFieldChange={p.onAxisFieldChange} columns={p.columns} onColumnChange={p.onColumnChange}
              />
            </div>
          )}

          {(p.mode === 'edit' || (p.runs && p.runs.length > 0)) && (
            <div className="mb-4 rounded-xl bg-white p-5 shadow-sm">
              <BackpopHistory runs={p.runs || []} onCancel={p.onCancelRun} />
            </div>
          )}

          {p.saveError && <div className="mb-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-600">{p.saveError}</div>}
          {p.saveOk && <div className="mb-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-[12px] text-emerald-700">{p.saveOk}</div>}
          <div className="mb-6 flex items-center gap-2.5">
            {p.onDelete && (
              <button disabled={p.saving} onClick={() => setDeleteOpen(true)} className="rounded-md border border-rose-200 bg-white px-4 py-2.5 text-[13px] font-medium text-rose-600 hover:bg-rose-50 disabled:opacity-50">Delete chart</button>
            )}
            <div className="ml-auto flex items-center gap-2.5">
              <button disabled={p.saving} onClick={p.onSaveDraft} className="rounded-md border border-slate-200 bg-white px-4 py-2.5 text-[13px] font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50">Save draft</button>
              <button disabled={p.saving || !p.generated} title={!p.generated ? 'Generate dims & metrics first' : ''} onClick={() => setBackpopOpen(true)} className="flex items-center gap-2 rounded-md bg-emerald-600 px-5 py-2.5 text-[13px] font-semibold text-white shadow-sm transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50">
                Save chart and backpopulate
              </button>
            </div>
          </div>
        </div>
      </div>

      <BackpopulateModal open={backpopOpen} defaultStart={p.backpopDefaults.start} defaultEnd={p.backpopDefaults.end} onClose={() => setBackpopOpen(false)} onConfirm={(r) => { setBackpopOpen(false); p.onSaveBackpopulate(r) }} />
      <DeleteConfirm open={deleteOpen} name={p.chartTitleLabel} onClose={() => setDeleteOpen(false)} onConfirm={() => { setDeleteOpen(false); p.onDelete && p.onDelete() }} />
      {p.toast && (
        <div className="pointer-events-none fixed bottom-5 left-1/2 z-[70] -translate-x-1/2 rounded-lg bg-slate-800 px-4 py-2 text-[13px] font-medium text-white shadow-lg">{p.toast}</div>
      )}
    </div>
  )
}
