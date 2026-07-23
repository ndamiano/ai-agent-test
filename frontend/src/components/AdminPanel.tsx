import React, { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { AdminQueues, QueueRow } from '../types'

const POLL_MS = 5000

// GPU-seconds are the only unit here (no dollar model yet). Compact to hours past an hour so a
// fleet's all-time spend stays readable; raw seconds below that, where the queue backlog lives.
export const fmtSecs = (s: number): string =>
    s >= 3600 ? `${(s / 3600).toFixed(1)}h` : `${Math.round(s)}s`

const fmtAge = (s: number | null): string =>
    s == null ? '—' : s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`

const Stat: React.FC<{ label: string; value: React.ReactNode; hint?: string }> = ({ label, value, hint }) => (
    <div title={hint}>
        <div className="text-gray-500 text-[10px] font-semibold uppercase tracking-wide">{label}</div>
        <div className="text-gray-200 text-sm font-mono mt-0.5">{value}</div>
    </div>
)

const QueueCard: React.FC<{ row: QueueRow }> = ({ row }) => {
    const active = row.pending + row.claimed > 0
    return (
        <div className="bg-[#141414] border border-white/[0.06] rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
                <span className="text-white font-semibold text-sm capitalize">{row.queue}</span>
                <span className={`text-[11px] font-mono ${active ? 'text-amber-400' : 'text-gray-500'}`}>
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

    if (error) return <div className="p-6 text-sm text-red-400">{error}</div>
    if (!data) return <div className="p-6 text-sm text-gray-500">Loading queue stats…</div>

    const t = data.totals
    return (
        <div className="h-full overflow-y-auto p-6">
            <div className="max-w-4xl mx-auto space-y-4">
                <div className="flex items-baseline justify-between">
                    <h2 className="text-white font-semibold text-sm">Inference queues</h2>
                    <span className="text-gray-500 text-[11px]">GPU-seconds · refreshes every {POLL_MS / 1000}s</span>
                </div>

                <div className="grid gap-3">
                    {data.queues.map(q => <QueueCard key={q.queue} row={q} />)}
                </div>

                <div className="bg-[#0f0f0f] border border-white/[0.06] rounded-lg p-4">
                    <div className="text-gray-400 text-xs font-semibold mb-3">Fleet totals</div>
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
