import { useEffect, useState } from 'react'
import { Ic } from '../../components/primitives'
import type { UIMetric } from '../../components/types'

/* ----------------------------------------------------- MetricOrderModal */
export function MetricOrderModal({ open, metrics, onClose, onSave }: {
  open: boolean; metrics: UIMetric[]; onClose: () => void; onSave: (orderedIds: string[]) => void
}) {
  const [order, setOrder] = useState<UIMetric[]>(metrics)
  const [drag, setDrag] = useState<number | null>(null)
  useEffect(() => { if (open) setOrder(metrics) }, [open, metrics])
  if (!open) return null
  const move = (from: number, to: number) => {
    if (to < 0 || to >= order.length) return
    setOrder((o) => { const a = [...o]; const [x] = a.splice(from, 1); a.splice(to, 0, x); return a })
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onMouseDown={onClose}>
      <div className="absolute inset-0 bg-slate-900/40" />
      <div className="relative z-10 w-[380px] max-w-[92vw] overflow-hidden rounded-xl bg-white shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Metrics</div>
            <h2 className="text-[16px] font-semibold text-slate-800">Reorder metrics</h2>
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"><Ic.close /></button>
        </div>
        <p className="px-5 pt-3 text-[12px] text-slate-400">Drag, or use the arrows. This sets the order metrics load in by default.</p>
        <div className="max-h-[50vh] overflow-y-auto px-3 py-2">
          {order.map((m, i) => (
            <div key={m.id} draggable
              onDragStart={() => setDrag(i)}
              onDragEnter={() => { if (drag != null && drag !== i) { move(drag, i); setDrag(i) } }}
              onDragEnd={() => setDrag(null)}
              onDragOver={(e) => e.preventDefault()}
              className={'group flex items-center gap-2.5 rounded-md px-2 py-2 ' + (drag === i ? 'bg-sky-50 ring-1 ring-sky-200' : 'hover:bg-slate-50')}>
              <span className="cursor-grab text-slate-300 group-hover:text-slate-400"><Ic.grip /></span>
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: m.color }} />
              <span className="flex-1 truncate text-[13px] text-slate-700">{m.name}</span>
              <span className="w-5 text-right text-[11px] text-slate-300">{i + 1}</span>
              <div className="flex flex-col leading-none">
                <button onClick={() => move(i, i - 1)} disabled={i === 0} className="text-slate-400 hover:text-sky-600 disabled:opacity-30"><Ic.caret className="rotate-180" /></button>
                <button onClick={() => move(i, i + 1)} disabled={i === order.length - 1} className="text-slate-400 hover:text-sky-600 disabled:opacity-30"><Ic.caret /></button>
              </div>
            </div>
          ))}
          {order.length === 0 && <div className="px-2 py-6 text-center text-[13px] text-slate-400">No metrics to order.</div>}
        </div>
        <div className="flex items-center justify-end gap-2.5 border-t border-slate-100 bg-slate-50 px-5 py-3.5">
          <button onClick={onClose} className="rounded-md border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-600 hover:bg-slate-100">Cancel</button>
          <button onClick={() => onSave(order.map((m) => m.id))} className="rounded-md bg-sky-500 px-4 py-2 text-[13px] font-semibold text-white shadow-sm hover:bg-sky-600">Save order</button>
        </div>
      </div>
    </div>
  )
}
