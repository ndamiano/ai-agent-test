import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { AdminCosts, CostPod } from '../../types'
import { Refresh, Stat, fmtHours, fmtUsd, shortGpu } from './format'

const WINDOWS = [{ days: 1, label: '24h' }, { days: 7, label: '7d' }, { days: 30, label: '30d' }]

const usd = (n: number | null): string => n == null ? '—' : fmtUsd(n)
const signedUsd = (n: number | null): string => n == null ? '—' : `${n < 0 ? '−' : ''}${fmtUsd(Math.abs(n))}`
const pct = (n: number | null): string => n == null ? '—' : `${Math.round(n * 100)}%`
const util = (p: CostPod): number | null => p.billed_seconds ? p.exec_seconds / p.billed_seconds : null
const margin = (p: CostPod): number | null => p.provider_usd == null ? null : p.customer_usd - p.provider_usd

interface Column {
    key: string
    label: string
    title?: string
    value: (p: CostPod) => number | string | null
    show: (p: CostPod) => React.ReactNode
}

const COLUMNS: Column[] = [
    { key: 'pod', label: 'pod', value: p => p.pod_id,
      show: p => <>{p.pod_id}{!p.tracked && <span className="text-slate"> ghost</span>}</> },
    { key: 'cost', label: 'cost', title: "the provider's bill for this pod; EC2 is its launch rate over its lifetime",
      value: p => p.provider_usd, show: p => usd(p.provider_usd) },
    { key: 'billed', label: 'billed', title: 'what customers were charged for its jobs', value: p => p.customer_usd,
      show: p => fmtUsd(p.customer_usd) },
    { key: 'jobs', label: 'jobs', value: p => p.jobs, show: p => p.jobs },
    { key: 'util', label: 'util', title: 'job time ÷ billed GPU time', value: util, show: p => pct(util(p)) },
    { key: 'margin', label: 'margin', title: 'billed − cost', value: margin,
      show: p => { const m = margin(p); return <span className={m != null && m < 0 ? 'text-wait' : ''}>{signedUsd(m)}</span> } },
]

export interface Sort { key: string; desc: boolean }

export const sortPods = (pods: CostPod[], { key, desc }: Sort): CostPod[] => {
    const col = COLUMNS.find(c => c.key === key)!
    return [...pods].sort((a, b) => {
        const x = col.value(a), y = col.value(b)
        if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1
        const c = x < y ? -1 : x > y ? 1 : 0
        return desc ? -c : c
    })
}

export const CostSummary: React.FC<{ pods: CostPod[]; reachable: boolean }> = ({ pods, reachable }) => {
    const cost = pods.reduce((a, p) => a + (p.provider_usd ?? 0), 0)
    const billed = pods.reduce((a, p) => a + p.customer_usd, 0)
    const billedSecs = pods.reduce((a, p) => a + (p.billed_seconds ?? 0), 0)
    const worked = pods.reduce((a, p) => a + p.exec_seconds, 0)
    const jobs = pods.reduce((a, p) => a + p.jobs, 0)
    const ghosts = pods.filter(p => !p.tracked)
    return (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
            <Stat label="cost" value={reachable ? fmtUsd(cost) : '—'} hint="RunPod's ledger plus EC2 at launch rate" />
            <Stat label="billed" value={fmtUsd(billed)} hint="what customers were charged" />
            <Stat label="margin" value={reachable ? signedUsd(billed - cost) : '—'} />
            <Stat label="pods" value={`${pods.length}${ghosts.length ? ` (${ghosts.length} ghost)` : ''}`}
                hint="ghost: billed by RunPod, never named by a worker row" />
            <Stat label="jobs" value={jobs} />
            <Stat label="util" value={pct(billedSecs ? worked / billedSecs : null)} />
        </div>
    )
}

export const PodTable: React.FC<{ pods: CostPod[]; sort: Sort; onSort: (s: Sort) => void }> = ({ pods, sort, onSort }) => (
    <div className="overflow-x-auto">
        <table className="w-full text-sm font-mono">
            <thead>
                <tr className="text-slate text-xs">
                    {COLUMNS.map((c, i) => (
                        <th key={c.key} title={c.title}
                            className={`font-semibold pb-1 ${i === 0 ? 'text-left pr-3' : 'text-right pl-3'}`}>
                            <button onClick={() => onSort({ key: c.key, desc: sort.key === c.key ? !sort.desc : c.key !== 'pod' })}
                                className="hover:text-bone">
                                {c.label}{sort.key === c.key ? (sort.desc ? ' ↓' : ' ↑') : ''}
                            </button>
                        </th>
                    ))}
                </tr>
            </thead>
            <tbody className="text-bone">
                {sortPods(pods, sort).map(p => (
                    <tr key={p.worker_id ?? `ghost-${p.pod_id}`} className={p.tracked ? '' : 'text-wait'}
                        title={p.tracked ? `${p.source} · ${p.queue} · ${p.gpu_type ? shortGpu(p.gpu_type) : '?'}` : undefined}>
                        {COLUMNS.map((c, i) => (
                            <td key={c.key} className={`py-0.5 ${i === 0 ? 'pr-3' : 'pl-3 text-right'}`}>{c.show(p)}</td>
                        ))}
                    </tr>
                ))}
            </tbody>
        </table>
    </div>
)

const CostsPage: React.FC = () => {
    const [days, setDays] = useState(7)
    const [sort, setSort] = useState<Sort>({ key: 'cost', desc: true })
    const [costs, setCosts] = useState<AdminCosts | null>(null)
    const [busy, setBusy] = useState(false)

    const load = useCallback(async () => {
        setBusy(true)
        try { setCosts(await api.getAdminCosts(days)) }
        catch { /* leave the last snapshot up */ }
        finally { setBusy(false) }
    }, [days])

    useEffect(() => { load() }, [load])

    if (!costs) return <div className="text-slate text-xs">Loading costs…</div>

    const g = costs.games
    return (
        <div className="space-y-4">
            <div className="flex items-baseline justify-between gap-3 flex-wrap">
                <h2 className="text-white font-semibold text-sm">
                    Pod costs {costs.runpod_reachable ? '' : '— RunPod ledger unreachable, our logs only'}
                </h2>
                <div className="flex items-baseline gap-3 text-xs">
                    {WINDOWS.map(w => (
                        <button key={w.days} onClick={() => setDays(w.days)}
                            className={w.days === days ? 'text-bone font-semibold' : 'text-slate hover:text-bone'}>
                            {w.label}
                        </button>
                    ))}
                    <Refresh busy={busy} onClick={load} />
                </div>
            </div>
            <CostSummary pods={costs.pods} reachable={costs.runpod_reachable} />
            <div className="text-xs text-slate font-mono">
                {g.n} game{g.n === 1 ? '' : 's'} built
                {g.avg_gpu_hours != null && ` · ${fmtHours(g.avg_gpu_hours * 3600)} GPU/game`}
                {g.avg_usd != null && ` · ${fmtUsd(g.avg_usd)}/game`}
                {g.avg_changes != null && ` · ${g.avg_changes} changes/game`}
            </div>
            <div className="bg-ink border border-edge rounded-lg p-4">
                {costs.pods.length === 0
                    ? <div className="text-slate text-xs">No pods in this window.</div>
                    : <PodTable pods={costs.pods} sort={sort} onSort={setSort} />}
            </div>
        </div>
    )
}

export default CostsPage
