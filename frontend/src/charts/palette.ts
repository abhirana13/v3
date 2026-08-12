/* Series colours — the ONE definition, shared by the chart view and dashboard widgets.
 *
 * These used to be two separate lists (a 10-colour one in ChartViewContainer and a different
 * 10-colour one in dashboards/widgets), so the same chart came out in different colours
 * depending on where you looked at it. Colour is supposed to identify a series; two palettes
 * for one chart means it identifies the page instead.
 *
 * Twenty colours, ASSIGNED BY SERIES INDEX IN ORDER — never shuffled, never sorted by value, so
 * a series keeps its colour as other series come and go. 1-10 are the strong set and cover any
 * chart with <=10 lines; 11-20 extend it with lighter/darker neighbours of the same hues.
 *
 * Twenty hues is past what colour alone resolves, so identity does NOT rest on hue. Every
 * consumer must keep its series labels: the chart view has an always-on legend plus a labelled
 * tooltip (HoverCard in time mode, the categorical formatter in TimeSeriesChart); widgets have a
 * scrolling ECharts legend and a labelled tooltip. Those labels are load-bearing — do not drop
 * them to save space.
 *
 * Line weight is 1.6px, 2.6px on hover-focus. Do not go below 1.5px: the lighter entries
 * (11-14, 16-18, 20) lose contrast against white.
 */

export const SERIES_COLORS = [
  '#2563EB', '#E4572E', '#0E9F6E', '#8B3FD1', '#C99700',
  '#0E7490', '#DB2777', '#4D7C0F', '#8B5E3C', '#475569',
  '#60A5FA', '#F59E0B', '#34D399', '#C084FC', '#A16207',
  '#22B8CF', '#F472B6', '#84CC16', '#B45309', '#94A3B8',
]

/** Amber, drawn DASHED, reserved for target / threshold lines. Also slot 12 of the palette,
 *  which is why seriesColors() drops it when a target line is on the same chart. */
export const TARGET_LINE_COLOR = '#F59E0B'

/** Slate, doubling as the "other / rest" bucket (slot 20). Nothing aggregates into an explicit
 *  "other" series yet; when something does, it takes this colour rather than a new one. */
export const OTHER_BUCKET_COLOR = '#94A3B8'

/** Colours available to SERIES on this chart. Drops the reserved amber when the chart also
 *  draws a target line, so no series can be mistaken for the target. */
export function seriesColors(hasTargetLine = false): string[] {
  return hasTargetLine ? SERIES_COLORS.filter((c) => c !== TARGET_LINE_COLOR) : SERIES_COLORS
}

/** Max series a chart may DRAW: one per available colour, so a colour is never reused inside a
 *  chart. Falls to 19 when a target line claims the amber — a chart that would have shown 20
 *  then reports one truncated rather than silently drawing two series the same colour. */
export function maxSeries(hasTargetLine = false): number {
  return seriesColors(hasTargetLine).length
}

/** Colour for series index `i`, wrapping only if a caller ignores maxSeries(). */
export function seriesColor(i: number, hasTargetLine = false): string {
  const colors = seriesColors(hasTargetLine)
  return colors[i % colors.length]
}
