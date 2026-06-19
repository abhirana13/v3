import { useEffect, useRef, useState } from 'react'
import { Ic } from '../../components/primitives'

/* --------------------------------------------------------------- ChartPicker */
/* Searchable chart selector — matches by chart number or name. */
export function ChartPicker({ charts, currentId, onSelect }: {
  charts: { id: number; name: string; number?: number | null; certified?: boolean }[]
  currentId: number | string
  onSelect: (id: number) => void
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  const cur = charts.find((c) => String(c.id) === String(currentId))
  const needle = q.trim().toLowerCase()
  const filtered = needle
    ? charts.filter((c) => c.name.toLowerCase().includes(needle) || String(c.number ?? '').includes(needle))
    : charts
  return (
    <div className="relative ml-2" ref={ref}>
      <button onClick={() => { setOpen((o) => !o); setQ('') }} className="flex max-w-[340px] items-center gap-1.5 rounded-md border border-slate-600 bg-slate-700/70 px-2.5 py-1.5 text-[12px] text-slate-200 hover:bg-slate-700">
        <Ic.search className="shrink-0 text-slate-400" />
        <span className="truncate"><span className="font-mono text-slate-400">{cur ? (cur.number ?? cur.id) : '—'}</span> · {cur ? cur.name : 'Select chart'}</span>
        <Ic.caret className="shrink-0 text-slate-400" />
      </button>
      {open && (
        <div className="absolute left-0 top-[calc(100%+4px)] z-50 w-[360px] overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl">
          <div className="border-b border-slate-100 p-2">
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by number or name…" className="w-full rounded-md border border-slate-200 px-2.5 py-1.5 text-[13px] text-slate-700 outline-none focus:border-sky-400 focus:ring-2 focus:ring-sky-100" />
          </div>
          <div className="max-h-72 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <div className="px-3 py-3 text-[12px] text-slate-400">No charts match “{q}”.</div>
            ) : filtered.map((c) => (
              <button key={c.id} onClick={() => { onSelect(c.id); setOpen(false) }} className={'flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-slate-50 ' + (String(c.id) === String(currentId) ? 'bg-sky-50' : '')}>
                <span className="w-12 shrink-0 font-mono text-[12px] text-slate-400">{c.number ?? c.id}</span>
                <span className="flex-1 truncate text-slate-700">{c.name}</span>
                {c.certified && <span className="shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-600">✓</span>}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
