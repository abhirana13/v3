/* Number formatting shared by the chart view and dashboard widgets.
 *
 * These lived only inside TimeSeriesChart, so widgets grew their own cruder versions and the
 * same value rendered differently depending on where you looked at it. The widget y-axis did
 * `v / 1000 + 'K'` with no rounding at all, so 1234.5678 came out as "1.2345678K".
 */

/** Axis-label precision, adapted to the data span.
 *
 * A fixed precision makes tight ranges collapse: near 1.0, three gridlines at 0.95 / 1.00 / 1.05
 * all render as "1". Derived from the SPAN (falling back to magnitude when every value is equal)
 * so the labels stay distinguishable at whatever scale the metric happens to live on.
 */
export function axisDecimals(values: number[]): number {
  if (!values.length) return 1
  let mn = Infinity, mx = -Infinity
  for (const v of values) { if (v < mn) mn = v; if (v > mx) mx = v }
  const ref = (mx - mn) || Math.abs(mx) || 1
  return ref >= 5 ? 0 : ref >= 0.5 ? 1 : 2
}

/** Compact axis label: 1.2k, 0.95, 12. `axisDec` comes from axisDecimals(). */
export function compactAxis(v: number, axisDec: number): string {
  if (v == null || isNaN(v)) return ''
  if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1).replace(/\.0$/, '') + 'k'
  const s = v.toFixed(axisDec)
  // keep 0.90 vs 1.00 distinct; tidy whole steps elsewhere
  return axisDec >= 2 ? s : s.replace(/\.0$/, '')
}

/** A metric's display shape. Matches the fields UISeries and the chart's metric config carry. */
export interface ValueFormat { unit?: string | null; decimals?: number | null }

/** Format a series value with its metric's unit and decimals.
 *
 * A non-zero value never collapses to "0": precision widens (to 6dp) until a digit appears, so a
 * small ratio metric configured at 0 or 2 decimals stays readable. That matters more now that
 * serving returns formula metrics at full precision instead of pre-rounding them.
 */
export function formatValue(v: number, f: ValueFormat): string {
  const u = f.unit && f.unit !== 'None' ? f.unit : ''
  let dp = f.decimals ?? 0
  if (v !== 0) while (dp < 6 && Number(v.toFixed(dp)) === 0) dp++
  // Placement comes from unitAffix() so tooltips and number tiles cannot disagree about it.
  // This used to inline its own '$' prefix / '%' suffix pair and dropped 'k' and 'ms' entirely,
  // so a metric configured in milliseconds showed a bare number.
  const { prefix, suffix, spaced } = unitAffix(u)
  return (
    prefix +
    v.toLocaleString(undefined, { maximumFractionDigits: dp, minimumFractionDigits: 0 }) +
    (suffix ? (spaced ? ' ' : '') + suffix : '')
  )
}


/** Where a unit sits relative to the number, and whether it wants a space.
 *
 * Only currency leads; everything else trails. The number tile used to prefix EVERY unit
 * (`withUnit` did `${unit} ${v}`), so a percentage metric rendered as "% 49.57".
 *
 * `spaced` is per-unit convention rather than taste: "49.57%" and "1.2k" close up, "150 ms" does
 * not. Units are the fixed set the metric editor offers — None, %, $, k, ms.
 */
export interface UnitAffix { prefix: string; suffix: string; spaced: boolean }

export function unitAffix(unit?: string | null): UnitAffix {
  const u = unit && unit !== 'None' ? unit : ''
  if (u === '$') return { prefix: '$', suffix: '', spaced: false }
  if (u === '%') return { prefix: '', suffix: '%', spaced: false }
  if (u === 'k') return { prefix: '', suffix: 'k', spaced: false }
  if (u === 'ms') return { prefix: '', suffix: 'ms', spaced: true }
  return { prefix: '', suffix: '', spaced: false }
}


/** Does this unit already carry a magnitude letter of its own?
 *
 * Only 'k' does. It matters because the number tile abbreviates large values itself (1234 ->
 * "1.23K"), so a metric whose unit is 'k' rendered "1.23Kk" — two magnitude markers, one of them
 * invented. Callers suppress their own abbreviation when this is true and show the plain number
 * with the unit the metric actually asked for.
 *
 * The unit is a LABEL, not a transform: nothing here divides the value. A metric that stores
 * thousands is the query's business, not the formatter's.
 */
export function unitCarriesMagnitude(unit?: string | null): boolean {
  return unit === 'k'
}

/** Affix for an ABSOLUTE delta (v - v_prev), which is not always the value's own unit.
 *
 * For a percentage metric the absolute delta is in percentage POINTS, so it reads 'pp'. Writing
 * '%' there would put two differently-meaning percentages side by side — the pill next to it
 * already shows the relative change — and "+17.25%" alongside "53.37%" invites reading the
 * absolute delta as another relative one. Every other unit is unchanged.
 */
export function deltaAffix(unit?: string | null): UnitAffix {
  if (unit === '%') return { prefix: '', suffix: 'pp', spaced: true }
  return unitAffix(unit)
}
