import { ConfigField, ConfigSelect, CIc } from './fields'
import { IndependentPicker } from './IndependentPicker'

export interface ConfigColumn {
  name: string
  classification: 'Dimension' | 'Metric'
  dataType: string
  independentOf: string[]
  included: boolean
}

/* --------------------------------------------------------- DimsMetricsTable */
export function DimsMetricsTable({ xAxis, timeColumn, dateFormat, axisOptions, timeOptions, dateFormatOptions, onAxisFieldChange, columns, onColumnChange }: {
  xAxis: string; timeColumn: string; dateFormat: string
  axisOptions: string[]; timeOptions: string[]; dateFormatOptions: string[]
  onAxisFieldChange: (patch: Record<string, string>) => void
  columns: ConfigColumn[]; onColumnChange: (name: string, patch: Partial<ConfigColumn>) => void
}) {
  const dimNames = columns.filter((d) => d.classification === 'Dimension').map((d) => d.name)
  return (
    <div>
      <div className="mb-4 grid grid-cols-3 gap-x-6">
        <ConfigField label="XAxis" required><ConfigSelect value={xAxis} onChange={(v) => onAxisFieldChange({ xAxis: v })} options={axisOptions} /></ConfigField>
        <ConfigField label="Time Column" required><ConfigSelect value={timeColumn} onChange={(v) => onAxisFieldChange({ timeColumn: v })} options={timeOptions} disabled /></ConfigField>
        <ConfigField label="DateFormat" required><ConfigSelect value={dateFormat} onChange={(v) => onAxisFieldChange({ dateFormat: v })} options={dateFormatOptions} /></ConfigField>
      </div>
      <div className="overflow-hidden rounded-lg border border-slate-200">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="bg-slate-50 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              <th className="w-8 px-3 py-2"></th>
              <th className="px-3 py-2">Column</th>
              <th className="px-3 py-2">Classification</th>
              <th className="px-3 py-2">Data type</th>
              <th className="px-3 py-2">Independent of</th>
              <th className="px-3 py-2 text-center">Include</th>
            </tr>
          </thead>
          <tbody>
            {columns.map((c) => (
              <tr key={c.name} className="border-t border-slate-100 hover:bg-slate-50/60">
                <td className="px-3 py-2 text-slate-300"><CIc.grip /></td>
                <td className="px-3 py-2 font-mono font-medium text-slate-700">{c.name}</td>
                <td className="px-3 py-2">
                  <div className="inline-flex overflow-hidden rounded-md border border-slate-200">
                    {(['Dimension', 'Metric'] as const).map((opt) => (
                      <button key={opt} onClick={() => onColumnChange(c.name, { classification: opt })}
                        className={'px-2.5 py-1 text-[12px] font-medium transition-colors ' + (c.classification === opt ? (opt === 'Dimension' ? 'bg-violet-500 text-white' : 'bg-sky-500 text-white') : 'bg-white text-slate-500 hover:bg-slate-50')}>{opt}</button>
                    ))}
                  </div>
                </td>
                <td className="px-3 py-2"><span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[11px] text-slate-500">{c.dataType}</span></td>
                <td className="px-3 py-2">
                  {c.classification === 'Metric'
                    ? <IndependentPicker dimNames={dimNames} selected={c.independentOf || []} onChange={(arr) => onColumnChange(c.name, { independentOf: arr })} />
                    : <span className="text-slate-300">—</span>}
                </td>
                <td className="px-3 py-2 text-center">
                  <button role="switch" aria-checked={c.included} onClick={() => onColumnChange(c.name, { included: !c.included })}
                    className={'relative inline-block h-5 w-9 rounded-full transition-colors ' + (c.included ? 'bg-sky-500' : 'bg-slate-200')}>
                    <span className={'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ' + (c.included ? 'left-[18px]' : 'left-0.5')} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
