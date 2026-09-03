import React, { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { AdminAnalytics, AdminCosts, AdminQueues, AdminViolation, QueueRow } from '../types'

const POLL_MS = 5000

export const fmtSecs = (s: number): string =>
    s >= 3600 ? `${(s / 3600).toFixed(1)}h` : `${Math.round(s)}s`

const fmtUsd = (n: number): string => `$${n.toFixed(2)}`

// Cost is fetched once and on demand, never on the poll — the backend caches, but RunPod's
// ledger is not a thing to lean on every five seconds.
const CostPanel: React.FC = () => {
    const [costs, setCosts] = useState<AdminCosts | null>(null)
    const [busy, setBusy] = useState(false)

    const load = useCallback(async () => {
        setBusy(true)
        try { setCosts(await api.getAdminCosts()) }
        catch { /* leave the last snapshot up */ }
        finally { setBusy(false) }
    }, [])

    useEffect(() => { load() }, [load])

    if (!costs) return <div className="text-slate text-xs">Loading costs…</div>

    return (
        <div className="bg-ink border border-edge rounded-lg p-4 space-y-3">
            <div className="flex items-baseline justify-between">
                <div className="text-slate text-xs font-semibold">
                    Effective cost {costs.runpod_reachable ? '(RunPod ledger)' : '— ledger unreachable, our logs only'}
                </div>
                <button onClick={load} disabled={busy}
                    className="text-xs text-slate hover:text-bone transition-colors disabled:opacity-40">
                    {busy ? 'refreshing…' : 'refresh'}
                </button>
            </div>

            <table className="w-full text-sm font-mono">
                <thead>
                    <tr className="text-slate text-xs text-left">
                        <th className="font-semibold pb-1">window</th>
                        <th className="font-semibold pb-1">spend</th>
                        <th className="font-semibold pb-1" title="spend ÷ billed pod wall-clock — cold start and failures included">$/GPU·h</th>
                        <th className="font-semibold pb-1" title="job exec seconds ÷ billed pod wall-clock">util</th>
                        <th className="font-semibold pb-1" title="billed wall-clock the jobs didn't use: cold start, idle, boot loops">overhead</th>
                        <th className="font-semibold pb-1">jobs ✓/✗</th>
                    </tr>
                </thead>
                <tbody className="text-bone">
                    {costs.windows.map(w => (
                        <tr key={w.label}>
                            <td className="py-0.5">{w.label}</td>
                            <td>{w.runpod ? fmtUsd(w.runpod.amount_usd) : '—'}</td>
                            <td>{w.derived.usd_per_gpu_hour != null ? fmtUsd(w.derived.usd_per_gpu_hour) : '—'}</td>
                            <td>{w.derived.utilization != null ? `${Math.round(w.derived.utilization * 100)}%` : '—'}</td>
                            <td>{w.derived.overhead_seconds != null ? fmtSecs(w.derived.overhead_seconds) : '—'}</td>
                            <td>{w.jobs.done}/{w.jobs.failed}</td>
                        </tr>
                    ))}
                </tbody>
            </table>

            {costs.windows.at(-1)?.runpod && (
                <div className="text-xs text-slate">
                    30d by card: {costs.windows.at(-1)!.runpod!.by_gpu.map(g =>
                        `${g.gpu.replace('NVIDIA ', '').replace('GeForce ', '')} ${fmtUsd(g.amount_usd)} (${fmtSecs(g.billed_seconds)})`,
                    ).join(' · ')}
                </div>
            )}
            {costs.ghost_30d && costs.ghost_30d.pods > 0 && (
                <div className="text-xs text-wait">
                    ghost spend 30d: {fmtUsd(costs.ghost_30d.amount_usd)} across {costs.ghost_30d.pods} pod{costs.ghost_30d.pods === 1 ? '' : 's'} that
                    billed {fmtSecs(costs.ghost_30d.billed_seconds)} and never worked a job
                </div>
            )}
        </div>
    )
}

// Safety refusals, newest first — the repeat-offender view. Rows carry only the matched terms.
const ViolationsPanel: React.FC = () => {
    const [rows, setRows] = useState<AdminViolation[] | null>(null)
    const [busy, setBusy] = useState(false)

    const load = useCallback(async () => {
        setBusy(true)
        try { setRows((await api.getAdminViolations()).violations) }
        catch { /* leave the last snapshot up */ }
        finally { setBusy(false) }
    }, [])

    useEffect(() => { load() }, [load])

    if (!rows) return <div className="text-slate text-xs">Loading violations…</div>

    return (
        <div className="bg-ink border border-edge rounded-lg p-4 space-y-3">
            <div className="flex items-baseline justify-between">
                <div className="text-slate text-xs font-semibold">Safety violations</div>
                <button onClick={load} disabled={busy}
                    className="text-xs text-slate hover:text-bone transition-colors disabled:opacity-40">
                    {busy ? 'refreshing…' : 'refresh'}
                </button>
            </div>
            {rows.length === 0
                ? <div className="text-slate text-xs">None recorded.</div>
                : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm font-mono">
                            <thead>
                                <tr className="text-slate text-xs text-left">
                                    <th className="font-semibold pb-1 pr-3">when</th>
                                    <th className="font-semibold pb-1 pr-3">user</th>
                                    <th className="font-semibold pb-1 pr-3">game</th>
                                    <th className="font-semibold pb-1 pr-3">source</th>
                                    <th className="font-semibold pb-1 pr-3">category</th>
                                    <th className="font-semibold pb-1">matched</th>
                                </tr>
                            </thead>
                            <tbody className="text-bone">
                                {rows.map(v => (
                                    <tr key={v.id}>
                                        <td className="py-0.5 pr-3 whitespace-nowrap">
                                            {new Date(v.created_at * 1000).toLocaleString()}
                                        </td>
                                        <td className="pr-3">{v.handle ?? v.user_id ?? '—'}</td>
                                        <td className="pr-3">{v.game_id ?? '—'}</td>
                                        <td className="pr-3">{v.source}</td>
                                        <td className="pr-3">{v.category}</td>
                                        <td>{v.matched}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
        </div>
    )
}

// Usage is fetched once and on demand, like costs — nobody sizes a fleet off page views.
const UsagePanel: React.FC = () => {
    const [data, setData] = useState<AdminAnalytics | null>(null)
    const [busy, setBusy] = useState(false)

    const load = useCallback(async () => {
        setBusy(true)
        try { setData(await api.getAdminAnalytics()) }
        catch { /* leave the last snapshot up */ }
        finally { setBusy(false) }
    }, [])

    useEffect(() => { load() }, [load])

    if (!data) return <div className="text-slate text-xs">Loading usage…</div>

    return (
        <div className="bg-ink border border-edge rounded-lg p-4 space-y-3">
            <div className="flex items-baseline justify-between">
                <div className="text-slate text-xs font-semibold">Usage — events by day, last 14 days</div>
                <button onClick={load} disabled={busy}
                    className="text-xs text-slate hover:text-bone transition-colors disabled:opacity-40">
                    {busy ? 'refreshing…' : 'refresh'}
                </button>
            </div>

            {data.days.length === 0
                ? <div className="text-slate text-xs">No user events recorded yet.</div>
                : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm font-mono">
                            <thead>
                                <tr className="text-slate text-xs text-left">
                                    <th className="font-semibold pb-1 pr-3">day</th>
                                    <th className="font-semibold pb-1 pr-3">users</th>
                                    {data.kinds.map(k => (
                                        <th key={k} className="font-semibold pb-1 pr-3">{k}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody className="text-bone">
                                {data.days.map(d => (
                                    <tr key={d.day}>
                                        <td className="py-0.5 pr-3">{d.day}</td>
                                        <td className="pr-3">{d.users}</td>
                                        {data.kinds.map(k => (
                                            <td key={k} className="pr-3">{d.kinds[k] ?? 0}</td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
        </div>
    )
}

const fmtAge = (s: number | null): string =>
    s == null ? '—' : s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`

const Stat: React.FC<{ label: string; value: React.ReactNode; hint?: string }> = ({ label, value, hint }) => (
    <div title={hint}>
        <div className="text-slate text-xs font-semibold uppercase tracking-wide">{label}</div>
        <div className="text-bone text-sm font-mono mt-0.5">{value}</div>
    </div>
)

const QueueCard: React.FC<{ row: QueueRow }> = ({ row }) => {
    const active = row.pending + row.claimed > 0
    return (
        <div className="bg-panel border border-edge rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
                <span className="text-white font-semibold text-sm capitalize">{row.queue}</span>
                <span className={`text-xs font-mono ${active ? 'text-wait' : 'text-slate'}`}>
                    {row.pending} pending · {row.claimed} claimed
                </span>
            </div>
            <div className="grid grid-cols-3 gap-y-3 gap-x-2">
                <Stat label="Workers" value={`${row.workers_live}${row.workers_max ? ` / ${row.workers_max}` : ''}`}
                    hint={row.workers_max ? 'live / max pods' : 'live workers (home box, no cap)'} />
                <Stat label="Oldest wait" value={fmtAge(row.oldest_pending_age_seconds)}
                    hint="age of the oldest pending job" />
                <Stat label="Backlog" value={fmtSecs(row.backlog_seconds)}
                    hint={`projected GPU-s to clear (est ${row.est_seconds}s/job)`} />
                <Stat label="Paid 24h" value={fmtSecs(row.paid_24h)} hint="GPU-s we paid for, last 24h" />
                <Stat label="Paid all" value={fmtSecs(row.paid_all)} hint="GPU-s we paid for, all-time" />
                <Stat label="Billed all" value={fmtSecs(row.billed_all)}
                    hint="delivered GPU-s charged to games, all-time" />
            </div>
        </div>
    )
}

const AdminPanel: React.FC = () => {
    const [data, setData] = useState<AdminQueues | null>(null)
    const [error, setError] = useState<string | null>(null)

    const load = useCallback(async () => {
        try {
            setData(await api.getAdminQueues())
            setError(null)
        } catch (e) {
            // A 403 shouldn't happen (the tab is role-gated) but surface it rather than spin silently.
            setError(e instanceof ApiError && e.status === 403 ? 'Admin access required.' : 'Failed to load queue stats.')
        }
    }, [])

    useEffect(() => {
        load()
        const id = setInterval(load, POLL_MS)
        return () => clearInterval(id)
    }, [load])

    if (error) return <div className="p-6 text-sm text-fail">{error}</div>
    if (!data) return <div className="p-6 text-sm text-slate">Loading queue stats…</div>

    const t = data.totals
    return (
        <div className="h-full overflow-y-auto p-6">
            <div className="max-w-4xl mx-auto space-y-4">
                <div className="flex items-baseline justify-between">
                    <h2 className="text-white font-semibold text-sm">Inference queues</h2>
                    <span className="text-slate text-xs">GPU-seconds · refreshes every {POLL_MS / 1000}s</span>
                </div>

                <div className="grid gap-3">
                    {data.queues.map(q => <QueueCard key={q.queue} row={q} />)}
                </div>

                <ViolationsPanel />

                <UsagePanel />

                <CostPanel />

                <div className="bg-ink border border-edge rounded-lg p-4">
                    <div className="text-slate text-xs font-semibold mb-3">Fleet totals</div>
                    <div className="grid grid-cols-4 gap-y-3 gap-x-2">
                        <Stat label="In queue" value={t.pending + t.claimed} hint="pending + claimed, all queues" />
                        <Stat label="Workers" value={t.workers_live} hint="live workers, all queues" />
                        <Stat label="Backlog" value={fmtSecs(t.backlog_seconds)} hint="projected GPU-s to clear everything" />
                        <Stat label="Paid 24h" value={fmtSecs(t.paid_24h)} hint="GPU-s we paid for, last 24h" />
                        <Stat label="Paid all" value={fmtSecs(t.paid_all)} hint="GPU-s we paid for, all-time" />
                        <Stat label="Billed all" value={fmtSecs(t.billed_all)} hint="delivered GPU-s charged to games" />
                        <Stat label="Billed 24h" value={fmtSecs(t.billed_24h)} hint="delivered GPU-s charged, last 24h" />
                        <Stat label="Unbilled all" value={fmtSecs(t.paid_all - t.billed_all)}
                            hint="paid but not charged: failures + chat/spec platform jobs" />
                    </div>
                </div>
            </div>
        </div>
    )
}

export default AdminPanel
