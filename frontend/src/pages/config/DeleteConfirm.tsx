/* --------------------------------------------------------- DeleteConfirm */
export function DeleteConfirm({ open, name, onClose, onConfirm }: { open: boolean; name: string; onClose: () => void; onConfirm: () => void }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center" onMouseDown={onClose}>
      <div className="absolute inset-0 bg-slate-900/40" />
      <div className="relative z-10 w-[440px] max-w-[95vw] rounded-xl bg-white p-5 shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <h2 className="text-[16px] font-semibold text-slate-800">Delete this chart?</h2>
        <p className="mt-1.5 text-[13px] leading-relaxed text-slate-500">
          <span className="font-semibold text-slate-700">{name}</span> and its dimensions, metrics, and backpopulation history will be permanently removed. This can’t be undone.
        </p>
        <div className="mt-4 flex justify-end gap-2.5">
          <button onClick={onClose} className="rounded-md border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-600 hover:bg-slate-50">Cancel</button>
          <button onClick={onConfirm} className="rounded-md bg-rose-600 px-4 py-2 text-[13px] font-semibold text-white hover:bg-rose-700">Delete chart</button>
        </div>
      </div>
    </div>
  )
}
