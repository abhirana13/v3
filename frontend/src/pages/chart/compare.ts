/* Period-over-period comparison: overlay the same series from N periods ago.
 *
 * The model is "shift the whole window back", not "show the last week next to the week
 * before". Every point on the current line gets a counterpart at the same position one (or
 * two, or four) periods earlier, so the comparison reads as a trend against its own past
 * rather than two short stubs side by side.
 */

export type CompareUnit = 'week' | 'month' | 'quarter' | 'year'

export interface CompareCfg {
  unit: CompareUnit
  /** how many periods back to overlay; 1 = the immediately preceding period. Empty = off. */
  offsets: number[]
}

export const COMPARE_OFF: CompareCfg = { unit: 'week', offsets: [] }
export const MAX_OFFSET = 4

export const COMPARE_UNITS: { unit: CompareUnit; label: string; letter: string }[] = [
  { unit: 'week', label: 'Week', letter: 'W' },
  { unit: 'month', label: 'Month', letter: 'M' },
  { unit: 'quarter', label: 'Quarter', letter: 'Q' },
  { unit: 'year', label: 'Year', letter: 'Y' },
]

const LETTER: Record<CompareUnit, string> = { week: 'W', month: 'M', quarter: 'Q', year: 'Y' }

export const compareLabel = (unit: CompareUnit, n: number) => `${LETTER[unit]}-${n}`

export const isComparing = (c: CompareCfg) => c.offsets.length > 0

const pad = (n: number) => String(n).padStart(2, '0')
const iso = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`

/* Dates are handled in UTC throughout.
 *
 * `new Date('2026-10-05T00:00:00')` is LOCAL time, and local arithmetic across a DST boundary
 * silently shifts a day: subtracting 7*n days over the end of summer time lands at 23:00 the
 * previous day, and toISO then reports the wrong date. Comparison windows routinely cross
 * those boundaries — a year back always does — so every calculation here uses Date.UTC. */
function shiftWeeks(y: number, m: number, d: number, weeks: number): string {
  const t = new Date(Date.UTC(y, m - 1, d))
  t.setUTCDate(t.getUTCDate() - 7 * weeks)
  return iso(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate())
}

/** Last day of a month, used to clamp a day that doesn't exist in the target month. */
const daysInMonth = (y: number, m: number) => new Date(Date.UTC(y, m, 0)).getUTCDate()

/** The same calendar position, `n` periods earlier. */
export function shiftBack(dateISO: string, unit: CompareUnit, n: number): string {
  const [y, m, d] = dateISO.split('-').map(Number)
  if (!y || !m || !d) return dateISO
  if (unit === 'week') return shiftWeeks(y, m, d, n)

  const months = unit === 'month' ? n : unit === 'quarter' ? 3 * n : 12 * n
  const total = y * 12 + (m - 1) - months
  const ty = Math.floor(total / 12)
  const tm = (total % 12) + 1
  // 31 Mar -1 month is 28 Feb, not 3 Mar. Clamping means several late-month days can share one
  // comparison day; that is inherent to comparing calendar months and is better than silently
  // rolling into the wrong month.
  return iso(ty, tm, Math.min(d, daysInMonth(ty, tm)))
}

/** Snap a date onto the bucket start the backend would have grouped it into.
 *
 * Without this, only granularity/unit pairs that happen to line up would work: weekly buckets
 * are Mondays, and a Monday shifted back a year is some other weekday, so an exact-date lookup
 * would miss every point and the comparison would silently render as nothing. date_trunc is
 * Monday-based in DuckDB, which is what 'week' mirrors here.
 */
export function snapToBucket(dateISO: string, granularity: string): string {
  const [y, m, d] = dateISO.split('-').map(Number)
  if (!y || !m || !d) return dateISO
  if (granularity === 'month') return iso(y, m, 1)
  if (granularity === 'week') {
    const t = new Date(Date.UTC(y, m - 1, d))
    const dow = (t.getUTCDay() + 6) % 7 // 0 = Monday
    t.setUTCDate(t.getUTCDate() - dow)
    return iso(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate())
  }
  return dateISO
}

/** Where a current bucket's value should be read from, `n` periods back. */
export const comparisonBucket = (
  bucketISO: string, unit: CompareUnit, n: number, granularity: string,
) => snapToBucket(shiftBack(bucketISO, unit, n), granularity)

/** The fetch window for one comparison offset, covering every bucket the overlay needs. */
export function comparisonWindow(
  start: string, end: string, unit: CompareUnit, n: number, granularity: string,
) {
  return {
    // snapped, because the earliest bucket needed may start before the shifted start date
    from: comparisonBucket(start, unit, n, granularity),
    to: shiftBack(end, unit, n),
  }
}

/* Comparison series share their base series' COLOUR and separate by DASH PATTERN.
 *
 * Giving a comparison its own hue would consume the palette (20 colours, one per series, never
 * reused) to say something that is not a different series — it is the same thing, earlier.
 *
 * The pattern, not opacity, is what tells W-1 from W-3. An opacity ladder alone collapses
 * exactly where it is needed: two faint lines of the same hue crossing each other are
 * indistinguishable without hovering every point, and the furthest period ends up so washed
 * out it reads as a rendering artefact. A dash pattern stays legible wherever the line goes,
 * including over gridlines and through a crossing.
 *
 * (Dashes were rejected earlier as a way to EXTEND the palette across 20 split series — one
 * colour per series is still the rule, and nothing here breaks it.)
 *
 * Patterns are chosen to differ in rhythm, not just in length, so they are separable at a
 * glance rather than by measuring: long dash, fine dot, dash-dot, sparse long dash.
 */
const GHOST_DASH: number[][] = [
  [7, 4],          // W-1 — dashed
  [1.5, 3.5],      // W-2 — dotted
  [9, 3, 1.5, 3],  // W-3 — dash-dot
  [14, 6],         // W-4 — long dash
]
export const compareDash = (offset: number): number[] =>
  GHOST_DASH[Math.min(offset, GHOST_DASH.length) - 1] ?? GHOST_DASH[GHOST_DASH.length - 1]

/** CSS stroke-dasharray for the same pattern, so the picker's swatches match the plot. */
export const compareDashCss = (offset: number) => compareDash(offset).join(' ')

/* A single modest step down in opacity, the same for every period.
 *
 * Its only job now is keeping the CURRENT period the obvious subject; distinguishing the
 * periods from each other is the dash pattern's. A ladder would fight that — the furthest
 * pattern would be the hardest to see precisely when it is the one being checked. */
export const compareOpacity = (_offset: number) => 0.8

/* Comparisons multiply the series count, and the 20-series cap is one-colour-per-series.
 * Rather than refusing the whole thing, drop the FURTHEST periods first: W-1 is the comparison
 * people actually read, W-4 the one they can lose. Returns the offsets that fit. */
export function offsetsWithinCap(offsets: number[], baseSeriesCount: number, cap: number): number[] {
  if (!offsets.length || baseSeriesCount <= 0) return offsets
  const room = Math.floor(cap / baseSeriesCount) - 1
  if (room >= offsets.length) return offsets
  return [...offsets].sort((a, b) => a - b).slice(0, Math.max(0, room))
}
