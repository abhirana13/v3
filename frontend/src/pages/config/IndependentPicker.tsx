import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { CIc } from './fields'

/* --------------------------------------------------------- IndependentPicker */
/* per-metric multiselect: which dimensions the metric is independent of.
   The panel is positioned `fixed` (anchored to the trigger) so it escapes the
   table wrapper's `overflow-hidden` clip; its height is capped to the available
   viewport space so the list always scrolls inside itself. */
export function IndependentPicker({ dimNames, selected, onChange }: { dimNames: string[]; selected: string[]; onChange: (arr: string[]) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const [pos, setPos] = useState<{ left: number; width: number; top?: number; bottom?: number; maxHeight: number } | null>(null)

  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  // Anchor the fixed panel to the trigger; reposition while open as the page scrolls/resizes.
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const btn = btnRef.current
      if (!btn) return
      const r = btn.getBoundingClientRect()
      const margin = 8
      const spaceBelow = window.innerHeight - r.bottom - margin
      const spaceAbove = r.top - margin
      const openUp = spaceBelow < 180 && spaceAbove > spaceBelow
      const maxHeight = Math.min(260, Math.max(120, openUp ? spaceAbove : spaceBelow))
      setPos(openUp
        ? { left: r.left, width: r.width, bottom: window.innerHeight - r.top + 4, maxHeight }
        : { left: r.left, width: r.width, top: r.bottom + 4, maxHeight })
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => { window.removeEventListener('scroll', place, true); window.removeEventListener('resize', place) }
  }, [open])

  const toggle = (n: string) => onChange(selected.includes(n) ? selected.filter((x) => x !== n) : [...selected, n])
  return (
    <div className="relative" ref={ref}>
      <button ref={btnRef} onClick={() => setOpen((o) => !o)} className={'flex min-h-[34px] w-full min-w-[200px] items-center gap-1 rounded-md border bg-white px-2 py-1 text-left transition-colors ' + (open ? 'border-violet-400 ring-2 ring-violet-100' : 'border-slate-200 hover:border-slate-300')}>
        {selected.length === 0 ? <span className="text-[12px] text-slate-300">Not independent</span> : (
          <span className="flex flex-wrap gap-1">
            {selected.map((n) => (
              <span key={n} className="flex items-center gap-1 rounded bg-violet-100 px-1.5 py-0.5 text-[11px] font-medium text-violet-700">{n}<span onClick={(e) => { e.stopPropagation(); toggle(n) }} className="cursor-pointer opacity-60 hover:opacity-100">×</span></span>
            ))}
          </span>
        )}
        <CIc.caret className="ml-auto shrink-0 text-slate-400" />
      </button>
      {open && pos && (
        <div
          className="fixed z-50 overflow-y-auto rounded-lg border border-slate-200 bg-white py-1 shadow-xl"
          style={{ left: pos.left, width: Math.max(pos.width, 224), top: pos.top, bottom: pos.bottom, maxHeight: pos.maxHeight }}
        >
          <div className="sticky top-0 bg-white px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Independent of</div>
          {dimNames.length === 0 ? <div className="px-3 py-2 text-[12px] text-slate-400">No dimensions to choose.</div> : dimNames.map((n) => (
            <label key={n} className="flex cursor-pointer items-center gap-2.5 px-3 py-1.5 text-[13px] text-slate-700 hover:bg-slate-50">
              <input type="checkbox" checked={selected.includes(n)} onChange={() => toggle(n)} className="h-4 w-4 accent-violet-500" />
              <span className="font-mono">{n}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}
