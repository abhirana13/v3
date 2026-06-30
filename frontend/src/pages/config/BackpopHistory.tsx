import { useState } from 'react'
import type { BackpopRun } from '../../api/types'
import { CIc } from './fields'

/* --------------------------------------------------------- BackpopHistory */
function fmtRunTime(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/* time the run took: completed - started, or "running…" while in flight */
function fmtDuration(started: string, completed: string | null, status: string): string {
  if (!completed) return status === 'running' ? 'running…' : status === 'queued' ? 'queued…' : '—'
  const ms = new Date(completed).getTime() - new Date(started).getTime()
  if (isNaN(ms) || ms < 0) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60), rs = s % 60
  if (m < 60) return rs ? `${m}m ${rs}s` : `${m}m`
  const h = Math.floor(m / 60), rm = m % 60
  return rm ? `${h}h ${rm}m` : `${h}h`
}

function RunStatusBadge({ status, error }: { status: string; error: string | null }) {
  const tone: Record<string, string> = {
    success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    failed: 'border-rose-200 bg-rose-50 text-rose-700',
    running: 'border-amber-200 bg-amber-50 text-amber-700',
    queued: 'border-sky-200 bg-sky-50 text-sky-700',
    cancelled: 'border-slate-300 bg-slate-100 text-slate-600',
  }
  return (
    <span title={error || undefined} className={'inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-semibold capitalize ' + (tone[status] || 'border-slate-200 bg-slate-50 text-slate-600')}>
      {status}
    </span>
  )
}

const RUNS_PER_PAGE = 5
export function BackpopHistory({ runs, onCancel }: { runs: BackpopRun[]; onCancel?: (runId: number) => void }) {
  const [open, setOpen] = useState(true)
  const [page, setPage] = useState(0)
  const pageCount = Math.max(1, Math.ceil(runs.length / RUNS_PER_PAGE))
  const cur = Math.min(page, pageCount - 1)
  const start = cur * RUNS_PER_PAGE
  const slice = runs.slice(start, start + RUNS_PER_PAGE)
  const latest = runs[0]
  const pageBtn = 'rounded border border-slate-200 px-2 py-1 text-[12px] font-medium text-slate-600 enabled:hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40'
  return (
    <div>
      {/* disclosure header */}
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 text-left">
        <CIc.caret className={'shrink-0 text-slate-400 transition-transform ' + (open ? '' : '-rotate-90')} />
        <span className="text-[13px] font-semibold text-slate-700">Backpopulation History</span>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-500">{runs.length}</span>
        {!open && latest && (
          <span className="ml-1 flex items-center gap-1.5 text-[12px] text-slate-400">latest <RunStatusBadge status={latest.status} error={latest.error_message} /> <span className="font-mono">{latest.from_date} → {latest.to_date}</span></span>
        )}
      </button>

      {open && (runs.length === 0 ? (
        <p className="mt-3 text-[12px] text-slate-400">No backpopulation runs yet.</p>
      ) : (
        <div className="mt-3">
          <div className="overflow-hidden rounded-lg border border-slate-200">
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="bg-slate-50 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                  <th className="px-3 py-2">Started</th>
                  <th className="px-3 py-2">Range</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2 text-right">Rows</th>
                  <th className="px-3 py-2 text-right">Batches</th>
                  <th className="px-3 py-2 text-right">Time</th>
                </tr>
              </thead>
              <tbody>
                {slice.map((r) => (
                  <tr key={r.id} className="border-t border-slate-100 hover:bg-slate-50/60">
                    <td className="whitespace-nowrap px-3 py-2 text-slate-600">{fmtRunTime(r.started_at)}</td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-[12px] text-slate-600">{r.from_date} → {r.to_date}</td>
                    <td className="px-3 py-2">
                      <span className="flex items-center gap-2">
                        <RunStatusBadge status={r.status} error={r.error_message} />
                        {(r.status === 'running' || r.status === 'queued') && onCancel && (
                          <button onClick={() => onCancel(r.id)} className="rounded border border-rose-200 bg-white px-1.5 py-0.5 text-[11px] font-medium text-rose-600 hover:bg-rose-50">Cancel</button>
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-700">{r.row_count.toLocaleString()}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{r.batches_completed}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{fmtDuration(r.started_at, r.completed_at, r.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {pageCount > 1 && (
            <div className="mt-2 flex items-center justify-between text-[12px] text-slate-500">
              <span>Showing {start + 1}–{start + slice.length} of {runs.length}</span>
              <div className="flex items-center gap-1.5">
                <button disabled={cur === 0} onClick={() => setPage(cur - 1)} className={pageBtn}>‹ Newer</button>
                <span className="px-1">Page {cur + 1} / {pageCount}</span>
                <button disabled={cur >= pageCount - 1} onClick={() => setPage(cur + 1)} className={pageBtn}>Older ›</button>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
