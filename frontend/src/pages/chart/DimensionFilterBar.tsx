import { useEffect, useRef, useState } from 'react'
import { Checkbox, Ic } from '../../components/primitives'
import type { UIDimension } from '../../components/types'

/* ----------------------------------------------------------- DimensionChip */
function DimensionChip({ dimension, onToggleValue, onSetAll, onToggleSplit }: {
  dimension: UIDimension
  onToggleValue: (k: string, v: string) => void
  onSetAll: (k: string, on: boolean) => void
  onToggleSplit: (k: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  const total = dimension.values.length
  const sel = dimension.selected.length
  const allSelected = total > 0 && sel === total
  const split = dimension.split
  // checked = aggregated ("All"); unchecking splits the chart by this dimension
  const filterLabel = allSelected ? 'All' : String(sel)
  const badge = split ? (allSelected ? 'Split' : `Split · ${sel}`) : filterLabel

  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen((o) => !o)}
        className={'flex items-center gap-2 rounded-md border bg-white py-[5px] pl-2 pr-2 text-[13px] transition-colors ' + (open ? 'border-sky-400 ring-2 ring-sky-100' : split ? 'border-violet-400 ring-2 ring-violet-100' : 'border-slate-200 hover:border-slate-300')}>
        <Checkbox checked={!split} onChange={() => onToggleSplit(dimension.key)} title="Uncheck to split the chart by this dimension" />
        <span className="font-medium text-slate-700">{dimension.label}</span>
        <span className={'rounded px-1.5 py-[1px] text-[11px] font-semibold ' + (split ? 'bg-violet-100 text-violet-700' : allSelected ? 'bg-slate-100 text-slate-500' : 'bg-sky-100 text-sky-700')}>{badge}</span>
        <Ic.caret className="text-slate-400" />
      </button>
      {open && (
        <div className="absolute left-0 top-[calc(100%+6px)] z-30 w-56 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Filter values</span>
            <div className="flex gap-2 text-[11px] font-semibold text-sky-600">
              <button className="hover:underline" onClick={() => onSetAll(dimension.key, true)}>All</button>
              <button className="hover:underline" onClick={() => onSetAll(dimension.key, false)}>None</button>
            </div>
          </div>
          <div className="max-h-60 overflow-y-auto py-1">
            {dimension.values.length === 0 && <div className="px-3 py-2 text-[12px] text-slate-400">No values</div>}
            {dimension.values.map((v) => (
              // whole row toggles; the Checkbox stops propagation so clicking it doesn't double-fire
              <div key={v} onClick={() => onToggleValue(dimension.key, v)}
                className="flex cursor-pointer items-center gap-2.5 px-3 py-1.5 text-[13px] text-slate-700 hover:bg-slate-50">
                <Checkbox checked={dimension.selected.includes(v)} onChange={() => onToggleValue(dimension.key, v)} />
                <span className="truncate">{v}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------- DimensionFilterBar */
export function DimensionFilterBar({ dimensions, allToggle, onToggleValue, onSetAll, onToggleSplit, onAllToggle, onAddDimension }: {
  dimensions: UIDimension[]
  allToggle: boolean
  onToggleValue: (k: string, v: string) => void
  onSetAll: (k: string, on: boolean) => void
  onToggleSplit: (k: string) => void
  onAllToggle: (on: boolean) => void
  onAddDimension: () => void
}) {
  return (
    <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-4 py-2.5">
      <label className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-white py-[5px] px-2 text-[13px] text-slate-600" title="Aggregate all dimensions (clear every split)">
        <Checkbox checked={allToggle} onChange={onAllToggle} />
        <span className="font-medium">All</span>
      </label>
      <div className="flex flex-wrap items-center gap-2">
        {dimensions.map((d) => (
          <DimensionChip key={d.key} dimension={d} onToggleValue={onToggleValue} onSetAll={onSetAll} onToggleSplit={onToggleSplit} />
        ))}
      </div>
      <button type="button" onClick={onAddDimension} title="Add dimension"
        className="flex h-7 w-7 items-center justify-center rounded-md border border-dashed border-slate-300 text-slate-400 transition-colors hover:border-sky-400 hover:text-sky-500">
        <Ic.plus />
      </button>
      <div className="ml-auto flex items-center gap-1.5">
        <button title="Filters" className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 text-slate-500 hover:bg-slate-50"><Ic.funnel /></button>
        <button title="Settings" className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 text-slate-500 hover:bg-slate-50"><Ic.sliders /></button>
      </div>
    </div>
  )
}
