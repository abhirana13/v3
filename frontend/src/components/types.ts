// Presentational prop shapes (decoupled from the API types). The container
// maps api/types.ts -> these.

export interface UIDimension {
  key: string
  label: string
  values: string[]
  selected: string[]
  split: boolean // unchecked chip => split the chart into one series per cut
}

export interface UIMetric {
  id: string
  name: string
  key: string
  color: string
  visible: boolean
  columnName?: string | null // backing DuckDB column for base metrics (null for formula)
  formula?: string
  independentFields?: string[]
  axis?: 'primary' | 'secondary'
  decimals?: number
  unit?: string
}

export interface UISeries {
  key: string
  label: string
  color: string
  axis: 'primary' | 'secondary'
  unit?: string
  decimals?: number
  metricKey?: string // which metric this series belongs to (cuts of one metric share it)
  metricLabel?: string // the metric's display name (legend groups split series under it)
  comboLabel?: string // when split, the dimension-value cut (e.g. "D0", "US · iOS")
}

export type ChartRow = Record<string, number | string | null>

// chart display options (Options tab) — all view-only, no backend
export interface ChartOptions {
  showLegend: boolean
  smooth: boolean
  showPoints: boolean
  connectNulls: boolean
  gridlines: boolean
  zeroBase: boolean // start primary Y-axis at zero
  logScale: boolean // logarithmic primary Y-axis
}
