import { useEffect, useMemo, useRef, useState } from 'react'

/* Date range picker ported from the Claude Design handoff (daterange.jsx):
   presets column + two text inputs + dual-month calendar with range highlighting.
   Exports:
     - DateRangeCalendar: the panel (no footer buttons), reports its draft upward.
     - DateRangePicker: trigger button + popover wrapping the calendar + Cancel/Apply.
   Both are dumb/controlled — value via props, changes via callbacks. */

export interface DateRange { start: string; end: string }
export interface DateDraft { start: string; end: string; valid: boolean; dayCount: number | null }

const Ic = {
  caret: (p: any) => (<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M4 6l4 4 4-4" /></svg>),
  cal: (p: any) => (<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="3" y="4.5" width="18" height="16" rx="2" /><path d="M3 9h18M8 2.5v4M16 2.5v4" /></svg>),
  prev: (p: any) => (<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M15 6l-6 6 6 6" /></svg>),
  next: (p: any) => (<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M9 6l6 6-6 6" /></svg>),
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const DOW = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']

const toISO = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const fromISO = (s: string): Date | null => {
  if (!s) return null
  const [y, m, d] = s.split('-').map(Number)
  if (!y || !m || !d) return null
  const dt = new Date(y, m - 1, d)
  return isNaN(dt.getTime()) ? null : dt
}
const sameDay = (a: Date | null, b: Date | null) => !!a && !!b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
const stripTime = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate())
const addMonths = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth() + n, 1)
const fmtPretty = (d: Date | null) => (d ? `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}` : '')

/* one month grid */
function MonthGrid({ month, rangeStart, rangeEnd, hover, onPick, onHover }: {
  month: Date; rangeStart: Date | null; rangeEnd: Date | null; hover: Date | null
  onPick: (d: Date) => void; onHover: (d: Date) => void
}) {
  const daysIn = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate()
  const startPad = new Date(month.getFullYear(), month.getMonth(), 1).getDay()
  const cells: (Date | null)[] = []
  for (let i = 0; i < startPad; i++) cells.push(null)
  for (let d = 1; d <= daysIn; d++) cells.push(new Date(month.getFullYear(), month.getMonth(), d))

  const previewEnd = rangeStart && !rangeEnd && hover ? hover : rangeEnd
  const inRange = (day: Date) => {
    if (!rangeStart || !previewEnd) return false
    const a = stripTime(rangeStart).getTime(), b = stripTime(previewEnd).getTime(), t = stripTime(day).getTime()
    return t > Math.min(a, b) && t < Math.max(a, b)
  }

  return (
    <div className="w-[244px]">
      <div className="mb-1.5 grid grid-cols-7">
        {DOW.map((d) => <div key={d} className="py-1 text-center text-[11px] font-semibold text-slate-400">{d}</div>)}
      </div>
      <div className="grid grid-cols-7 gap-y-0.5">
        {cells.map((day, i) => {
          if (!day) return <div key={i} />
          const isEdge = sameDay(day, rangeStart) || sameDay(day, previewEnd)
          const mid = inRange(day)
          return (
            <div key={i} className={'flex justify-center ' + (mid || (isEdge && rangeStart && previewEnd) ? 'bg-sky-50' : '')}>
              <button
                onClick={() => onPick(day)}
                onMouseEnter={() => onHover(day)}
                className={'flex h-8 w-8 items-center justify-center rounded-full text-[12.5px] transition-colors ' + (isEdge ? 'bg-sky-500 font-semibold text-white' : mid ? 'text-sky-700' : 'text-slate-600 hover:bg-slate-100')}
              >
                {day.getDate()}
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const PRESETS = [
  { l: 'Last 7 days', d: 7 }, { l: 'Last 30 days', d: 30 },
  { l: 'Last 90 days', d: 90 }, { l: 'Last 120 days', d: 120 }, { l: 'Last 365 days', d: 365 },
]

/* The calendar panel (presets + inputs + dual month). Seeds from start/end and
   reports the working draft via onDraft on every change. No footer buttons —
   the container (popover or modal) owns Apply/Confirm. */
export function DateRangeCalendar({ start, end, onDraft }: { start: string; end: string; onDraft: (d: DateDraft) => void }) {
  const [viewMonth, setViewMonth] = useState<Date>(() => fromISO(start) || new Date())
  const [draftStart, setDraftStart] = useState<Date | null>(() => fromISO(start))
  const [draftEnd, setDraftEnd] = useState<Date | null>(() => fromISO(end))
  const [hover, setHover] = useState<Date | null>(null)
  const [startText, setStartText] = useState(start)
  const [endText, setEndText] = useState(end)

  // re-seed when the incoming range changes (e.g. chart switch / modal reopen)
  useEffect(() => {
    setDraftStart(fromISO(start)); setDraftEnd(fromISO(end))
    setStartText(start); setEndText(end)
    setViewMonth(fromISO(start) || new Date())
  }, [start, end])

  const dayCount = useMemo(() => {
    if (!draftStart || !draftEnd) return null
    return Math.round((+stripTime(draftEnd) - +stripTime(draftStart)) / 86400000) + 1
  }, [draftStart, draftEnd])
  const valid = !!(draftStart && draftEnd && stripTime(draftStart) <= stripTime(draftEnd))

  // forward draft upward without depending on onDraft's identity (avoids loops)
  const onDraftRef = useRef(onDraft)
  onDraftRef.current = onDraft
  useEffect(() => {
    onDraftRef.current({ start: draftStart ? toISO(draftStart) : '', end: draftEnd ? toISO(draftEnd) : '', valid, dayCount })
  }, [draftStart, draftEnd, valid, dayCount])

  const pick = (day: Date) => {
    if (!draftStart || (draftStart && draftEnd)) {
      setDraftStart(day); setDraftEnd(null); setStartText(toISO(day)); setEndText('')
    } else if (stripTime(day) < stripTime(draftStart)) {
      setDraftEnd(draftStart); setDraftStart(day); setStartText(toISO(day)); setEndText(toISO(draftStart))
    } else {
      setDraftEnd(day); setEndText(toISO(day))
    }
  }
  const applyPreset = (days: number) => {
    const e = stripTime(new Date())
    const s = stripTime(new Date()); s.setDate(s.getDate() - (days - 1))
    setDraftStart(s); setDraftEnd(e); setStartText(toISO(s)); setEndText(toISO(e))
    setViewMonth(new Date(s.getFullYear(), s.getMonth(), 1))
  }
  const commitText = (which: 'start' | 'end', txt: string) => {
    const d = fromISO(txt)
    if (!d) return
    if (which === 'start') { setDraftStart(d); setViewMonth(new Date(d.getFullYear(), d.getMonth(), 1)) }
    else setDraftEnd(d)
  }

  const monthLabel = (m: Date) => `${MONTHS[m.getMonth()]} ${m.getFullYear()}`
  const field = 'w-full rounded-md border border-slate-200 px-2.5 py-1.5 text-[13px] text-slate-700 outline-none focus:border-sky-400 focus:ring-2 focus:ring-sky-100'

  return (
    <div className="flex">
      <div className="w-40 shrink-0 border-r border-slate-100 bg-slate-50/60 py-2">
        {PRESETS.map((p) => {
          const e = stripTime(new Date())
          const s = stripTime(new Date()); s.setDate(s.getDate() - (p.d - 1))
          const active = sameDay(draftStart, s) && sameDay(draftEnd, e)
          return (
            <button key={p.l} onClick={() => applyPreset(p.d)} className={'block w-full px-4 py-2 text-left text-[13px] transition-colors ' + (active ? 'bg-sky-100 font-semibold text-sky-700' : 'text-slate-600 hover:bg-slate-100')}>{p.l}</button>
          )
        })}
      </div>
      <div className="p-4">
        <div className="mb-3 flex items-center gap-2">
          <input value={startText} onChange={(e) => setStartText(e.target.value)} onBlur={() => commitText('start', startText)} placeholder="YYYY-MM-DD" className={field} />
          <span className="text-slate-300">→</span>
          <input value={endText} onChange={(e) => setEndText(e.target.value)} onBlur={() => commitText('end', endText)} placeholder="YYYY-MM-DD" className={field} />
        </div>
        <div className="mb-2 flex items-center justify-between">
          <button onClick={() => setViewMonth((m) => addMonths(m, -1))} className="rounded-md p-1 text-slate-500 hover:bg-slate-100"><Ic.prev /></button>
          <div className="flex flex-1 items-center justify-around px-2">
            <span className="text-[13px] font-semibold text-slate-700">{monthLabel(viewMonth)}</span>
            <span className="text-[13px] font-semibold text-slate-700">{monthLabel(addMonths(viewMonth, 1))}</span>
          </div>
          <button onClick={() => setViewMonth((m) => addMonths(m, 1))} className="rounded-md p-1 text-slate-500 hover:bg-slate-100"><Ic.next /></button>
        </div>
        <div className="flex gap-5" onMouseLeave={() => setHover(null)}>
          <MonthGrid month={viewMonth} rangeStart={draftStart} rangeEnd={draftEnd} hover={hover} onPick={pick} onHover={setHover} />
          <MonthGrid month={addMonths(viewMonth, 1)} rangeStart={draftStart} rangeEnd={draftEnd} hover={hover} onPick={pick} onHover={setHover} />
        </div>
      </div>
    </div>
  )
}

/* Trigger + popover. value/onChange in ISO; onChange fires only on Apply. */
export function DateRangePicker({ value, onChange, label, align = 'left', widthClass = 'w-[300px]' }: {
  value: DateRange; onChange: (r: DateRange) => void
  label?: string; align?: 'left' | 'right'; widthClass?: string
}) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<DateDraft>({ start: value.start, end: value.end, valid: false, dayCount: null })
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  const apply = () => { if (draft.valid) { onChange({ start: draft.start, end: draft.end }); setOpen(false) } }
  const s = fromISO(value.start), e = fromISO(value.end)

  return (
    <div className="relative inline-block" ref={ref}>
      {label && <div className="mb-1.5 text-[12px] font-medium text-slate-500">{label}</div>}
      <button
        onClick={() => setOpen((o) => !o)}
        className={'flex items-center gap-2 rounded-md border bg-white px-3 py-2 text-[13px] transition-colors ' + widthClass + ' ' + (open ? 'border-sky-400 ring-2 ring-sky-100' : 'border-slate-200 hover:border-slate-300')}
      >
        <Ic.cal className="shrink-0 text-slate-400" />
        <span className="text-slate-700">{s ? fmtPretty(s) : 'Start date'}</span>
        <span className="text-slate-300">→</span>
        <span className="text-slate-700">{e ? fmtPretty(e) : 'End date'}</span>
        <Ic.caret className="ml-auto shrink-0 text-slate-400" />
      </button>

      {open && (
        <div className={'absolute top-[calc(100%+8px)] z-40 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl ' + (align === 'right' ? 'right-0' : 'left-0')}>
          <DateRangeCalendar start={value.start} end={value.end} onDraft={setDraft} />
          <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3">
            <span className="text-[12px] text-slate-400">
              {draft.valid ? <><span className="font-semibold text-slate-600">{draft.dayCount}</span> day{draft.dayCount === 1 ? '' : 's'} selected</> : 'Pick a start and end date'}
            </span>
            <div className="flex gap-2">
              <button onClick={() => setOpen(false)} className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-600 hover:bg-slate-50">Cancel</button>
              <button disabled={!draft.valid} onClick={apply} className={'rounded-md px-4 py-1.5 text-[13px] font-semibold text-white ' + (draft.valid ? 'bg-sky-500 hover:bg-sky-600' : 'cursor-not-allowed bg-slate-300')}>Apply</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
