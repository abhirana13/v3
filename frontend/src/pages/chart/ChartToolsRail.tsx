import { Ic } from '../../components/primitives'

/* circular-button tools rail (Icon Rail design) */
const RailIc = {
  table: (q: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...q}><rect x="3" y="4.5" width="18" height="15" rx="2" /><path d="M3 9.5h18M3 14.5h18M9 9.5V19" /></svg>),
  refresh: (q: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...q}><path d="M20 11a8 8 0 1 0-.5 3.5" /><path d="M20 4v6h-6" /></svg>),
  expand: (q: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...q}><path d="M8 3H4v4M16 3h4v4M8 21H4v-4M16 21h4v-4" /></svg>),
  dots: (q: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" {...q}><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></svg>),
  eye: (q: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...q}><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></svg>),
  eyeOff: (q: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...q}><path d="M3 3l18 18M10.6 5.1A9.6 9.6 0 0 1 12 5c6.5 0 10 7 10 7a16.8 16.8 0 0 1-3.2 4M6.5 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.5 9.5 0 0 0 4-.9" /><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" /></svg>),
}
function RailSpinner() {
  return (<svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6"><path d="M12 3a9 9 0 1 0 9 9" strokeLinecap="round" /></svg>)
}
export function ChartToolsRail({ tableOpen, pct, ma, fullscreen, backpopBusy, linesAllHidden, linesCount, onTable, onPct, onMa, onBackpop, onFullscreen, onToggleAllLines }: {
  tableOpen: boolean; pct: boolean; ma: boolean; fullscreen: boolean; backpopBusy?: boolean
  linesAllHidden: boolean; linesCount: number
  onTable: () => void; onPct: () => void; onMa: () => void; onBackpop: () => void; onFullscreen: () => void
  onToggleAllLines: () => void
}) {
  const cls = (active: boolean, disabled: boolean) =>
    'flex h-8 w-8 items-center justify-center rounded-full text-[12px] font-semibold transition-colors ' +
    (disabled ? 'cursor-not-allowed bg-slate-100 text-slate-300' : active ? 'bg-sky-500 text-white' : 'bg-sky-100 text-sky-600 hover:bg-sky-200')
  return (
    <div className="flex w-12 shrink-0 flex-col items-center gap-2 border-l border-slate-200 bg-slate-50 py-3">
      <button title="Table view — data on the selected cuts" onClick={onTable} className={cls(tableOpen, false)}><RailIc.table /></button>
      <button title="Chart view (coming soon)" disabled className={cls(false, true)}><Ic.line /></button>
      <button title="Percentage mode — each cut as a share of 100%" onClick={onPct} className={cls(pct, false)}>%</button>
      <button title="Moving average — convert the trend line(s) to a moving average" onClick={onMa} className={cls(ma, false)}>M</button>
      <button title={linesCount === 0 ? 'No lines to toggle' : linesAllHidden ? 'Show all lines' : 'Hide all lines'} onClick={onToggleAllLines} disabled={linesCount === 0} className={cls(linesAllHidden, linesCount === 0)}>{linesAllHidden ? <RailIc.eyeOff /> : <RailIc.eye />}</button>
      <button title="Annotations (coming soon)" disabled className={cls(false, true)}>A</button>
      <button title="Backpopulation — run this chart's default window now" onClick={onBackpop} disabled={backpopBusy} className={cls(false, !!backpopBusy)}>{backpopBusy ? <RailSpinner /> : <RailIc.refresh />}</button>
      <button title="Fullscreen" onClick={onFullscreen} className={cls(fullscreen, false)}><RailIc.expand /></button>
      <button title="More (coming soon)" disabled className={cls(false, true)}><RailIc.dots /></button>
    </div>
  )
}
