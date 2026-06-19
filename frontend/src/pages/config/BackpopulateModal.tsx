import { useState } from 'react'
import { DateRangeCalendar } from '../../components/DateRangePicker'
import type { DateDraft } from '../../components/DateRangePicker'

/* --------------------------------------------------------- BackpopulateModal */
export function BackpopulateModal({ open, defaultStart, defaultEnd, onClose, onConfirm }: { open: boolean; defaultStart: string; defaultEnd: string; onClose: () => void; onConfirm: (r: { start: string; end: string }) => void }) {
  const [draft, setDraft] = useState<DateDraft>({ start: defaultStart, end: defaultEnd, valid: false, dayCount: null })
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onMouseDown={onClose}>
      <div className="absolute inset-0 bg-slate-900/40" />
      <div className="relative z-10 w-[760px] max-w-[95vw] overflow-hidden rounded-xl bg-white shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="border-b border-slate-100 px-5 py-4">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Backpopulate</div>
          <h2 className="text-[16px] font-semibold text-slate-800">Select date window</h2>
          <p className="mt-0.5 text-[12px] text-slate-400">The chart will be recomputed for every day in this range.</p>
        </div>
        <DateRangeCalendar start={defaultStart} end={defaultEnd} onDraft={setDraft} />
        <div className="border-t border-slate-100 px-5 pt-3">
          <div className={'rounded-md px-3 py-2 text-[12px] ' + (draft.valid ? 'bg-sky-50 text-sky-700' : 'bg-rose-50 text-rose-600')}>
            {draft.valid ? <span><span className="font-semibold">{draft.dayCount}</span> day{draft.dayCount === 1 ? '' : 's'} will be backpopulated.</span> : 'Pick a start and end date (end on or after start).'}
          </div>
        </div>
        <div className="mt-3 flex items-center justify-end gap-2.5 border-t border-slate-100 bg-slate-50 px-5 py-3.5">
          <button onClick={onClose} className="rounded-md border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-600 hover:bg-slate-100">Cancel</button>
          <button disabled={!draft.valid} onClick={() => onConfirm({ start: draft.start, end: draft.end })} className={'rounded-md px-4 py-2 text-[13px] font-semibold text-white shadow-sm ' + (draft.valid ? 'bg-sky-500 hover:bg-sky-600' : 'cursor-not-allowed bg-slate-300')}>Start backpopulation</button>
        </div>
      </div>
    </div>
  )
}
