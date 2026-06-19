// Thin api-client. The ONLY place that knows the backend's URL shape, so the
// UI stays decoupled (CLAUDE.md). Calls go to /api/* which Vite proxies to the
// backend (prefix stripped).
import type { BackpopRun, ChartFull, ChartOverview, ChartSummary, ChartWriteBody, DataQuery, DataResponse, DimsMetrics, DimValues, Freshness, IntrospectionResult, MetricCfg } from './types'

const BASE = '/api'

async function json<T>(url: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(BASE + url, opts)
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  return res.json() as Promise<T>
}

function qs(params: Record<string, string | undefined | null>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v as string)}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

function dataQuery(q: DataQuery): string {
  const parts: string[] = [`granularity=${q.granularity}`]
  if (q.from) parts.push(`from_date=${q.from}`)
  if (q.to) parts.push(`to_date=${q.to}`)
  // group_by: one repeated param per split dimension. Always send at least an
  // empty one so the backend stays time-only (omitting it groups by ALL dims).
  if (q.groupBy.length) for (const d of q.groupBy) parts.push(`group_by=${encodeURIComponent(d)}`)
  else parts.push('group_by=')
  for (const m of q.metrics) parts.push(`metrics=${encodeURIComponent(m)}`)
  if (Object.keys(q.filters).length) parts.push(`filters=${encodeURIComponent(JSON.stringify(q.filters))}`)
  if (q.hideZero) parts.push('hide_zero=true')
  return `?${parts.join('&')}`
}

const jsonBody = (method: string, body: unknown) => ({
  method,
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  listCharts: () => json<ChartSummary[]>('/charts'),
  getChart: (id: number) => json<ChartFull>(`/charts/${id}`),
  createChart: (body: ChartWriteBody) => json<ChartFull>('/charts', jsonBody('POST', body)),
  updateChart: (id: number, body: ChartWriteBody) => json<ChartFull>(`/charts/${id}`, jsonBody('PUT', body)),
  introspect: (id: number) => json<IntrospectionResult>(`/charts/${id}/introspect`, { method: 'POST' }),
  // body omitted => server runs the chart's default window (default_backpop_days, ending today)
  backpopulate: (id: number, body?: { from_date: string; to_date: string; batch_size?: number }) =>
    json<BackpopRun>(
      `/charts/${id}/backpopulate`,
      body ? jsonBody('POST', body) : { method: 'POST' },
    ),
  backpopRuns: (id: number) => json<BackpopRun[]>(`/charts/${id}/backpop-runs`),
  freshness: (id: number) => json<Freshness>(`/charts/${id}/freshness`),
  chartsOverview: () => json<ChartOverview[]>('/charts/overview'),
  deleteChart: (id: number) =>
    fetch(`${BASE}/charts/${id}`, { method: 'DELETE' }).then(async (res) => {
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
    }),
  getDimsMetrics: (id: number) => json<DimsMetrics>(`/charts/${id}/dims-metrics`),
  getDimValues: (id: number, from?: string | null, to?: string | null) =>
    json<DimValues>(`/charts/${id}/dim-values${qs({ from_date: from, to_date: to })}`),
  getData: (id: number, q: DataQuery) => json<DataResponse>(`/charts/${id}/data${dataQuery(q)}`),
  putDimsMetrics: (id: number, body: { time_column: string | null; dimensions: { name: string; column_name: string }[]; metrics: MetricCfg[] }) =>
    json<DimsMetrics>(`/charts/${id}/dims-metrics`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }),
}
