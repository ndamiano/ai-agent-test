import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { AdminAnalytics } from '../../types'
import { Refresh } from './format'

// Usage is fetched once and on demand, like costs — nobody sizes a fleet off page views.
const UsagePage: React.FC = () => {
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
                <Refresh busy={busy} onClick={load} />
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

export default UsagePage
