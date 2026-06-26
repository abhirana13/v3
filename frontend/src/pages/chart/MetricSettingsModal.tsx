import { useEffect, useState } from 'react'
import { Checkbox, Ic } from '../../components/primitives'
import { FormulaBuilder } from './FormulaBuilder'

/* --------------------------------------------------- MetricSettingsModal */
export interface MetricDraft {
  id: string; name: string; formula?: string; independentFields?: string[]
  axis?: 'primary' | 'secondary'; decimals?: number; unit?: string; isNew?: boolean
}
export function MetricSettingsModal({ open, metric, dimensionNames, metricNames, error, onClose, onApply, onSave, onDelete }: {
  open: boolean; metric: MetricDraft | null; dimensionNames: string[]; metricNames: string[]; error?: string | null
  onClose: () => void; onApply: (d: MetricDraft) => void; onSave: (d: MetricDraft) => void
  onDelete?: (d: MetricDraft) => void
}) {
  const [draft, setDraft] = useState<MetricDraft>(metric || ({} as MetricDraft))
  const [indOpen, setIndOpen] = useState(false)
  const [formulaValid, setFormulaValid] = useState(true)
  const [confirmDel, setConfirmDel] = useState(false)
  useEffect(() => { setDraft(metric || ({} as MetricDraft)); setConfirmDel(false) }, [metric, open])
  if (!open || !metric) return null

  const set = (k: keyof MetricDraft, v: any) => setDraft((d) => ({ ...d, [k]: v }))
  const toggleInd = (name: string) => setDraft((d) => {
    const cur = d.independentFields || []
    return { ...d, independentFields: cur.includes(name) ? cur.filter((x) => x !== name) : [...cur, name] }
  })
  const units = ['None', '%', '$', 'k', 'ms']
  const field = 'w-full rounded-md border border-slate-200 px-3 py-2 text-[13px] text-slate-700 outline-none focus:border-sky-400 focus:ring-2 focus:ring-sky-100'
  const label = 'mb-1.5 block text-[12px] font-semibold text-slate-500'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onMouseDown={onClose}>
      <div className="absolute inset-0 bg-slate-900/40" />
      <div className="relative z-10 w-[460px] max-w-[92vw] overflow-hidden rounded-xl bg-white shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{metric.isNew ? 'New metric' : 'Metric settings'}</div>
            <h2 className="text-[16px] font-semibold text-slate-800">{draft.name || metric.name || 'New metric'}</h2>
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"><Ic.close /></button>
        </div>
        <div className="max-h-[70vh] space-y-4 overflow-y-auto px-5 py-5">
          <div>
            <label className={label}>Metric name</label>
            <input className={field} value={draft.name || ''} onChange={(e) => set('name', e.target.value)} />
          </div>
          <div className="relative">
            <label className={label}>Independent fields</label>
            <button type="button" onClick={() => setIndOpen((o) => !o)} className={field + ' flex min-h-[38px] items-center gap-1.5 text-left'}>
              {(draft.independentFields || []).length === 0 ? (
                <span className="text-slate-400">Select dimensions this metric is independent of…</span>
              ) : (
                <span className="flex flex-wrap gap-1.5">
                  {(draft.independentFields || []).map((n) => (
                    <span key={n} className="flex items-center gap-1 rounded bg-sky-100 px-1.5 py-0.5 text-[12px] font-medium text-sky-700">
                      {n}<span onClick={(e) => { e.stopPropagation(); toggleInd(n) }} className="cursor-pointer text-sky-400 hover:text-sky-700">×</span>
                    </span>
                  ))}
                </span>
              )}
              <Ic.caret className="ml-auto text-slate-400" />
            </button>
            {indOpen && (
              <div className="absolute z-20 mt-1 max-h-48 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white py-1 shadow-xl">
                {dimensionNames.map((n) => (
                  <label key={n} className="flex cursor-pointer items-center gap-2.5 px-3 py-1.5 text-[13px] text-slate-700 hover:bg-slate-50">
                    <Checkbox checked={(draft.independentFields || []).includes(n)} onChange={() => toggleInd(n)} />{n}
                  </label>
                ))}
              </div>
            )}
            <p className="mt-1 text-[11px] text-slate-400">Dimensions selected here are not split across this metric (e.g. dau is independent of source).</p>
          </div>
          <div>
            <label className={label}>Formula</label>
            <FormulaBuilder
              key={metric.id}
              initial={metric.formula || ''}
              metrics={metricNames}
              onChange={(f) => set('formula', f)}
              onValidityChange={setFormulaValid}
            />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className={label}>Y-axis</label>
              <select className={field} value={draft.axis || 'primary'} onChange={(e) => set('axis', e.target.value)}>
                <option value="primary">Primary</option>
                <option value="secondary">Secondary</option>
              </select>
            </div>
            <div>
              <label className={label}>Decimal places</label>
              <input type="number" min={0} max={6} className={field} value={draft.decimals ?? 0} onChange={(e) => set('decimals', Number(e.target.value))} />
            </div>
            <div>
              <label className={label}>Unit</label>
              <select className={field} value={draft.unit || 'None'} onChange={(e) => set('unit', e.target.value)}>
                {units.map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </div>
          </div>
          {error && <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-600">{error}</div>}
        </div>
        <div className="flex items-center gap-2.5 border-t border-slate-100 bg-slate-50 px-5 py-3.5">
          {/* delete (existing metrics only) — two-click confirm to avoid accidents */}
          {!metric.isNew && onDelete && (confirmDel ? (
            <span className="flex items-center gap-2 text-[12px]">
              <span className="text-slate-500">Delete this metric?</span>
              <button onClick={() => onDelete(draft)} className="rounded-md bg-rose-600 px-2.5 py-1.5 text-[12px] font-semibold text-white hover:bg-rose-700">Yes, delete</button>
              <button onClick={() => setConfirmDel(false)} className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[12px] text-slate-600 hover:bg-slate-50">Cancel</button>
            </span>
          ) : (
            <button onClick={() => setConfirmDel(true)} className="rounded-md border border-rose-200 bg-white px-3 py-2 text-[13px] font-medium text-rose-600 hover:bg-rose-50">Delete metric</button>
          ))}
          <div className="ml-auto flex items-center gap-2.5">
            {!formulaValid && <span className="text-[12px] font-medium text-rose-500">Formula is incomplete</span>}
            {!metric.isNew && <button onClick={() => onApply(draft)} disabled={!formulaValid} className="rounded-md border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40">Apply changes</button>}
            <button onClick={() => onSave(draft)} disabled={!formulaValid} className="rounded-md bg-sky-500 px-4 py-2 text-[13px] font-semibold text-white shadow-sm hover:bg-sky-600 disabled:cursor-not-allowed disabled:opacity-50">Save changes</button>
          </div>
        </div>
      </div>
    </div>
  )
}
