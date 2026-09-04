import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { AdminCosts, CostWindow } from '../../types'
import { Refresh, fmtHours, fmtSecs, fmtUsd, shortGpu } from './format'

const usd = (n: number | null): string => n == null ? '—' : fmtUsd(n)
const hours = (s: number | null): string => s == null ? '—' : fmtHours(s)

const WindowTable: React.FC<{ w: CostWindow }> = ({ w }) => {
    const alive = w.gpus.reduce((a, g) => a + (g.alive_usd ?? 0), 0)
    const worked = w.gpus.reduce((a, g) => a + g.worked_usd, 0)
    const aliveSecs = w.gpus.reduce((a, g) => a + (g.alive_seconds ?? 0), 0)
    const workedSecs = w.gpus.reduce((a, g) => a + g.worked_seconds, 0)
    const anyAlive = w.gpus.some(g => g.alive_seconds != null)
    return (
        <div className="bg-ink border border-edge rounded-lg p-4 space-y-3">
            <div className="flex items-baseline justify-between">
                <div className="text-white font-semibold text-sm">{w.label}</div>
                <div className="text-xs text-slate font-mono">
                    {w.games.n} game{w.games.n === 1 ? '' : 's'}
                    {w.games.avg_gpu_hours != null && ` · ${fmtHours(w.games.avg_gpu_hours * 3600)} GPU/game`}
                    {w.games.avg_usd != null && ` · ${fmtUsd(w.games.avg_usd)}/game`}
                    {w.games.avg_changes != null && ` · ${w.games.avg_changes} changes/game`}
                </div>
            </div>
            {w.gpus.length === 0
                ? <div className="text-slate text-xs">No GPU time in this window.</div>
                : <div className="overflow-x-auto">
                    <table className="w-full text-sm font-mono">
                        <thead>
                            <tr className="text-slate text-xs text-left">
                                <th className="font-semibold pb-1 pr-3">gpu</th>
                                <th className="font-semibold pb-1 pr-3 text-right" title="pod wall-clock RunPod billed">alive</th>
                                <th className="font-semibold pb-1 pr-3 text-right" title="what RunPod charged for it">cost</th>
                                <th className="font-semibold pb-1 pr-3 text-right" title="seconds our jobs ran on the card">worked</th>
                                <th className="font-semibold pb-1 pr-3 text-right" title="worked hours at our rate table — the cost with zero overhead">cost</th>
                                <th className="font-semibold pb-1 text-right" title="worked ÷ alive">util</th>
                            </tr>
                        </thead>
                        <tbody className="text-bone">
                            {w.gpus.map(g => (
                                <tr key={g.gpu}>
                                    <td className="py-0.5 pr-3" title={g.gpu}>{shortGpu(g.gpu)}</td>
                                    <td className="pr-3 text-right">{hours(g.alive_seconds)}</td>
                                    <td className="pr-3 text-right">{usd(g.alive_usd)}</td>
                                    <td className="pr-3 text-right">{fmtHours(g.worked_seconds)}</td>
                                    <td className="pr-3 text-right">{fmtUsd(g.worked_usd)}</td>
                                    <td className="text-right">
                                        {g.alive_seconds ? `${Math.round(g.worked_seconds / g.alive_seconds * 100)}%` : '—'}
                                    </td>
                                </tr>
                            ))}
                            {w.gpus.length > 1 && (
                                <tr className="text-slate border-t border-edge">
                                    <td className="py-0.5 pr-3">total</td>
                                    <td className="pr-3 text-right">{anyAlive ? fmtHours(aliveSecs) : '—'}</td>
                                    <td className="pr-3 text-right">{anyAlive ? fmtUsd(alive) : '—'}</td>
                                    <td className="pr-3 text-right">{fmtHours(workedSecs)}</td>
                                    <td className="pr-3 text-right">{fmtUsd(worked)}</td>
                                    <td className="text-right">{aliveSecs ? `${Math.round(workedSecs / aliveSecs * 100)}%` : '—'}</td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>}
        </div>
    )
}

// Fetched once and on demand — the backend caches, but RunPod's ledger is not a thing to lean
// on every five seconds.
const CostsPage: React.FC = () => {
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
        <div className="space-y-4">
            <div className="flex items-baseline justify-between">
                <h2 className="text-white font-semibold text-sm">
                    Effective cost {costs.runpod_reachable ? '' : '— ledger unreachable, our logs only'}
                </h2>
                <Refresh busy={busy} onClick={load} />
            </div>
            <div className="text-xs text-slate">
                alive is pod wall-clock RunPod billed; worked is what our jobs ran, priced at our rate table.
                A game counts in the window its full build finished in, and costs everything it ever ran: design, art, change rounds.
            </div>
            {costs.windows.map(w => <WindowTable key={w.label} w={w} />)}
            {costs.ghost_30d && costs.ghost_30d.pods > 0 && (
                <div className="text-xs text-wait">
                    ghost spend 30d: {fmtUsd(costs.ghost_30d.amount_usd)} across {costs.ghost_30d.pods} pod{costs.ghost_30d.pods === 1 ? '' : 's'} that
                    billed {fmtSecs(costs.ghost_30d.billed_seconds)} and never worked a job
                </div>
            )}
        </div>
    )
}

export default CostsPage
