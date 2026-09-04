import React, { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../../api/client'
import type { AdminQueues, QueueRow, Stockouts, WorkerRow } from '../../types'
import { Stat, fmtAge, fmtClock, fmtSecs, fmtStamp, fmtUsd, shortGpu } from './format'

const POLL_MS = 5000

const short = (id: string | null): string => id ? id.slice(0, 8) : '—'

const STATE_CLASS: Record<WorkerRow['state'], string> = {
    busy: 'text-wait', idle: 'text-live', booting: 'text-slate',
}

const NextJobs: React.FC<{ row: QueueRow }> = ({ row }) => (
    <div>
        <div className="text-slate text-xs font-semibold mb-1">
            Next up{row.pending > row.next.length ? ` (${row.next.length} of ${row.pending})` : ''}
        </div>
        {row.next.length === 0
            ? <div className="text-slate text-xs">Nothing pending.</div>
            : <table className="w-full text-xs font-mono">
                <thead>
                    <tr className="text-slate text-left">
                        <th className="pr-3 font-normal">#</th>
                        <th className="pr-3 font-normal">game</th>
                        <th className="pr-3 font-normal">build</th>
                        <th className="pr-3 font-normal">job</th>
                        <th className="font-normal text-right">waiting</th>
                    </tr>
                </thead>
                <tbody className="text-bone">
                    {row.next.map((j, i) => (
                        <tr key={j.id}>
                            <td className="pr-3 text-slate">{i + 1}</td>
                            <td className="pr-3" title={j.game_id ?? 'platform job'}>{short(j.game_id)}</td>
                            <td className="pr-3" title={j.build_id ?? ''}>{short(j.build_id)}</td>
                            <td className="pr-3" title={j.id}>{short(j.id)}</td>
                            <td className="text-right text-wait">{fmtAge(j.waiting_seconds)}</td>
                        </tr>
                    ))}
                </tbody>
            </table>}
    </div>
)

const Workers: React.FC<{ row: QueueRow }> = ({ row }) => (
    <div>
        <div className="text-slate text-xs font-semibold mb-1">Workers</div>
        {row.workers.length === 0
            ? <div className="text-slate text-xs">None.</div>
            : <table className="w-full text-xs font-mono">
                <thead>
                    <tr className="text-slate text-left">
                        <th className="pr-3 font-normal">state</th>
                        <th className="pr-3 font-normal">worker</th>
                        <th className="pr-3 font-normal">gpu</th>
                        <th className="pr-3 font-normal text-right">$/h</th>
                        <th className="pr-3 font-normal text-right">up</th>
                        <th className="pr-3 font-normal text-right">seen</th>
                        <th className="pr-3 font-normal">on</th>
                        <th className="font-normal text-right">for</th>
                    </tr>
                </thead>
                <tbody className="text-bone">
                    {row.workers.map(w => (
                        <tr key={w.id}>
                            <td className={`pr-3 ${STATE_CLASS[w.state]}`}>{w.state}</td>
                            <td className="pr-3" title={`${w.id}${w.pod_id ? ` · pod ${w.pod_id}` : ''} · ${w.source ?? ''}`}>
                                {short(w.id)}
                            </td>
                            <td className="pr-3" title={w.gpu_type ?? ''}>{w.gpu_type ? shortGpu(w.gpu_type) : '—'}</td>
                            <td className="pr-3 text-right">{w.usd_per_hour == null ? '—' : fmtUsd(w.usd_per_hour)}</td>
                            <td className="pr-3 text-right">{fmtAge(w.uptime_seconds)}</td>
                            <td className="pr-3 text-right">{fmtAge(w.last_seen_seconds)}</td>
                            <td className="pr-3" title={w.job ? `job ${w.job.id}${w.job.build_id ? ` · build ${w.job.build_id}` : ''}` : ''}>
                                {w.job ? short(w.job.game_id) : '—'}
                            </td>
                            <td className="text-right text-wait">{w.job ? fmtAge(w.job.running_seconds) : ''}</td>
                        </tr>
                    ))}
                </tbody>
            </table>}
    </div>
)

// Shows nothing at all when the provider has never refused for stock: the line exists to make a
// stock-out visible, not to reassure.
export const StockoutLines: React.FC<{ s: Stockouts }> = ({ s }) => {
    if (s.last_7d === 0 && !s.active && s.totals_all.attempts === 0) return null
    const ratio = (t: Stockouts['totals_all']) => `${t.stock_refusals.toLocaleString()} of ${t.attempts.toLocaleString()} pod requests refused for stock`
    return (
        <div className="space-y-1 text-xs">
            {s.active && s.active_since != null && (
                <div className="text-fail font-semibold" title={s.last_error ?? ''}>
                    Out of stock now — {s.active_count} refusal{s.active_count === 1 ? '' : 's'} since {fmtClock(s.active_since)}
                    {s.last_error ? ` (last: '${s.last_error}')` : ''}
                </div>
            )}
            {s.last_7d > 0 && s.last_at != null && (
                <div className="text-slate" title={`${s.last_1h} in the last hour · ${s.last_24h} in the last 24h`}>
                    {s.last_7d} stock-out{s.last_7d === 1 ? '' : 's'} in the last 7 days, last {fmtStamp(s.last_at)}
                </div>
            )}
            {s.totals_all.attempts > 0 && (
                <div className="text-slate" title={`other refusals: ${s.totals_60d.other_refusals} in 60 days · ${s.totals_all.other_refusals} all time`}>
                    Last 60 days: {ratio(s.totals_60d)} · all time since {s.totals_all.since}: {ratio(s.totals_all)}
                </div>
            )}
        </div>
    )
}

const QueueCard: React.FC<{ row: QueueRow }> = ({ row }) => {
    const active = row.pending + row.claimed > 0
    return (
        <div className="bg-panel border border-edge rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between">
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
            </div>
            <StockoutLines s={row.stockouts} />
            <div className="overflow-x-auto"><NextJobs row={row} /></div>
            <div className="overflow-x-auto"><Workers row={row} /></div>
        </div>
    )
}

const QueuesPage: React.FC = () => {
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

    if (error) return <div className="text-sm text-fail">{error}</div>
    if (!data) return <div className="text-sm text-slate">Loading queue stats…</div>

    const t = data.totals
    return (
        <div className="space-y-4">
            <div className="flex items-baseline justify-between">
                <h2 className="text-white font-semibold text-sm">Inference queues</h2>
                <span className="text-slate text-xs">refreshes every {POLL_MS / 1000}s</span>
            </div>

            <div className="grid gap-3">
                {data.queues.map(q => <QueueCard key={q.queue} row={q} />)}
            </div>

            <div className="bg-ink border border-edge rounded-lg p-4">
                <div className="text-slate text-xs font-semibold mb-3">Fleet totals</div>
                <div className="grid grid-cols-4 gap-y-3 gap-x-2">
                    <Stat label="In queue" value={t.pending + t.claimed} hint="pending + claimed, all queues" />
                    <Stat label="Workers" value={t.workers_live} hint="live workers, all queues" />
                    <Stat label="Backlog" value={fmtSecs(t.backlog_seconds)} hint="projected GPU-s to clear everything" />
                </div>
            </div>
        </div>
    )
}

export default QueuesPage
