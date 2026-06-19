import type { SVGProps } from 'react'

/* icons local to the config screen */
type IconFn = (p: SVGProps<SVGSVGElement>) => JSX.Element
export const CIc: Record<string, IconFn> = {
  caret: (p) => (<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M4 6l4 4 4-4" /></svg>),
  info: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>),
  spark: (p) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8" /></svg>),
  grip: (p) => (<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" {...p}><circle cx="9" cy="6" r="1.4" /><circle cx="15" cy="6" r="1.4" /><circle cx="9" cy="12" r="1.4" /><circle cx="15" cy="12" r="1.4" /><circle cx="9" cy="18" r="1.4" /><circle cx="15" cy="18" r="1.4" /></svg>),
  back: (p) => (<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M15 18l-6-6 6-6" /></svg>),
}

const REQ = <span className="text-rose-400">∗ </span>

export function ConfigField({ label, required, hint, children, className = '' }: { label: string; required?: boolean; hint?: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <label className="mb-1.5 flex items-center gap-1 text-[12px] font-medium text-slate-500">
        {required && REQ}{label}
        {hint && <span title={hint} className="text-slate-300"><CIc.info /></span>}
      </label>
      {children}
    </div>
  )
}

export function ConfigInput({ value, onChange, placeholder, disabled, type = 'text' }: { value: string; onChange?: (v: string) => void; placeholder?: string; disabled?: boolean; type?: string }) {
  return (
    <input
      value={value ?? ''} placeholder={placeholder} disabled={disabled} type={type}
      onChange={(e) => onChange && onChange(e.target.value)}
      className={'w-full rounded-md border px-3 py-2 text-[13px] outline-none transition-colors ' + (disabled ? 'border-slate-200 bg-slate-50 text-slate-400 placeholder:text-slate-300' : 'border-slate-200 bg-white text-slate-700 placeholder:text-slate-300 focus:border-sky-400 focus:ring-2 focus:ring-sky-100')}
    />
  )
}

export function ConfigSelect({ value, onChange, options, disabled, center }: { value: string; onChange?: (v: string) => void; options: string[]; disabled?: boolean; center?: boolean }) {
  return (
    <div className="relative">
      <select value={value} disabled={disabled} onChange={(e) => onChange && onChange(e.target.value)}
        className={'w-full appearance-none rounded-md border px-3 py-2 text-[13px] outline-none transition-colors ' + (center ? 'text-center ' : '') + (disabled ? 'border-slate-200 bg-slate-50 text-slate-400' : 'border-slate-200 bg-white text-slate-700 focus:border-sky-400 focus:ring-2 focus:ring-sky-100')}>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
      <CIc.caret className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
    </div>
  )
}

export function SectionHeader({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-3 text-[13px] font-semibold text-slate-700">{children}</h2>
}
