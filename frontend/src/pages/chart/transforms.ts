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
