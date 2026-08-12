/* Chart view state carried in the URL.
 *
 * Exists so a dashboard widget can link to its source chart showing the SAME cuts: metrics,
 * splits, filters, window, granularity and x-axis. Before this, the only link was `?chart=<id>`
 * and the chart opened on whatever the viewer last looked at (view state lives in localStorage,
 * see viewState.ts), so the numbers on screen rarely matched the widget you clicked from.
 *
 * Parsed ONCE at App init rather than in an effect: App rewrites the URL to a bare `?chart=<id>`
 * as soon as the chart id settles, which would strip these params out from under a later reader.
 *
 * Both the parser and the builder live here so the two ends cannot drift apart.
 */

export interface ChartViewSeed {
  metrics?: string[]
  split?: string[]
  filters?: Record<string, (string | number | boolean)[]>
  from?: string
  to?: string
  granularity?: string // 'Day' | 'Week' | 'Month' — the chart view's own casing
  // a dimension name to pivot on; '' means force a plain time series
  xAxis?: string | null
}

const GRAN_FROM_API: Record<string, string> = { day: 'Day', week: 'Week', month: 'Month' }
const csv = (v: string | null) => (v ? v.split(',').map((s) => s.trim()).filter(Boolean) : undefined)

/** Read a seed from a query string. Returns null when it carries no view state at all, so the
 *  caller can tell "opened plainly" from "opened on specific cuts". */
export function parseChartSeed(search: string): ChartViewSeed | null {
  const p = new URLSearchParams(search)
  const seed: ChartViewSeed = {}
  const metrics = csv(p.get('metrics'))
  if (metrics) seed.metrics = metrics
  const split = csv(p.get('split'))
  if (split) seed.split = split
  const rawFilters = p.get('filters')
  if (rawFilters) {
    try {
      const parsed = JSON.parse(rawFilters)
      // only accept the shape we emit; a hand-mangled param must not throw the whole page
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        const out: Record<string, (string | number | boolean)[]> = {}
        for (const [k, v] of Object.entries(parsed)) if (Array.isArray(v)) out[k] = v as never[]
        if (Object.keys(out).length) seed.filters = out
      }
    } catch {
      /* ignore a malformed filters param rather than failing the load */
    }
  }
  const from = p.get('from'), to = p.get('to')
  if (from) seed.from = from
  if (to) seed.to = to
  const gran = p.get('gran')
  if (gran) seed.granularity = GRAN_FROM_API[gran.toLowerCase()] || gran
  // xaxis='' is meaningful (force a time series), so test for PRESENCE, not truthiness
  if (p.has('xaxis')) seed.xAxis = p.get('xaxis') || ''
  return Object.keys(seed).length ? seed : null
}

/** Build a link that reopens `chartId` on the given cuts. Omits everything empty so a plain
 *  link stays `?chart=<id>`. */
export function buildChartLink(chartId: number, seed: ChartViewSeed, pathname = window.location.pathname): string {
  const p = new URLSearchParams()
  p.set('chart', String(chartId))
  if (seed.metrics?.length) p.set('metrics', seed.metrics.join(','))
  if (seed.split?.length) p.set('split', seed.split.join(','))
  if (seed.filters && Object.keys(seed.filters).length) p.set('filters', JSON.stringify(seed.filters))
  if (seed.from) p.set('from', seed.from)
  if (seed.to) p.set('to', seed.to)
  if (seed.granularity) p.set('gran', seed.granularity.toLowerCase())
  if (seed.xAxis != null) p.set('xaxis', seed.xAxis)
  return `${pathname}?${p.toString()}`
}
