import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { ChartOverview } from '../../api/types'

const fmtDate = (iso: string | null) => {
  if (!iso) return '—'
  const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso)
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}
const fmtWhen = (iso: string | null) => {
  if (!iso) return ''
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function StatusPill({ status, running }: { status: string | null; running: boolean }) {
  if (running) return <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700">running…</span>
  const tone: Record<string, string> = {
    success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    failed: 'border-rose-200 bg-rose-50 text-rose-700',
  }
  const cls = (status && tone[status]) || 'border-slate-200 bg-slate-50 text-slate-500'
  return <span className={'rounded-full border px-2 py-0.5 text-[11px] font-semibold capitalize ' + cls}>{status || 'never'}</span>
}

const cfgUrl = (target: number | 'new') => `${window.location.pathname}?config=${target}`

export function HomePage({ onOpenChart }: { onOpenChart: (id: number) => void }) {
  const [rows, setRows] = useState<ChartOverview[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')

  useEffect(() => {
    api.chartsOverview().then(setRows).catch((e: any) => setError(String(e.message || e)))
  }, [])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const list = rows || []
    if (!needle) return list
    return list.filter((r) => r.name.toLowerCase().includes(needle) || String(r.chart_number ?? '').includes(needle))
  }, [rows, q])

  return (
    <div className="flex h-full flex-col bg-slate-100 font-sans text-slate-800">
      <header className="flex h-12 shrink-0 items-center gap-4 bg-slate-800 px-4 text-slate-200">
        <div className="flex h-7 w-7 items-center justify-center rounded bg-sky-500 text-xs font-bold text-white">FG</div>
        <span className="text-[13px] font-semibold text-white">Analytics</span>
        <span className="text-[12px] text-slate-400">· All charts</span>
        <button onClick={() => window.open(cfgUrl('new'), '_blank')} className="ml-auto rounded-md bg-sky-500 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-sky-600">+ New chart</button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1100px] px-6 py-6">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h1 className="text-[20px] font-bold text-slate-800">Charts</h1>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by number or name…" className="w-72 rounded-md border border-slate-200 bg-white px-3 py-2 text-[13px] text-slate-700 outline-none focus:border-sky-400 focus:ring-2 focus:ring-sky-100" />
          </div>

          {error && <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[13px] text-rose-600">Failed to load charts: {error}</div>}
          {!rows && !error && <div className="py-16 text-center text-[14px] text-slate-400">Loading…</div>}
          {rows && rows.length === 0 && (
            <div className="rounded-xl bg-white p-10 text-center shadow-sm">
              <p className="mb-3 text-[14px] text-slate-500">No charts yet.</p>
              <button onClick={() => window.open(cfgUrl('new'), '_blank')} className="rounded-md bg-sky-500 px-4 py-2 text-[13px] font-semibold text-white hover:bg-sky-600">Create your first chart</button>
            </div>
          )}

          {rows && rows.length > 0 && (
            <div className="overflow-hidden rounded-xl bg-white shadow-sm">
              <table className="w-full border-collapse text-[13px]">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                    <th className="px-4 py-2.5">#</th>
                    <th className="px-4 py-2.5">Chart</th>
                    <th className="px-4 py-2.5">Data through</th>
                    <th className="px-4 py-2.5">Last backpop</th>
                    <th className="px-4 py-2.5 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r) => (
                    <tr key={r.id} onClick={() => onOpenChart(r.id)} className="cursor-pointer border-b border-slate-50 last:border-0 hover:bg-sky-50/40">
                      <td className="px-4 py-3 font-mono text-[12px] text-slate-400">{r.chart_number ?? r.id}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-slate-700">{r.name}</span>
                          {r.certified && <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-600">✓ Certified</span>}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-slate-600">{r.latest_data_date ? fmtDate(r.latest_data_date) : <span className="text-slate-300">no data</span>}</td>
                      <td className="px-4 py-3">
                        <span className="flex items-center gap-2"><StatusPill status={r.last_backpop_status} running={r.running} /><span className="text-[12px] text-slate-400">{fmtWhen(r.last_backpop_at)}</span></span>
                      </td>
                      <td className="px-4 py-3 text-right" onClick={(e) => e.stopPropagation()}>
                        <button onClick={() => onOpenChart(r.id)} className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[12px] font-medium text-slate-600 hover:bg-slate-50">Open</button>
                        <button onClick={() => window.open(cfgUrl(r.id), '_blank')} className="ml-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[12px] font-medium text-slate-600 hover:bg-slate-50">Configure</button>
                      </td>
                    </tr>
                  ))}
                  {filtered.length === 0 && (
                    <tr><td colSpan={5} className="px-4 py-8 text-center text-[13px] text-slate-400">No charts match “{q}”.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
