import type { ChartRow, UISeries } from '../../components/types'

const sanitize = (s: string) => s.replace(/[^\w.-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 60) || 'chart'

function trigger(filename: string, href: string, revoke = false) {
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  if (revoke) setTimeout(() => URL.revokeObjectURL(href), 0)
}

const cell = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)

/** Export the currently-plotted series as a CSV (date column + one column per series). */
export function downloadCsv(base: string, data: ChartRow[], series: UISeries[]) {
  const lines = [['date', ...series.map((s) => s.label)].map(cell).join(',')]
  for (const row of data) {
    const cells = [String(row.date ?? ''), ...series.map((s) => { const v = row[s.key]; return v == null ? '' : String(v) })]
    lines.push(cells.map(cell).join(','))
  }
  const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' }))
  trigger(`${sanitize(base)}.csv`, url, true)
}

/** Export a PNG data URL produced by the ECharts instance. */
export function downloadPng(base: string, dataUrl: string) {
  trigger(`${sanitize(base)}.png`, dataUrl)
}
