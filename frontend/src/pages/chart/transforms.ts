import type { ChartRow, UISeries } from '../../components/types'

/* ----------------------------------------------- view transforms (rail tools) */
export function applyPercentage(data: ChartRow[], series: UISeries[]): { data: ChartRow[]; series: UISeries[] } {
  // per time bucket, each cut becomes its share of the metric's total (cuts of one metric sum to 100)
  const groups = new Map<string, UISeries[]>()
  for (const s of series) { const k = s.metricKey || s.key; const g = groups.get(k) || []; g.push(s); groups.set(k, g) }
  const out = data.map((row) => {
    const r: ChartRow = { date: row.date }
    groups.forEach((gs) => {
      const total = gs.reduce((sum, s) => sum + (typeof row[s.key] === 'number' ? (row[s.key] as number) : 0), 0)
      for (const s of gs) {
        const v = row[s.key]
        // A cut absent on this day is 0% (not null) — otherwise ECharts breaks the
        // stacked fill at the gap, so the day no longer sums to a solid 100%.
        r[s.key] = typeof v === 'number' && total > 0 ? (v / total) * 100 : 0
      }
    })
    return r
  })
  return { data: out, series: series.map((s) => ({ ...s, unit: '%', decimals: 1 })) }
}

export function applyMovingAverage(data: ChartRow[], series: UISeries[], window: number): ChartRow[] {
  // replace each series' value with the trailing mean over `window` buckets
  return data.map((_row, i) => {
    const r: ChartRow = { date: data[i].date }
    for (const s of series) {
      let sum = 0, n = 0
      for (let j = Math.max(0, i - window + 1); j <= i; j++) { const v = data[j][s.key]; if (typeof v === 'number') { sum += v; n++ } }
      r[s.key] = n > 0 ? sum / n : null
    }
    return r
  })
}

// NOTE: buildCategorical() used to live here — it re-pivoted already-fetched split
// series into categories in the browser. The x-axis dimension is now grouped by the
// BACKEND (chart.x_axis / the x_axis query param), so rows arrive keyed on it directly:
// correct aggregation at any cardinality, no 20-series split cap, and the
// independent-metric dedup applied in SQL rather than re-derived from summed series.

/* Order x-axis categories the way a human reads them, not the way strings sort.
   The x slot holds an ISO date on a time axis but a DIMENSION VALUE when the chart is
   pivoted (x_axis = a dimension), and both arrive here coerced to a string. A plain
   .sort() then produces "0, 1, 10, 11, ... 19, 2, 20" for level_number, and puts the
   cohort buckets in the order D1, D121-D360, D15-D30, D2-D7 — both unreadable.

   Splits each label into digit and non-digit chunks and compares chunk by chunk,
   numerically where both chunks are digits. Digits sort before text at the same
   position, mirroring app/serving/_natural_key so the axis and the filter dropdowns
   agree. ISO dates are unaffected: 2026-08-04 splits to [2026, '-', 8, '-', 4] and
   still compares in calendar order. */
type NatChunk = [0, number] | [1, string]

function naturalKey(value: unknown): NatChunk[] {
  return String(value)
    .split(/(\d+)/)
    .filter((p) => p !== '')
    .map<NatChunk>((p) => (/^\d+$/.test(p) ? [0, Number(p)] : [1, p.toLowerCase()]))
}

export function naturalCompare(a: unknown, b: unknown): number {
  const ka = naturalKey(a)
  const kb = naturalKey(b)
  for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
    const x = ka[i]
    const y = kb[i]
    if (x === undefined) return -1
    if (y === undefined) return 1
    if (x[0] !== y[0]) return x[0] - y[0]          // digits before text
    if (x[1] < y[1]) return -1
    if (x[1] > y[1]) return 1
  }
  return 0
}
