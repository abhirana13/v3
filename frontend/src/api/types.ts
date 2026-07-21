// Shapes returned by the backend. Kept here so components never import the
// network layer directly — the container maps these into presentational props.

export interface ChartSummary {
  id: number
  name: string
  chart_number: number | null
  certified: boolean
  time_column: string | null
}

export interface Datasources {
  databases: string[]
  default: string
}

export interface ChartFull {
  id: number
  name: string
  chart_number: number | null
  certified: boolean
  source: string
  database: string | null
  query: string
  refresh_interval: string
  default_backpop_days: number
  backpop_batch_size: number
  default_date_range_days: number
  default_end_offset_days: number
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
  database?: string | null
  query?: string
  refresh_interval?: string
  default_backpop_days?: number
  backpop_batch_size?: number
  default_date_range_days?: number
  default_end_offset_days?: number
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
  default_end_offset_days?: number
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

// ---------- dashboards ----------

export type FilterValue = string | number | boolean
export type GlobalFilters = Record<string, FilterValue[]>

export interface WidgetLayout { x: number; y: number; w: number; h: number }

export interface WidgetMetricSel { name: string; y_axis: 'primary' | 'secondary' }

export interface ChartWidgetConfig {
  viz: 'line' | 'bar'
  metrics: WidgetMetricSel[]
  filters: GlobalFilters
  group_by: string[]
  offset_days: number | null
  offset_mode: string
  x_axis: string
  y_axis: Record<string, { min?: number; max?: number }>
  target: number | null
}

export interface NumberWidgetConfig {
  metric: string
  filters: GlobalFilters
  decimals: number
  unit: string | null
  compares: ('previous_day' | 'last_week')[]
  offset_days: number | null
  target: number | null
}

export interface DashWidget {
  id: number
  tab_id: number
  source_chart_id: number
  type: 'chart' | 'number'
  name: string
  layout: WidgetLayout
  config: Partial<ChartWidgetConfig & NumberWidgetConfig>
  display_order: number
}

export interface DashTab { id: number; name: string; display_order: number; widgets: DashWidget[] }

export interface DashFilterCfg { id: number; dimension: string; default_values: FilterValue[]; display_order: number }

export interface DashboardMeta {
  id: number
  name: string
  number: number | null
  enabled: boolean
  default_date_range_days: number
  default_end_offset_days: number
  created_at: string
  updated_at: string
}

export interface DashboardFull extends DashboardMeta {
  tabs: DashTab[]
  filters: DashFilterCfg[]
}

export interface DashboardOverviewRow {
  id: number
  name: string
  number: number | null
  enabled: boolean
  updated_at: string
  tab_count: number
  widget_count: number
}

export interface ChartWidgetData {
  widget_id: number
  chart_id: number
  time_column: string
  dimension_columns: string[]
  from_date: string | null
  to_date: string | null
  granularity: string
  dimensions: string[]
  metrics: string[]
  rows: Record<string, number | string | null>[]
  row_count: number
}

export interface CompareDelta { abs: number; pct: number | null }

export interface NumberWidgetData {
  widget_id: number
  chart_id: number
  metric: string
  as_of_date: string
  value: number | null
  compares: Partial<Record<'previous_day' | 'last_week', CompareDelta | null>>
  unit: string | null
  decimals: number
  target: number | null
}

export type WidgetData = ChartWidgetData | NumberWidgetData

export interface WidgetWriteBody {
  type?: 'chart' | 'number'
  source_chart_id?: number
  name?: string
  layout?: WidgetLayout
  config?: Record<string, unknown>
}
