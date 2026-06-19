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

// X-axis = dimension: collapse time → categories are the cuts, value is the metric
// summed over the whole range. One series per metric (cuts of one metric share metricKey).
const XSEP = ''
export function buildCategorical(data: ChartRow[], series: UISeries[]): { data: ChartRow[]; series: UISeries[] } {
  const comboOf = (s: UISeries) => { const i = s.key.indexOf(XSEP); return i >= 0 ? s.key.slice(i + 1) : s.label }
  const cats: string[] = []
  const seenCat = new Set<string>()
  for (const s of series) { const c = comboOf(s); if (!seenCat.has(c)) { seenCat.add(c); cats.push(c) } }
  const metrics: UISeries[] = []
  const seenM = new Set<string>()
  for (const s of series) { const mk = s.metricKey || s.key; if (!seenM.has(mk)) { seenM.add(mk); metrics.push({ ...s, key: mk, label: s.metricKey || s.label, metricKey: mk }) } }
  const keyByMC = new Map<string, string>()
  for (const s of series) keyByMC.set((s.metricKey || s.key) + XSEP + comboOf(s), s.key)
  const sumOver = (k: string) => data.reduce((acc, r) => acc + (typeof r[k] === 'number' ? (r[k] as number) : 0), 0)
  const outData: ChartRow[] = cats.map((c) => {
    const r: ChartRow = { date: c } // 'date' field carries the category for the X-axis
    for (const m of metrics) { const sk = keyByMC.get(m.key + XSEP + c); r[m.key] = sk != null ? sumOver(sk) : null }
    return r
  })
  return { data: outData, series: metrics }
}
