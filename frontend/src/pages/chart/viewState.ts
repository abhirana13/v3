/* Per-chart view state, remembered in the browser.
 *
 * Selecting dimensions and then reloading, or bouncing through the configure page, used to
 * reset everything: switching views unmounts the chart, and configure opens in a NEW tab
 * whose "Back to chart" renders the chart there — so the state has to be readable from a
 * different tab. That rules out sessionStorage (per-tab) and means localStorage.
 *
 * Costs the backend nothing: this never leaves the browser.
 *
 * Kept deliberately small — a selection is stored as the 'all' sentinel unless it's a
 * partial one, so the common case is a few dozen bytes instead of every dimension value.
 * Entries carry a timestamp and expire, so a filter set from last month can't silently
 * resurrect weeks later, and a version tag lets the shape change without reading garbage.
 */

const VERSION = 1
const KEY = (chartId: number | string) => `analytics.chartview.v${VERSION}.${chartId}`
const TTL_MS = 7 * 24 * 60 * 60 * 1000 // a week

export interface SavedDimState {
  split?: boolean
  sel?: 'all' | string[] // 'all' => every value (the default); a list => partial selection
}

export interface SavedChartView {
  v: number
  ts: number
  granularity?: string
  chartType?: string
  hideZero?: boolean
  // NOT stored: the date window, the recency offset and the x-axis. Those are the chart's
  // DEFINITION, re-read from its config on every open — a remembered window meant a chart
  // opened on whatever range someone last dragged it to. Entries written before this change
  // may still carry those keys on disk; they are simply ignored.
  dims?: Record<string, SavedDimState>
  metrics?: string[] // visible metric names
}

/** Saved state for a chart, or null when absent/expired/unreadable. */
export function loadChartView(chartId: number | string): SavedChartView | null {
  try {
    const raw = window.localStorage.getItem(KEY(chartId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as SavedChartView
    if (!parsed || parsed.v !== VERSION) return null
    if (typeof parsed.ts !== 'number' || Date.now() - parsed.ts > TTL_MS) {
      window.localStorage.removeItem(KEY(chartId))
      return null
    }
    return parsed
  } catch {
    // private mode, disabled storage, or corrupt JSON — fall back to defaults
    return null
  }
}

export function saveChartView(chartId: number | string, state: Omit<SavedChartView, 'v' | 'ts'>): void {
  try {
    window.localStorage.setItem(KEY(chartId), JSON.stringify({ ...state, v: VERSION, ts: Date.now() }))
  } catch {
    // quota exceeded or storage unavailable — remembering state is best-effort
  }
}

export function clearChartView(chartId: number | string): void {
  try {
    window.localStorage.removeItem(KEY(chartId))
  } catch {
    /* ignore */
  }
}

/** 'all' when every value is selected (keeps the payload tiny), else the explicit list. */
export function encodeSelection(selected: string[], values: string[]): 'all' | string[] {
  return selected.length === values.length ? 'all' : selected
}

/** Restore a selection against the CURRENT values — dimension values change as data lands,
 *  so anything stale is dropped. An empty result means the saved values are all gone, which
 *  would render as "nothing selected"; fall back to everything rather than an empty chart. */
export function decodeSelection(saved: SavedDimState | undefined, values: string[]): string[] {
  if (!saved || saved.sel == null || saved.sel === 'all') return values
  const keep = new Set(saved.sel.map(String))
  const next = values.filter((v) => keep.has(String(v)))
  return next.length ? next : values
}
