// Thin api-client. The ONLY place that knows the backend's URL shape, so the
// UI stays decoupled (CLAUDE.md). Calls go to /api/* which Vite proxies to the
// backend (prefix stripped).
import type { BackpopRun, ChartFull, ChartOverview, ChartSummary, ChartWriteBody, DashboardFull, DashboardMeta, DashboardOverviewRow, DashTab, DashWidget, DataQuery, DataResponse, Datasources, DimsMetrics, DimValues, FilterValue, Freshness, GlobalFilters, IntrospectionResult, MetricCfg, WidgetData, WidgetLayout, WidgetWriteBody } from './types'

const BASE = '/api'

/* A 503 from this backend means one specific thing: a backpopulation holds DuckDB's write
   lock, which is per-FILE and so blocks reads of every chart, not just the one being written.
   It clears on its own in seconds, so surfacing it as an error was wrong — the user opened an
   unrelated chart and got "the aggregate cache is being written by a backpopulation right
   now". Wait out the window instead, honouring the Retry-After the backend sends.

   Only for GET. A 503 on a mutation is left alone: our one mutating 503 (chart delete) means
   the drop did not happen, and silently retrying writes is how you get surprises. */
const RETRY_503 = 2

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function json<T>(url: string, opts?: RequestInit, attempt = 0): Promise<T> {
  const res = await fetch(BASE + url, opts)
  if (res.status === 503 && attempt < RETRY_503 && !(opts?.method && opts.method !== 'GET')) {
    const after = Number(res.headers.get('Retry-After'))
    await sleep((Number.isFinite(after) && after > 0 ? after : 5) * 1000)
    return json<T>(url, opts, attempt + 1)
  }
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
  // x_axis: only sent when set — omitting it lets the backend apply the chart's default
  if (q.xAxis) parts.push(`x_axis=${encodeURIComponent(q.xAxis)}`)
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
  datasources: () => json<Datasources>('/datasources'),
  listCharts: () => json<ChartSummary[]>('/charts'),
  getChart: (id: number) => json<ChartFull>(`/charts/${id}`),
  createChart: (body: ChartWriteBody) => json<ChartFull>('/charts', jsonBody('POST', body)),
  updateChart: (id: number, body: ChartWriteBody) => json<ChartFull>(`/charts/${id}`, jsonBody('PUT', body)),
  introspect: (id: number) => json<IntrospectionResult>(`/charts/${id}/introspect`, { method: 'POST' }),
  // body omitted => server runs the chart's default window (default_backpop_days, ending today)
  backpopulate: (id: number, body?: { from_date: string; to_date: string; batch_size?: number; force?: boolean }) =>
    json<BackpopRun>(
      `/charts/${id}/backpopulate`,
      body ? jsonBody('POST', body) : { method: 'POST' },
    ),
  backpopRuns: (id: number) => json<BackpopRun[]>(`/charts/${id}/backpop-runs`),
  cancelBackpop: (chartId: number, runId: number) =>
    json<BackpopRun>(`/charts/${chartId}/backpop-runs/${runId}/cancel`, { method: 'POST' }),
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
  putDimsMetrics: (id: number, body: { time_column: string | null; dimensions: { name: string; column_name: string; value_order?: 'natural' | 'metric' }[]; metrics: MetricCfg[] }) =>
    json<DimsMetrics>(`/charts/${id}/dims-metrics`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }),

  // ---------- dashboards ----------
  listDashboards: () => json<DashboardOverviewRow[]>('/dashboards'),
  getDashboard: (id: number) => json<DashboardFull>(`/dashboards/${id}`),
  createDashboard: (body: { name: string; enabled?: boolean; default_date_range_days?: number; default_end_offset_days?: number }) =>
    json<DashboardMeta>('/dashboards', jsonBody('POST', body)),
  updateDashboard: (id: number, body: Partial<{ name: string; enabled: boolean; default_date_range_days: number; default_end_offset_days: number }>) =>
    json<DashboardMeta>(`/dashboards/${id}`, jsonBody('PUT', body)),
  deleteDashboard: (id: number) =>
    fetch(`${BASE}/dashboards/${id}`, { method: 'DELETE' }).then(async (res) => {
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
    }),
  replicateDashboard: (id: number) => json<DashboardFull>(`/dashboards/${id}/replicate`, { method: 'POST' }),
  dashboardFilterValues: (id: number) => json<{ values: Record<string, FilterValue[]> }>(`/dashboards/${id}/filter-values`),
  getWidgetData: (dashboardId: number, widgetId: number, q: { from?: string | null; to?: string | null; granularity?: string; filters?: GlobalFilters; split?: string[]; offsetDays?: number | null }) => {
    const parts: string[] = []
    if (q.from) parts.push(`from_date=${q.from}`)
    if (q.to) parts.push(`to_date=${q.to}`)
    if (q.granularity) parts.push(`granularity=${q.granularity}`)
    if (q.filters && Object.keys(q.filters).length) parts.push(`filters=${encodeURIComponent(JSON.stringify(q.filters))}`)
    for (const d of q.split || []) parts.push(`split=${encodeURIComponent(d)}`)
    if (q.offsetDays != null) parts.push(`offset_days=${q.offsetDays}`)
    return json<WidgetData>(`/dashboards/${dashboardId}/widgets/${widgetId}/data${parts.length ? `?${parts.join('&')}` : ''}`)
  },
  // render an unsaved widget (edit-mode working copy) from a posted config
  previewWidgetData: (dashboardId: number, body: { type: 'chart' | 'number'; source_chart_id: number; config: Record<string, unknown>; from?: string | null; to?: string | null; granularity?: string; filters?: GlobalFilters; split?: string[]; offsetDays?: number | null }) =>
    json<WidgetData>(`/dashboards/${dashboardId}/widget-preview`, jsonBody('POST', {
      type: body.type,
      source_chart_id: body.source_chart_id,
      config: body.config,
      from_date: body.from ?? null,
      to_date: body.to ?? null,
      granularity: body.granularity ?? 'day',
      filters: body.filters ?? {},
      split: body.split ?? [],
      offset_days: body.offsetDays ?? null,
    })),
  addDashboardTab: (dashboardId: number, name: string) =>
    json<DashTab>(`/dashboards/${dashboardId}/tabs`, jsonBody('POST', { name })),
  updateDashboardTab: (dashboardId: number, tabId: number, body: Partial<{ name: string; display_order: number }>) =>
    json<DashTab>(`/dashboards/${dashboardId}/tabs/${tabId}`, jsonBody('PUT', body)),
  deleteDashboardTab: (dashboardId: number, tabId: number) =>
    fetch(`${BASE}/dashboards/${dashboardId}/tabs/${tabId}`, { method: 'DELETE' }).then(async (res) => {
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
    }),
  addDashboardWidget: (dashboardId: number, tabId: number, body: WidgetWriteBody) =>
    json<DashWidget>(`/dashboards/${dashboardId}/tabs/${tabId}/widgets`, jsonBody('POST', body)),
  updateDashboardWidget: (dashboardId: number, widgetId: number, body: WidgetWriteBody) =>
    json<DashWidget>(`/dashboards/${dashboardId}/widgets/${widgetId}`, jsonBody('PUT', body)),
  deleteDashboardWidget: (dashboardId: number, widgetId: number) =>
    fetch(`${BASE}/dashboards/${dashboardId}/widgets/${widgetId}`, { method: 'DELETE' }).then(async (res) => {
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
    }),
  saveTabLayout: (dashboardId: number, tabId: number, items: ({ widget_id: number } & WidgetLayout)[]) =>
    json<DashTab>(`/dashboards/${dashboardId}/tabs/${tabId}/layout`, jsonBody('PUT', items)),
  putDashboardFilters: (dashboardId: number, filters: { dimension: string; default_values?: FilterValue[] }[]) =>
    json<DashboardFull>(`/dashboards/${dashboardId}/filters`, jsonBody('PUT', { filters })),
}
