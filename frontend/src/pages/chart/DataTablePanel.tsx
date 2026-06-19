import { Ic } from '../../components/primitives'
import type { ChartRow, UISeries } from '../../components/types'

/* data table shown below the chart (Table view) */
export function DataTablePanel({ data, series, onClose }: { data: ChartRow[]; series: UISeries[]; onClose: () => void }) {
  const fmt = (v: number | string | null, s: UISeries) => {
    if (v == null || typeof v !== 'number') return '—'
    const u = s.unit && s.unit !== 'None' ? s.unit : ''
    const dp = s.decimals ?? 0
    return (u === '$' ? '$' : '') + v.toLocaleString(undefined, { maximumFractionDigits: dp, minimumFractionDigits: 0 }) + (u === '%' ? '%' : '')
  }
  return (
    <div className="mt-3 flex max-h-[42%] min-h-[150px] shrink-0 flex-col overflow-hidden rounded-lg border border-slate-200">
      <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-3 py-2">
        <span className="text-[12px] font-semibold text-slate-600">Data table · {series.length} cut{series.length === 1 ? '' : 's'} · {data.length} row{data.length === 1 ? '' : 's'}</span>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-600" title="Close table"><Ic.close /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead className="sticky top-0 z-10 bg-white shadow-[0_1px_0_#e2e8f0]">
            <tr>
              <th className="px-3 py-2 text-left font-semibold text-slate-500">Date</th>
              {series.map((s) => <th key={s.key} className="whitespace-nowrap px-3 py-2 text-right font-semibold text-slate-500">{s.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr key={i} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="whitespace-nowrap px-3 py-1.5 text-slate-500">{String(row.date)}</td>
                {series.map((s) => <td key={s.key} className="whitespace-nowrap px-3 py-1.5 text-right tabular-nums text-slate-700">{fmt(row[s.key], s)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
