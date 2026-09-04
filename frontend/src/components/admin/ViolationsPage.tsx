import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { AdminViolation } from '../../types'
import { Refresh } from './format'

// Safety refusals, newest first — the repeat-offender view. Rows carry only the matched terms.
const ViolationsPage: React.FC = () => {
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
                <Refresh busy={busy} onClick={load} />
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

export default ViolationsPage
