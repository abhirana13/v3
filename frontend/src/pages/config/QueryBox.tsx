import { useRef } from 'react'

/* ----------------------------------------------------------------- QueryBox */
const SQL_KEYWORDS = /^(SELECT|FROM|WHERE|AND|OR|AS|CASE|WHEN|THEN|ELSE|END|GROUP|BY|ORDER|HAVING|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|ON|IN|NOT|NULL|IS|LIMIT|UNION|ALL|DISTINCT|WITH|OVER|PARTITION|DESC|ASC|BETWEEN|LIKE|COALESCE|LOWER|UPPER|CAST|SUM|COUNT|AVG|MIN|MAX|DATE|INTERVAL)$/i
function tokenizeSqlLine(line: string, dark: boolean) {
  const C = dark
    ? { kw: '#c586c0', fn: '#4ec9b0', str: '#ce9178', num: '#b5cea8', punc: '#d4d4d4', id: '#9cdcfe', plain: '#d4d4d4', ph: '#dcdcaa' }
    : { kw: '#0033b3', fn: '#7a3e9d', str: '#c41a16', num: '#1c6e42', punc: '#475569', id: '#0f172a', plain: '#334155', ph: '#b45309' }
  const out: { t: string; c: string }[] = []
  const re = /('[^']*'|\{[A-Z_]+\}|[A-Za-z_][A-Za-z0-9_]*|\d+\.?\d*|\s+|[(),.*/+\-=<>]|.)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(line)) !== null) {
    const tok = m[0]
    let color = C.plain
    if (/^\s+$/.test(tok)) { out.push({ t: tok, c: C.plain }); continue }
    if (/^'.*'$/.test(tok)) color = C.str
    else if (/^\{[A-Z_]+\}$/.test(tok)) color = C.ph
    else if (/^\d/.test(tok)) color = C.num
    else if (SQL_KEYWORDS.test(tok)) color = /^(COALESCE|LOWER|UPPER|CAST|SUM|COUNT|AVG|MIN|MAX|DATE)$/i.test(tok) ? C.fn : C.kw
    else if (/^[A-Za-z_]/.test(tok)) color = C.id
    else color = C.punc
    out.push({ t: tok, c: color })
  }
  return out
}

export function QueryBox({ value, onChange, theme = 'light', onThemeChange, maxHeight = 360 }: { value: string; onChange: (v: string) => void; theme?: string; onThemeChange: (t: string) => void; maxHeight?: number }) {
  const taRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const dark = theme === 'dark'
  const lines = (value || '').split('\n')
  const bg = dark ? '#1e1e1e' : '#ffffff'
  const gutterBg = dark ? '#252526' : '#f8fafc'
  const gutterFg = dark ? '#858585' : '#cbd5e1'
  const border = dark ? '#333' : '#e2e8f0'
  const codeStyle: React.CSSProperties = { margin: 0, padding: '12px 24px 12px 12px', fontSize: '12.5px', lineHeight: '20px', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', whiteSpace: 'pre' }

  return (
    <div className="overflow-hidden rounded-lg border" style={{ borderColor: border, background: bg }}>
      <div className="flex items-center justify-end gap-2 border-b px-3 py-1.5" style={{ borderColor: border, background: dark ? '#252526' : '#fbfcfe' }}>
        <span className={'text-[12px] ' + (dark ? 'text-slate-500' : 'text-slate-400')}>Light</span>
        <button role="switch" aria-checked={dark} onClick={() => onThemeChange(dark ? 'light' : 'dark')} className={'relative h-4 w-7 rounded-full transition-colors ' + (dark ? 'bg-sky-500' : 'bg-slate-300')}>
          <span className={'absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-all ' + (dark ? 'left-[14px]' : 'left-0.5')} />
        </button>
        <span className={'text-[12px] ' + (dark ? 'text-slate-300' : 'text-slate-400')}>Dark</span>
      </div>
      <div ref={scrollRef} className="relative overflow-auto" style={{ maxHeight, overscrollBehavior: 'contain', background: bg }}
        onScroll={(e) => { if (taRef.current) { taRef.current.scrollTop = (e.target as HTMLDivElement).scrollTop; taRef.current.scrollLeft = (e.target as HTMLDivElement).scrollLeft } }}>
        <div className="relative flex" style={{ width: 'max-content', minWidth: '100%' }}>
          <div className="sticky left-0 z-10 select-none py-3 text-right" style={{ background: gutterBg, color: gutterFg, minWidth: 44 }}>
            {lines.map((_, i) => <div key={i} className="px-2 text-[12.5px] leading-[20px]">{i + 1}</div>)}
          </div>
          <div className="relative flex-1">
            {/* highlight layer */}
            <pre style={{ ...codeStyle, color: dark ? '#d4d4d4' : '#334155' }} aria-hidden>
              {lines.map((ln, i) => (
                <div key={i} style={{ minHeight: 20 }}>{ln === '' ? '​' : tokenizeSqlLine(ln, dark).map((tk, j) => <span key={j} style={{ color: tk.c }}>{tk.t}</span>)}</div>
              ))}
            </pre>
            {/* editable layer (transparent text over the highlight) */}
            <textarea
              ref={taRef} value={value} spellCheck={false}
              onChange={(e) => onChange(e.target.value)}
              onScroll={(e) => { if (scrollRef.current) { scrollRef.current.scrollTop = (e.target as HTMLTextAreaElement).scrollTop } }}
              className="absolute inset-0 h-full w-full resize-none overflow-hidden border-0 bg-transparent outline-none"
              style={{ ...codeStyle, color: 'transparent', caretColor: dark ? '#fff' : '#0f172a' }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
