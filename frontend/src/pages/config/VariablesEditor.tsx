/* -------------------------------------------------- Query Variables editor */
export interface VarRow { name: string; value: string }
export function VariablesEditor({ rows, onChange }: { rows: VarRow[]; onChange: (rows: VarRow[]) => void }) {
  const set = (i: number, patch: Partial<VarRow>) => onChange(rows.map((r, j) => j === i ? { ...r, ...patch } : r))
  const add = () => onChange([...rows, { name: '', value: '' }])
  const remove = (i: number) => onChange(rows.filter((_, j) => j !== i))
  return (
    <div>
      <p className="mb-2 text-[12px] text-slate-400">Values for non-date <span className="font-mono">{'{TOKENS}'}</span> in the query. Comma-separated values become a SQL list (<span className="font-mono">'a', 'b'</span>); a single value is inserted as-is.</p>
      <div className="space-y-2">
        {rows.map((r, i) => (
          <div key={i} className="flex items-center gap-2">
            <input value={r.name} onChange={(e) => set(i, { name: e.target.value.toUpperCase() })} placeholder="TOKEN_NAME" className="w-56 rounded-md border border-slate-200 px-3 py-2 font-mono text-[12px] text-slate-700 outline-none focus:border-sky-400 focus:ring-2 focus:ring-sky-100" />
            <input value={r.value} onChange={(e) => set(i, { value: e.target.value })} placeholder="value or a, b, c" className="flex-1 rounded-md border border-slate-200 px-3 py-2 text-[13px] text-slate-700 outline-none focus:border-sky-400 focus:ring-2 focus:ring-sky-100" />
            <button onClick={() => remove(i)} className="rounded-md px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">×</button>
          </div>
        ))}
      </div>
      <button onClick={add} className="mt-2 rounded-md border border-dashed border-slate-300 px-3 py-1.5 text-[12px] font-medium text-slate-500 hover:border-sky-400 hover:text-sky-600">+ Add variable</button>
    </div>
  )
}
