import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { DashboardOverviewRow } from '../../api/types'

/* Dashboard list, ported from the Claude Design handoff (dashlist.jsx), with
   the app's standard dark header bar for navigation back to charts. */

const Ic = {
  search: (p: any) => (<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>),
  plus: (p: any) => (<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" {...p}><path d="M12 5v14M5 12h14" /></svg>),
  copy: (p: any) => (<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>),
  open: (p: any) => (<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M14 4h6v6M20 4l-9 9M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5" /></svg>),
  grid: (p: any) => (<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></svg>),
}

const fmtWhen = (iso: string) => {
  const d = new Date(iso)
  return isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function EnabledPill({ on }: { on: boolean }) {
  return on ? (
    <span className="inline-flex items-center gap-1.5 rounded bg-green-50 px-2 py-0.5 text-[11.5px] font-semibold text-green-600">
      <span className="h-1.5 w-1.5 rounded-full bg-green-500" />Enabled
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 rounded bg-slate-100 px-2 py-0.5 text-[11.5px] font-semibold text-slate-500">
      <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />Disabled
    </span>
  )
}

export function DashboardListPage({ onOpen, onGoCharts }: {
  onOpen: (id: number) => void
  onGoCharts: () => void
}) {
  const [rows, setRows] = useState<DashboardOverviewRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')

  const reload = () => api.listDashboards().then(setRows).catch((e: any) => setError(String(e.message || e)))
  useEffect(() => { reload() }, [])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const list = rows || []
    if (!needle) return list
    return list.filter((d) => (d.name + ' #' + (d.number ?? '')).toLowerCase().includes(needle))
  }, [rows, q])

  const onNew = () => {
    const name = window.prompt('Dashboard name:')
    if (!name || !name.trim()) return
    api.createDashboard({ name: name.trim() })
      .then((d) => onOpen(d.id))
      .catch((e: any) => window.alert(String(e.message || e)))
  }
  const onReplicate = (id: number) => {
    api.replicateDashboard(id)
      .then((copy) => onOpen(copy.id))
      .catch((e: any) => window.alert(String(e.message || e)))
  }

  return (
    <div className="flex h-full flex-col bg-slate-50 font-sans text-slate-900">
      <header className="flex h-12 shrink-0 items-center gap-4 bg-slate-800 px-4 text-slate-200">
        <div className="flex h-7 w-7 items-center justify-center rounded bg-sky-500 text-xs font-bold text-white">FG</div>
        <span className="text-[13px] font-semibold text-white">Analytics</span>
        <span className="text-[12px] text-slate-400">· Dashboards</span>
        <button onClick={onGoCharts} className="ml-auto rounded-md border border-slate-600 px-3 py-1.5 text-[12px] font-semibold text-slate-200 hover:bg-slate-700">Charts</button>
      </header>

      <div className="flex items-center gap-3 px-6 pt-5 pb-4">
        <h1 className="text-[22px] font-bold tracking-tight">Dashboards</h1>
        {rows && <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-500">{rows.length}</span>}
        <div className="ml-auto flex items-center gap-2">
          <div className="flex w-72 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-slate-400 focus-within:border-sky-400 focus-within:ring-1 focus-within:ring-sky-100">
            <Ic.search />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search dashboards…"
              className="w-full bg-transparent text-[13px] text-slate-700 outline-none placeholder:text-slate-300" />
          </div>
          <button onClick={onNew} className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3.5 py-1.5 text-sm font-medium text-white hover:bg-sky-700">
            <Ic.plus />New Dashboard
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8">
        {error && <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[13px] text-rose-600">Failed to load dashboards: {error}</div>}
        {!rows && !error && <div className="py-16 text-center text-[14px] text-slate-400">Loading…</div>}
        {rows && (
          <div className="overflow-hidden rounded-lg border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
            <div className="grid grid-cols-[minmax(0,1fr)_110px_190px_180px] gap-4 border-b border-slate-200 bg-slate-50/60 px-5 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              <div>Name</div><div>Status</div><div>Last updated</div><div className="text-right">Actions</div>
            </div>
            {filtered.length === 0 && (
              <div className="px-5 py-10 text-center text-[13px] text-slate-400">
                {rows.length === 0 ? 'No dashboards yet — create your first one.' : 'No dashboards match your search.'}
              </div>
            )}
            {filtered.map((d) => (
              <div key={d.id} onClick={() => onOpen(d.id)}
                className="group grid cursor-pointer grid-cols-[minmax(0,1fr)_110px_190px_180px] items-center gap-4 border-b border-slate-100 px-5 py-3 transition-colors last:border-0 hover:bg-slate-50">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-sky-50 text-sky-500"><Ic.grid /></span>
                  <div className="min-w-0">
                    <div className="truncate text-[13.5px] font-semibold text-slate-800">
                      {d.name} <span className="font-medium text-slate-400">#{d.number ?? d.id}</span>
                    </div>
                    <div className="truncate text-[11.5px] text-slate-400">
                      {d.tab_count} tab{d.tab_count === 1 ? '' : 's'} · {d.widget_count} widget{d.widget_count === 1 ? '' : 's'}
                    </div>
                  </div>
                </div>
                <div><EnabledPill on={d.enabled} /></div>
                <div className="text-[12.5px] tabular-nums text-slate-500">{fmtWhen(d.updated_at)}</div>
                <div className="flex items-center justify-end gap-2 opacity-0 transition-opacity group-hover:opacity-100">
                  <button onClick={(e) => { e.stopPropagation(); onOpen(d.id) }}
                    className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[12px] font-medium text-slate-600 hover:border-sky-300 hover:text-sky-600">
                    <Ic.open />Open
                  </button>
                  <button onClick={(e) => { e.stopPropagation(); onReplicate(d.id) }}
                    className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[12px] font-medium text-slate-600 hover:border-sky-300 hover:text-sky-600">
                    <Ic.copy />Replicate
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
