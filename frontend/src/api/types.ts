// Shapes returned by the backend. Kept here so components never import the
// network layer directly — the container maps these into presentational props.

export interface ChartSummary {
  id: number
  name: string
  chart_number: number | null
  certified: boolean
  time_column: string | null
}

export interface ChartFull {
  id: number
  name: string
  chart_number: number | null
  certified: boolean
  source: string
  query: string
  refresh_interval: string
  default_backpop_days: number
  backpop_batch_size: number
  default_date_range_days: number
  cur_date_behavior: string
  cache_strategy: string
  time_column: string | null
  date_format: string
  variables: Record<string, string | string[]>
}

export interface IntrospectColumn {
  name: string
  column_name: string
  kind?: string
  data_type?: string | null
}
export interface IntrospectionResult {
  time_column: string | null
  dimensions: IntrospectColumn[]
  metrics: IntrospectColumn[]
}

export interface ChartWriteBody {
  name?: string
  certified?: boolean
  source?: string
  query?: string
  refresh_interval?: string
  default_backpop_days?: number
  backpop_batch_size?: number
  default_date_range_days?: number
  cur_date_behavior?: string
  cache_strategy?: string
  time_column?: string | null
  date_format?: string
  variables?: Record<string, string | string[]>
}

export interface DimensionCfg {
  name: string
  column_name: string
  kind: string
  value_order?: 'natural' | 'metric'
  derived?: boolean // computed in the backend (e.g. country_tier) — usable as a filter, not editable
  data_type?: string | null
}

export interface MetricCfg {
  name: string
  column_name: string | null
  independent_dimensions: string[]
  formula: string | null
  y_axis: 'primary' | 'secondary'
  decimals: number
  unit: string | null
  data_type?: string | null
}

export interface DimsMetrics {
  time_column: string | null
  date_format: string | null
  dimensions: DimensionCfg[]
  metrics: MetricCfg[]
}

export interface DimValues {
  dimensions: Record<string, string[]>
  date_min: string | null
  date_max: string | null
}

export interface DataResponse {
  chart_id: number
  granularity: string
  dimensions: string[]
  metrics: string[]
  rows: Record<string, number | string | null>[]
  row_count: number
}

export interface BackpopRun {
  id: number
  chart_id: number
  from_date: string
  to_date: string
  batch_size: number
  status: string // running | success | failed
  row_count: number
  batches_completed: number
  error_message: string | null
  started_at: string
  completed_at: string | null
}

export interface Freshness {
  latest_data_date: string | null
  running: boolean
  last_run: BackpopRun | null
}

export interface ChartOverview {
  id: number
  name: string
  chart_number: number | null
  certified: boolean
  latest_data_date: string | null
  last_backpop_status: string | null
  last_backpop_at: string | null
  last_backpop_rows: number | null
  running: boolean
}

export interface DataQuery {
  granularity: string
  from?: string | null
  to?: string | null
  metrics: string[]
  groupBy: string[] // dimensions to split by (empty => time-only aggregate)
  filters: Record<string, string[]>
  hideZero: boolean
}
