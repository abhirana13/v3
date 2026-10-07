import { useEffect, useRef, useState } from 'react'
import { Ic } from '../../components/primitives'
import { COMPARE_UNITS, MAX_OFFSET, compareDashCss, compareLabel, compareOpacity, isComparing } from './compare'
import type { CompareCfg, CompareUnit } from './compare'

/* Period-over-period control.
 *
 * Closed, it is one button like every other toolbar control. The period list only appears once
 * you are actually comparing — until then there is nothing to choose between, and a permanently
 * visible row of W-1..W-4 would be four dead controls on a chart that isn't comparing.
 *
 * Turning it on picks W-1 rather than opening with nothing selected: "compare" with no period
 * chosen renders identically to "off", which reads as a broken button.
 */
export function ComparePicker({ value, onChange, disabled, disabledReason }: {
  value: CompareCfg
  onChange: (c: CompareCfg) => void
  disabled?: boolean
  disabledReason?: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  useEffect(() => { if (disabled) setOpen(false) }, [disabled])

  const on = isComparing(value)
  const sorted = [...value.offsets].sort((a, b) => a - b)
  const summary = on ? sorted.map((n) => compareLabel(value.unit, n)).join(', ') : 'Compare'

  const toggleOffset = (n: number) => {
    const next = value.offsets.includes(n) ? value.offsets.filter((o) => o !== n) : [...value.offsets, n]
    onChange({ ...value, offsets: next })
  }
  const setUnit = (unit: CompareUnit) => {
    // Keep the chosen periods when switching unit — someone comparing W-1 and W-2 who
    // switches to Month almost always means M-1 and M-2, not "start again".
    onChange({ unit, offsets: value.offsets.length ? value.offsets : [1] })
  }

  return (
    <div className="relative" ref={ref}>
      <button
        title={disabled ? disabledReason : 'Overlay the same series from earlier periods'}
        disabled={disabled}
        onClick={() => {
          if (disabled) return
          if (!on && !open) onChange({ ...value, offsets: [1] })
          setOpen((o) => !o)
        }}
        className={
          'flex items-center gap-1.5 rounded-md border px-2.5 py-[7px] text-[13px] font-medium transition-colors ' +
          (disabled
            ? 'cursor-not-allowed border-slate-200 bg-white text-slate-300'
            : on
              ? 'border-sky-300 bg-sky-50 text-sky-700 hover:border-sky-400'
              : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300')
        }
      >
        <Ic.compare />
        {summary}
        <Ic.caret className={disabled ? 'text-slate-300' : on ? 'text-sky-400' : 'text-slate-400'} />
      </button>

      {open && !disabled && (
        <div className="absolute right-0 top-[calc(100%+4px)] z-30 w-[268px] overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Compare with</span>
            {on && (
              <button
                onClick={() => { onChange({ ...value, offsets: [] }); setOpen(false) }}
                className="text-[11.5px] font-semibold text-slate-400 hover:text-rose-600"
              >
                Turn off
              </button>
            )}
          </div>

          <div className="px-3 pt-2.5">
            <div className="grid grid-cols-4 overflow-hidden rounded-md border border-slate-200">
              {COMPARE_UNITS.map((u) => (
                <button
                  key={u.unit}
                  onClick={() => setUnit(u.unit)}
                  className={
                    'py-1.5 text-[12px] font-semibold transition-colors ' +
                    (value.unit === u.unit ? 'bg-sky-600 text-white' : 'bg-white text-slate-500 hover:bg-slate-50')
                  }
                >
                  {u.label}
                </button>
              ))}
            </div>
          </div>

          {/* Only shown once comparing — see the component note. */}
          {on && (
            <div className="px-3 pb-1 pt-3">
              <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Periods back</div>
              <div className="space-y-1">
                {Array.from({ length: MAX_OFFSET }, (_, i) => i + 1).map((n) => {
                  const checked = value.offsets.includes(n)
                  return (
                    <label key={n} className="flex cursor-pointer items-center gap-2.5 rounded-md px-1 py-1 hover:bg-slate-50">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleOffset(n)}
                        className="h-4 w-4 shrink-0 rounded border-slate-300 text-sky-600 focus:ring-sky-400"
                      />
                      {/* the swatch draws the exact dash pattern the overlay will use, so the
                          line you find on the plot is identifiable from this list alone */}
                      <svg width="26" height="8" viewBox="0 0 26 8" className="shrink-0 text-sky-600" aria-hidden>
                        <line x1="0" y1="4" x2="26" y2="4" stroke="currentColor" strokeWidth="2"
                          strokeLinecap="round" strokeDasharray={compareDashCss(n)} opacity={compareOpacity(n)} />
                      </svg>
                      <span className="text-[13px] font-medium text-slate-700">{compareLabel(value.unit, n)}</span>
                      <span className="ml-auto text-[11.5px] text-slate-400">
                        {n} {value.unit}{n > 1 ? 's' : ''} ago
                      </span>
                    </label>
                  )
                })}
              </div>
            </div>
          )}

          <p className="border-t border-slate-100 px-3 py-2 text-[11.5px] leading-snug text-slate-400">
            Each period is drawn in its series' own colour, told apart by its dash pattern.
          </p>
        </div>
      )}
    </div>
  )
}
