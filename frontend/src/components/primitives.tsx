import { useEffect, useRef, useState } from 'react'
import type { SVGProps } from 'react'

/* Tiny inline stroke icons — no external icon dependency (ported from design). */
type IconFn = (p: SVGProps<SVGSVGElement>) => JSX.Element
export const Ic: Record<string, IconFn> = {
  caret: (p) => (<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M4 6l4 4 4-4" /></svg>),
  gear: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>),
  search: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>),
  plus: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M12 5v14M5 12h14" /></svg>),
  close: (p) => (<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M18 6 6 18M6 6l12 12" /></svg>),
  hamburger: (p) => (<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" {...p}><path d="M4 7h16M4 12h16M4 17h16" /></svg>),
  line: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M3 17l5-6 4 3 6-8" /><path d="M3 21h18" /></svg>),
  cal: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="3" y="4.5" width="18" height="16" rx="2" /><path d="M3 9h18M8 2.5v4M16 2.5v4" /></svg>),
  funnel: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M3 5h18l-7 8v6l-4 2v-8z" /></svg>),
  sliders: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h7M15 18h5" /><circle cx="16" cy="6" r="2" fill="white" /><circle cx="8" cy="12" r="2" fill="white" /><circle cx="13" cy="18" r="2" fill="white" /></svg>),
  sort: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M7 4v16M7 4L4 7M7 4l3 3M17 20V4M17 20l-3-3M17 20l3-3" /></svg>),
  link: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M10 13a5 5 0 0 0 7.07 0l2-2a5 5 0 0 0-7.07-7.07l-1.5 1.5" /><path d="M14 11a5 5 0 0 0-7.07 0l-2 2a5 5 0 0 0 7.07 7.07l1.5-1.5" /></svg>),
  grip: (p) => (<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" {...p}><circle cx="9" cy="6" r="1.4" /><circle cx="15" cy="6" r="1.4" /><circle cx="9" cy="12" r="1.4" /><circle cx="15" cy="12" r="1.4" /><circle cx="9" cy="18" r="1.4" /><circle cx="15" cy="18" r="1.4" /></svg>),
  home: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M3 11l9-8 9 8" /><path d="M5 10v10h5v-6h4v6h5V10" /></svg>),
  download: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M12 3v12M7 11l5 4 5-4M5 21h14" /></svg>),
}

/* Square checkbox with indeterminate state. */
export function Checkbox({ checked, indeterminate, onChange, id, title }: {
  checked?: boolean; indeterminate?: boolean; onChange?: (v: boolean) => void; id?: string; title?: string
}) {
  return (
    <span
      id={id}
      title={title}
      role="checkbox"
      aria-checked={indeterminate ? 'mixed' : !!checked}
      onClick={(e) => { e.stopPropagation(); onChange && onChange(!checked) }}
      className={
        'inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-[4px] border transition-colors cursor-pointer ' +
        (checked || indeterminate ? 'border-sky-500 bg-sky-500 text-white' : 'border-slate-300 bg-white hover:border-slate-400')
      }
    >
      {checked && (<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M3.5 8.5l3 3 6-7" /></svg>)}
      {indeterminate && !checked && <span className="h-[2px] w-2 rounded bg-white" />}
    </span>
  )
}

/* Generic title-bar dropdown. */
export function Dropdown({ value, onChange, options, icon, title }: {
  value: string; onChange: (v: string) => void; options: string[]; icon?: JSX.Element; title?: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  return (
    <div className="relative" ref={ref}>
      <button title={title} onClick={() => setOpen((o) => !o)} className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-[7px] text-[13px] font-medium text-slate-600 hover:border-slate-300">
        {icon}{value}<Ic.caret className="text-slate-400" />
      </button>
      {open && (
        <div className="absolute right-0 top-[calc(100%+4px)] z-30 w-40 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-xl">
          {options.map((o) => (
            <button key={o} onClick={() => { onChange(o); setOpen(false) }} className={'flex w-full items-center px-3 py-1.5 text-left text-[13px] hover:bg-slate-50 ' + (o === value ? 'font-semibold text-sky-600' : 'text-slate-600')}>{o}</button>
          ))}
        </div>
      )}
    </div>
  )
}
