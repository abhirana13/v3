import { useEffect, useState } from 'react'
import { DateRangeCalendar } from '../../components/DateRangePicker'
import type { DateDraft } from '../../components/DateRangePicker'
import type { CacheCompat } from '../../api/types'

export interface BackpopRequestDraft { start: string; end: string; force: boolean; keepCache: boolean }

/* --------------------------------------------------------- BackpopulateModal */
export function BackpopulateModal({ open, defaultStart, defaultEnd, queryChanged, onCheckCompat, onClose, onConfirm }: {
  open: boolean
  defaultStart: string
  defaultEnd: string
  /** the saved-or-drafted query differs from the one this chart's cache was built with */
  queryChanged: boolean
  /** ask the backend whether the draft query still fits the cache (LIMIT 0 round trip) */
  onCheckCompat?: () => Promise<CacheCompat | null>
  onClose: () => void
  onConfirm: (r: BackpopRequestDraft) => void
}) {
  const [draft, setDraft] = useState<DateDraft>({ start: defaultStart, end: defaultEnd, valid: false, dayCount: null })
  const [force, setForce] = useState(false)
  const [keepCache, setKeepCache] = useState(false)
  const [compat, setCompat] = useState<CacheCompat | null>(null)
  const [checking, setChecking] = useState(false)

  /* Establish for ourselves whether the edit was structural, rather than asking the person
     who made it to vouch for it. Adding a game to an IN list cannot change the output
     columns, and a LIMIT 0 round trip settles that in about a second — so the dialog should
     know the answer before it says anything alarming. */
  useEffect(() => {
    if (!open || !queryChanged || !onCheckCompat) { setCompat(null); return }
    let alive = true
    setChecking(true)
    onCheckCompat()
      .then((c) => { if (alive) setCompat(c) })
      .catch(() => { if (alive) setCompat(null) })
      .finally(() => { if (alive) setChecking(false) })
    return () => { alive = false }
  }, [open, queryChanged, onCheckCompat])

  // Verified structurally safe => start from the non-destructive choice. What is left for the
  // editor to answer is the one thing the backend cannot see: whether the edit changed what
  // the EXISTING rows mean. That question is on the checkbox label.
  useEffect(() => {
    if (compat?.checked && compat.columns_match && compat.has_cache && compat.keep_supported !== false) setKeepCache(true)
  }, [compat])

  if (!open) return null

  const verified = compat?.checked === true && compat.has_cache
  const keepBlocked = compat?.keep_supported === false
  const columnsChanged = verified && !compat!.columns_match
  const columnsSame = verified && compat!.columns_match
  // Amber is for "you are about to lose something". A confirmation that everything is fine
  // must not wear the same colour, or the colour stops meaning anything.
  const losingHistory = queryChanged && !keepCache && compat?.has_cache !== false

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onMouseDown={onClose}>
      <div className="absolute inset-0 bg-slate-900/40" />
      <div className="relative z-10 w-[760px] max-w-[95vw] overflow-hidden rounded-xl bg-white shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="border-b border-slate-100 px-5 py-4">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Backpopulate</div>
          <h2 className="text-[16px] font-semibold text-slate-800">Select date window</h2>
          <p className="mt-0.5 text-[12px] text-slate-400">The chart will be recomputed for every day in this range.</p>
        </div>

        {queryChanged && (
          <div className="mx-5 mt-4">
            {checking && (
              <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2.5 text-[12px] text-slate-500">
                Checking whether this edit changes the chart's columns…
              </div>
            )}

            {!checking && !keepBlocked && columnsSame && keepCache && (
              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-[12px] leading-snug text-emerald-900">
                <span className="font-semibold">Checked — this edit doesn't change the chart's columns.</span>{' '}
                It only changes which rows come back, so the existing data will be kept: just the
                days selected above are re-read, and everything outside them is left as it is.
                <div className="mt-1 text-emerald-800/80">
                  If the edit also changed what existing rows <span className="font-semibold">mean</span> —
                  a different filter, a corrected aggregation — untick below so the chart is rebuilt instead.
                </div>
              </div>
            )}

            {!checking && !keepBlocked && columnsSame && !keepCache && (
              <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5 text-[12px] leading-snug text-amber-900">
                <span className="font-semibold">This will discard the whole cache</span> and rebuild only the
                range selected above — any history outside it is lost. The columns are unchanged, so
                ticking “Keep existing data” below is safe unless your edit changed what existing rows mean.
              </div>
            )}

            {!checking && keepBlocked && (
              <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5 text-[12px] leading-snug text-amber-900">
                <span className="font-semibold">This chart can't keep its cache.</span>{' '}
                {compat!.keep_blocked_reason}. Set a time column on the chart, or accept that
                backpopulating rebuilds it over the selected range.
              </div>
            )}

            {!checking && !keepBlocked && columnsChanged && (
              <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5 text-[12px] leading-snug text-amber-900">
                <span className="font-semibold">This edit changes the chart's columns</span>
                {compat!.added.length > 0 && <> (added <span className="font-mono">{compat!.added.join(', ')}</span>)</>}
                {compat!.removed.length > 0 && <> (removed <span className="font-mono">{compat!.removed.join(', ')}</span>)</>}
                . The existing cache cannot hold the new shape, so it will be rebuilt over the range
                selected above and history outside it is lost. Widen the range to keep more.
              </div>
            )}

            {!checking && !keepBlocked && !verified && (
              <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5 text-[12px] leading-snug text-amber-900">
                <span className="font-semibold">The query has changed since this chart's data was cached.</span>{' '}
                {compat?.checked === false
                  ? <>The column check couldn't run ({compat.message}), so decide below.</>
                  : <>{losingHistory
                      ? <>Backpopulating will discard the whole cache and rebuild only the range selected above.</>
                      : <>Existing data will be kept — only the selected days are re-read.</>}</>}
              </div>
            )}
          </div>
        )}

        <DateRangeCalendar start={defaultStart} end={defaultEnd} onDraft={setDraft} />
        <div className="border-t border-slate-100 px-5 pt-3">
          <div className={'rounded-md px-3 py-2 text-[12px] ' + (draft.valid ? 'bg-sky-50 text-sky-700' : 'bg-rose-50 text-rose-600')}>
            {draft.valid ? <span><span className="font-semibold">{draft.dayCount}</span> day{draft.dayCount === 1 ? '' : 's'} will be backpopulated.</span> : 'Pick a start and end date (end on or after start).'}
          </div>
        </div>

        <div className="space-y-2.5 px-5 pt-3">
          <label className={'flex items-start gap-2.5 ' + (columnsChanged || keepBlocked ? 'cursor-not-allowed opacity-50' : 'cursor-pointer')}>
            <input type="checkbox" disabled={columnsChanged || keepBlocked} checked={keepCache && !columnsChanged && !keepBlocked} onChange={(e) => setKeepCache(e.target.checked)} className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 text-sky-600 focus:ring-sky-400 disabled:opacity-50" />
            <span className="text-[12px] leading-snug text-slate-600">
              <span className="font-semibold text-slate-700">Keep existing data</span> — re-read only the days
              selected above and leave every other day alone. Correct when the edit <span className="font-semibold">adds rows</span> (a
              new game in the IN list). Untick it if the edit changed what an <span className="font-semibold">existing row means</span> —
              a different filter, a corrected metric — because those days would keep their old values.
              {columnsChanged && <span className="block text-amber-700">Unavailable: the output columns changed.</span>}
              {keepBlocked && <span className="block text-amber-700">Unavailable: this chart has no time column.</span>}
            </span>
          </label>

          <label className="flex cursor-pointer items-start gap-2.5">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 text-sky-600 focus:ring-sky-400" />
            <span className="text-[12px] leading-snug text-slate-600">
              <span className="font-semibold text-slate-700">Force refresh</span> — re-pull and overwrite every day in this range, even days already cached. Use for restated/corrected data. (Without this, already-cached older days are skipped.)
            </span>
          </label>
          {force && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-700">
              Every day in the selected range will be re-queried from Redshift and overwritten; a day that now returns no rows will be cleared.
              {keepCache && !columnsChanged && !keepBlocked && <> With “Keep existing data” also ticked, a day that reads empty is <span className="font-semibold">still cleared</span> — force wins on that point.</>}
            </div>
          )}
        </div>

        <div className="mt-3 flex items-center justify-end gap-2.5 border-t border-slate-100 bg-slate-50 px-5 py-3.5">
          <button onClick={onClose} className="rounded-md border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-600 hover:bg-slate-100">Cancel</button>
          <button disabled={!draft.valid} onClick={() => onConfirm({ start: draft.start, end: draft.end, force, keepCache: keepCache && !columnsChanged && !keepBlocked })} className={'rounded-md px-4 py-2 text-[13px] font-semibold text-white shadow-sm ' + (draft.valid ? 'bg-sky-500 hover:bg-sky-600' : 'cursor-not-allowed bg-slate-300')}>Start backpopulation</button>
        </div>
      </div>
    </div>
  )
}
