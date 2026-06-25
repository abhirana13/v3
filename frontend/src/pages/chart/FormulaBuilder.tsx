import { useEffect, useRef, useState } from 'react'

/* Chip/token formula builder. Replaces the free-text formula box: the user
   assembles a formula from metric chips, operators, parentheses and numbers.
   It still emits the same formula string the backend already understands
   (e.g. "( levels_won + bonus ) / dau * 100"), so serving is unchanged. */

export type Tok =
  | { k: 'metric'; v: string }
  | { k: 'num'; v: string }
  | { k: 'op'; v: '+' | '-' | '*' | '/' | '%' }
  | { k: 'lp' }
  | { k: 'rp' }

const OP_DISPLAY: Record<string, string> = { '+': '+', '-': '−', '*': '×', '/': '÷', '%': '%' }

/* parse an existing formula string back into editable tokens */
export function parseFormula(s: string): Tok[] {
  const toks: Tok[] = []
  const re = /[A-Za-z_][A-Za-z0-9_]*|\d+\.?\d*|[()+\-*/%]/g
  let m: RegExpExecArray | null
  while ((m = re.exec(s || '')) !== null) {
    const t = m[0]
    if (/^[A-Za-z_]/.test(t)) toks.push({ k: 'metric', v: t })
    else if (/^\d/.test(t)) toks.push({ k: 'num', v: t })
    else if (t === '(') toks.push({ k: 'lp' })
    else if (t === ')') toks.push({ k: 'rp' })
    else toks.push({ k: 'op', v: t as '+' | '-' | '*' | '/' | '%' })
  }
  return toks
}

export function serializeFormula(toks: Tok[]): string {
  return toks
    .map((t) => (t.k === 'metric' || t.k === 'num' ? t.v : t.k === 'op' ? t.v : t.k === 'lp' ? '(' : ')'))
    .join(' ')
}

/* well-formedness: balanced parens, alternating operand/operator. Empty = base metric (valid). */
export function isValidFormula(toks: Tok[]): boolean {
  if (toks.length === 0) return true
  let expectOperand = true
  let depth = 0
  for (const t of toks) {
    if (t.k === 'metric' || t.k === 'num') {
      if (!expectOperand) return false
      expectOperand = false
    } else if (t.k === 'op') {
      if (expectOperand) return false
      expectOperand = true
    } else if (t.k === 'lp') {
      if (!expectOperand) return false
      depth++
    } else {
      if (expectOperand || depth === 0) return false
      depth--
    }
  }
  return depth === 0 && !expectOperand
}

export function FormulaBuilder({ initial, metrics, onChange, onValidityChange }: {
  initial: string
  metrics: string[]
  onChange: (formula: string) => void
  onValidityChange: (valid: boolean) => void
}) {
  const [toks, setToks] = useState<Tok[]>(() => parseFormula(initial))
  const [num, setNum] = useState('')
  const mounted = useRef(false)

  useEffect(() => {
    onValidityChange(isValidFormula(toks))
    if (mounted.current) onChange(serializeFormula(toks)) // don't clobber `initial` on first render
    mounted.current = true
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toks])

  const add = (t: Tok) => setToks((p) => [...p, t])
  const valid = isValidFormula(toks)
  const opBtn = 'px-2.5 py-1.5 text-[14px] text-slate-600 hover:bg-slate-50'

  return (
    <div>
      <div className={'flex min-h-[44px] flex-wrap items-center gap-1.5 rounded-md border px-2.5 py-2 transition-colors ' + (toks.length > 0 && !valid ? 'border-rose-300 ring-2 ring-rose-100' : 'border-slate-200')}>
        {toks.length === 0 && <span className="text-[12px] text-slate-300">Build a formula, or leave empty for a base metric</span>}
        {toks.map((t, i) => {
          if (t.k === 'op') return <span key={i} className="px-0.5 text-[15px] text-slate-500">{OP_DISPLAY[t.v]}</span>
          if (t.k === 'lp' || t.k === 'rp') return <span key={i} className="text-[17px] leading-none text-slate-400">{t.k === 'lp' ? '(' : ')'}</span>
          const metric = t.k === 'metric'
          return (
            <span key={i} className={'flex items-center gap-1 rounded-md px-2 py-0.5 font-mono text-[12px] ' + (metric ? 'bg-sky-100 text-sky-700' : 'bg-slate-100 text-slate-600')}>
              {t.v}
              <span onClick={() => setToks((p) => p.filter((_, j) => j !== i))} className="cursor-pointer opacity-50 hover:opacity-100" title="Remove">×</span>
            </span>
          )
        })}
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <select value="" onChange={(e) => { if (e.target.value) add({ k: 'metric', v: e.target.value }) }}
          className="rounded-md border border-slate-200 px-2 py-1.5 text-[13px] text-slate-700 outline-none focus:border-sky-400 focus:ring-2 focus:ring-sky-100">
          <option value="">+ Insert metric…</option>
          {metrics.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <div className="inline-flex overflow-hidden rounded-md border border-slate-200">
          {(['+', '-', '*', '/'] as const).map((op, i) => (
            <button key={op} type="button" onClick={() => add({ k: 'op', v: op })} className={opBtn + (i ? ' border-l border-slate-200' : '')}>{OP_DISPLAY[op]}</button>
          ))}
        </div>
        <div className="inline-flex overflow-hidden rounded-md border border-slate-200">
          <button type="button" onClick={() => add({ k: 'lp' })} className={opBtn}>(</button>
          <button type="button" onClick={() => add({ k: 'rp' })} className={opBtn + ' border-l border-slate-200'}>)</button>
        </div>
        <div className="inline-flex items-center gap-1">
          <input value={num} onChange={(e) => setNum(e.target.value.replace(/[^\d.]/g, ''))} placeholder="#" inputMode="decimal"
            className="w-14 rounded-md border border-slate-200 px-2 py-1.5 text-[13px] text-slate-700 outline-none focus:border-sky-400 focus:ring-2 focus:ring-sky-100" />
          <button type="button" disabled={!num} onClick={() => { add({ k: 'num', v: num }); setNum('') }}
            className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[13px] text-slate-600 hover:bg-slate-50 disabled:opacity-40">add</button>
        </div>
        <button type="button" onClick={() => setToks((p) => p.slice(0, -1))} disabled={toks.length === 0} title="Remove last"
          className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[14px] text-slate-500 hover:bg-slate-50 disabled:opacity-40">⌫</button>
      </div>

      <div className="mt-2 flex items-center gap-2 text-[11px]">
        {toks.length === 0 ? (
          <span className="text-slate-400">No formula — saves as a base (column-backed) metric.</span>
        ) : (
          <>
            <span className="text-slate-400">Preview</span>
            <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[12px] text-slate-600">{serializeFormula(toks)}</span>
            {valid
              ? <span className="font-semibold text-emerald-600">✓ valid</span>
              : <span className="font-semibold text-rose-500">incomplete</span>}
          </>
        )}
      </div>
    </div>
  )
}
