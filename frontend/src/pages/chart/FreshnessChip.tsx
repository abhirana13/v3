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
    <span className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-[12px] text-slate-400" title={failed ? (freshness.last_run?.error_message || 'Last backpopulation failed') : 'Latest cached data date'}>
      {/* "Data through" and the word "failed" are prose, not information — the date and the
          red chip carry it. They are hidden below 2xl so the chart TITLE gets those ~110px
          back; at 1280 the header had no room left and the title was ellipsed to nothing. */}
      {through
        ? <><span className="hidden 2xl:inline">Data through </span><span className="font-medium text-slate-600">{through}</span></>
        : <span>No cached data</span>}
      {failed && (
        <span className="shrink-0 rounded bg-rose-50 px-1.5 py-0.5 text-[11px] font-semibold text-rose-600">
          backpop<span className="hidden 2xl:inline"> failed</span>
        </span>
      )}
    </span>
  )
}
