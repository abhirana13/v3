/* Chart hover card (Hover Tooltip design): per-series value + two period-over-
   period deltas whose labels adapt to granularity (DoD/WoW, WoW/4W, MoM/YoY).
   Dumb/presentational — values arrive pre-formatted, deltas are integer percents
   (null => "—"). Positioning is handled by TimeSeriesChart. */

export interface HoverRow {
  name: string
  color: string
  value: string // pre-formatted (unit + decimals applied) or "—"
  short: number | null // shorter-period delta (e.g. DoD)
  long: number | null // longer-period delta (e.g. WoW)
}

// Total across all series at the hovered point + its deltas (null => not shown).
export interface HoverTotal {
  value: string // pre-formatted aggregate
  short: number | null
  long: number | null
}

function Delta({ v }: { v: number | null }) {
  if (v == null) return <span className="text-[11px] text-slate-300">—</span>
  const up = v > 0, flat = v === 0
  const cls = flat ? 'text-slate-400' : up ? 'text-emerald-600' : 'text-rose-500'
  const arrow = flat ? '' : up ? '▲' : '▼'
  return <span className={'text-[11px] font-semibold tabular-nums ' + cls}>{arrow} {Math.abs(v)}%</span>
}

export function HoverCard({ title, rows, shortLabel, longLabel, total }: {
  title: string; rows: HoverRow[]; shortLabel: string; longLabel: string; total?: HoverTotal | null
}) {
  return (
    <div className="w-[268px] overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl">
      {/* date + (optional) total aggregate; no labels, deltas reuse the same color format */}
      <div className="flex items-center border-b border-slate-100 px-3.5 py-2.5">
        <span className="text-[13px] font-bold text-slate-800">{title}</span>
        {total && (
          <span className="ml-auto flex items-center gap-2.5 pl-2">
            <span className="text-[13px] font-bold tabular-nums text-slate-800">{total.value}</span>
            <Delta v={total.short} />
            <Delta v={total.long} />
          </span>
        )}
      </div>
      <div className="grid grid-cols-[1fr_40px_52px_52px] items-center gap-x-3 px-3.5 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
        <span>Series</span><span className="text-right">Val</span><span className="text-right">{shortLabel}</span><span className="text-right">{longLabel}</span>
      </div>
      <div className="max-h-[280px] overflow-y-auto px-3.5 pb-2.5">
        {rows.map((r) => (
          <div key={r.name} className="grid grid-cols-[1fr_40px_52px_52px] items-center gap-x-3 py-[3px]">
            <span className="flex items-center gap-2 truncate">
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: r.color }} />
              <span className="truncate text-[13px] text-slate-700">{r.name}</span>
            </span>
            <span className="text-right text-[13px] font-semibold tabular-nums text-slate-800">{r.value}</span>
            <span className="text-right"><Delta v={r.short} /></span>
            <span className="text-right"><Delta v={r.long} /></span>
          </div>
        ))}
      </div>
    </div>
  )
}
