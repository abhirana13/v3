import type { Freshness } from '../../api/types'

const fmtDate = (iso: string | null) => {
  if (!iso) return null
  const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso)
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

/* Compact "data through <date>" indicator + a flag when the last backpop failed. */
export function FreshnessChip({ freshness }: { freshness: Freshness }) {
  if (freshness.running) {
    return <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">Backpopulating…</span>
  }
  const through = fmtDate(freshness.latest_data_date)
  const failed = freshness.last_run?.status === 'failed'
  if (!through && !freshness.last_run) return null
  return (
    <span className="flex items-center gap-1.5 text-[12px] text-slate-400" title={failed ? (freshness.last_run?.error_message || 'Last backpopulation failed') : 'Latest cached data date'}>
      {through
        ? <>Data through <span className="font-medium text-slate-600">{through}</span></>
        : <span>No cached data</span>}
      {failed && <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[11px] font-semibold text-rose-600">backpop failed</span>}
    </span>
  )
}
