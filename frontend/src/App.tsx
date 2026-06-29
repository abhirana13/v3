import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import { api } from './api/client'
import type { ChartSummary } from './api/types'

// Route-level code splitting: each view is its own async chunk so the heavy one
// (ChartViewContainer pulls in ECharts) only downloads when that view is opened,
// not on the initial home/list landing. Named exports → unwrap to { default }.
const ChartViewContainer = lazy(() => import('./pages/chart/ChartViewContainer').then((m) => ({ default: m.ChartViewContainer })))
const ConfigContainer = lazy(() => import('./pages/config/ConfigContainer').then((m) => ({ default: m.ConfigContainer })))
const HomePage = lazy(() => import('./pages/home/HomePage').then((m) => ({ default: m.HomePage })))

type View = { name: 'home' } | { name: 'chart' } | { name: 'config'; target: number | 'new' }

export function App() {
  const [charts, setCharts] = useState<ChartSummary[] | null>(null)
  const [chartId, setChartId] = useState<number | null>(null)
  const [view, setView] = useState<View>(() => {
    const params = new URLSearchParams(window.location.search)
    const cfg = params.get('config') // ?config=<id|new> opens the configure tab
    if (cfg != null) return { name: 'config', target: cfg === 'new' ? 'new' : Number(cfg) }
    const chart = Number(params.get('chart')) // ?chart=<id> deep-links straight to a chart
    if (Number.isInteger(chart) && chart > 0) return { name: 'chart' }
    return { name: 'home' } // default landing = charts list
  })
  const [error, setError] = useState<string | null>(null)

  const loadCharts = useCallback(async (selectId?: number) => {
    const cs = await api.listCharts()
    setCharts(cs)
    if (selectId != null && cs.some((c) => c.id === selectId)) setChartId(selectId)
    else if (chartId == null && cs.length) setChartId(cs[0].id)
  }, [chartId])

  useEffect(() => {
    const q = Number(new URLSearchParams(window.location.search).get('chart')) // shared-link target
    loadCharts(Number.isInteger(q) && q > 0 ? q : undefined).catch((e) => setError(String(e.message || e)))
  }, [])

  // keep the URL in sync with the current view so a browser reload lands in the
  // same place. replaceState (not push) avoids history spam; we leave the URL
  // untouched while a chart is still loading so we don't clobber a ?chart= deep link.
  useEffect(() => {
    const base = window.location.pathname
    if (view.name === 'home') window.history.replaceState({}, '', base)
    else if (view.name === 'chart' && chartId != null) window.history.replaceState({}, '', `${base}?chart=${chartId}`)
    else if (view.name === 'config') window.history.replaceState({}, '', `${base}?config=${view.target}`)
  }, [view, chartId])

  if (error) return <Centered>Failed to load: {error}</Centered>

  // Resolve the current view to an element, then render it under one Suspense
  // boundary so a lazy view's chunk shows the same "Loading…" fallback while it loads.
  let body: React.ReactNode
  if (view.name === 'home') {
    body = <HomePage onOpenChart={(id) => { setChartId(id); setView({ name: 'chart' }) }} />
  } else if (view.name === 'config') {
    body = (
      <ConfigContainer
        target={view.target}
        charts={charts || []}
        onBack={() => {
          // go back to the chart we were editing, not whatever chartId defaulted
          // to (cs[0]) when this config view loaded from a ?config=<id> link
          if (typeof view.target === 'number') setChartId(view.target)
          setView({ name: 'chart' })
        }}
        onSaved={async (id) => { await loadCharts(id) }}
        onDeleted={async () => {
          window.history.replaceState({}, '', window.location.pathname)
          const cs = await api.listCharts()
          setCharts(cs)
          setChartId(cs.length ? cs[0].id : null)
          setView({ name: 'chart' })
        }}
      />
    )
  } else if (!charts) {
    body = <Centered>Loading…</Centered>
  } else if (charts.length === 0) {
    body = (
      <Centered>
        <div className="text-center">
          <p className="mb-3">No charts yet.</p>
          <button onClick={() => setView({ name: 'config', target: 'new' })} className="rounded-md bg-sky-500 px-4 py-2 text-[13px] font-semibold text-white hover:bg-sky-600">Create your first chart</button>
        </div>
      </Centered>
    )
  } else if (chartId == null) {
    body = <Centered>Select a chart.</Centered>
  } else {
    body = (
      <ChartViewContainer
        chartId={chartId}
        charts={charts.map((c) => ({ id: c.id, name: c.name, number: c.chart_number, certified: c.certified }))}
        onSelectChart={setChartId}
        onGoHome={() => setView({ name: 'home' })}
        onEditChart={(id) => window.open(`${window.location.pathname}?config=${id}`, '_blank')}
        onCreateChart={() => window.open(`${window.location.pathname}?config=new`, '_blank')}
      />
    )
  }

  return <Suspense fallback={<Centered>Loading…</Centered>}>{body}</Suspense>
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center font-sans text-[14px] text-slate-500">{children}</div>
}
