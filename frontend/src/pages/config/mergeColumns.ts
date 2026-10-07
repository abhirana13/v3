import type { ConfigColumn } from './DimsMetricsTable'
import type { IntrospectColumn } from '../../api/types'

/* Re-introspection discovers which COLUMNS a query returns. It must not re-decide what the
 * editor already told us about them.
 *
 * "Generate Dims And Metrics" used to rebuild the table from the introspection result alone,
 * so every per-column setting on a column that still existed was silently reset:
 *
 *   independentOf -> []          the independent-metric declaration, which is the one piece
 *                                of config the backend cannot infer and the one CLAUDE.md
 *                                calls the most important correctness concern. A metric that
 *                                was declared independent of a dimension quietly started
 *                                being summed across it again.
 *   decimals      -> 0           (and 0 also used to mean int() downstream)
 *   unit          -> null
 *   yAxis         -> primary
 *   valueOrder    -> natural
 *   included      -> true        re-showing columns the editor had hidden
 *   classification-> whatever the type heuristic guesses, discarding an explicit override
 *
 * None of that is visible when it happens: the table looks freshly populated, and the loss
 * only surfaces later as a chart whose numbers changed. It bites hardest right after a query
 * edit, because regenerating is exactly what you have to do then — the backpopulate button is
 * gated on it.
 *
 * So: introspection owns the column SET and each column's dataType. Everything else is the
 * editor's and is carried across by name. Genuinely new columns get the introspected defaults.
 */
export function mergeIntrospectedColumns(
  previous: ConfigColumn[],
  introspected: { dimensions: IntrospectColumn[]; metrics: IntrospectColumn[] },
): ConfigColumn[] {
  const prevByName = new Map(previous.map((c) => [c.name, c]))
  const seen = new Set<string>()

  const merge = (
    col: IntrospectColumn,
    fallbackClassification: 'Dimension' | 'Metric',
  ): ConfigColumn => {
    seen.add(col.name)
    const prev = prevByName.get(col.name)
    if (!prev) {
      return {
        name: col.name,
        classification: fallbackClassification,
        dataType: col.data_type || '—',
        independentOf: [],
        valueOrder: 'natural',
        included: true,
      }
    }
    return {
      ...prev,
      // the one thing the query is authoritative about
      dataType: col.data_type || prev.dataType || '—',
      // An explicit Dimension/Metric choice outranks the type heuristic that produced the
      // suggestion in the first place — game_id is numeric and is a dimension.
      classification: prev.classification,
    }
  }

  const dimensions = introspected.dimensions.map((d) => merge(d, 'Dimension'))
  const metrics = introspected.metrics.map((m) => merge(m, 'Metric'))

  // Formula metrics aren't column-backed, so introspection never returns them; they'd
  // otherwise be dropped by regenerating. (Pre-existing behaviour, kept.)
  const keptFormulas = previous.filter(
    (c) => c.classification === 'Metric' && c.formula && !seen.has(c.name),
  )

  const merged = [...dimensions, ...metrics, ...keptFormulas]

  // A metric can only be independent of a dimension that still exists. Dropping a stale name
  // here rather than at save time keeps the table honest about what will actually be stored.
  const dimNames = new Set(merged.filter((c) => c.classification === 'Dimension').map((c) => c.name))
  return merged.map((c) =>
    c.independentOf?.length
      ? { ...c, independentOf: c.independentOf.filter((n) => dimNames.has(n)) }
      : c,
  )
}
