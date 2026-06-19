import { useEffect, useRef, useState } from 'react'
import { Ic } from '../../components/primitives'

/* Header export button: PNG image (from the ECharts canvas) or CSV (current series). */
export function ExportMenu({ onPng, onCsv }: { onPng: () => void; onCsv: () => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((o) => !o)} title="Export chart" className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-[7px] text-[13px] font-medium text-slate-600 hover:border-slate-300">
        <Ic.download /> Export <Ic.caret className="text-slate-400" />
      </button>
      {open && (
        <div className="absolute right-0 top-[calc(100%+4px)] z-30 w-40 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-xl">
          <button onClick={() => { setOpen(false); onPng() }} className="flex w-full items-center px-3 py-1.5 text-left text-[13px] text-slate-600 hover:bg-slate-50">PNG image</button>
          <button onClick={() => { setOpen(false); onCsv() }} className="flex w-full items-center px-3 py-1.5 text-left text-[13px] text-slate-600 hover:bg-slate-50">CSV data</button>
        </div>
      )}
    </div>
  )
}
