import React from 'react'

export const fmtSecs = (s: number): string =>
    s >= 3600 ? `${(s / 3600).toFixed(1)}h` : `${Math.round(s)}s`

export const fmtAge = (s: number | null): string =>
    s == null ? '—' : s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`

export const fmtClock = (ts: number): string =>
    new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

export const fmtStamp = (ts: number): string =>
    new Date(ts * 1000).toLocaleString([], { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })

export const fmtUsd = (n: number): string => `$${n.toFixed(2)}`

export const fmtHours = (s: number): string => `${(s / 3600).toFixed(2)}h`

export const shortGpu = (gpu: string): string => gpu.replace(/^NVIDIA (GeForce )?/, '')

export const Stat: React.FC<{ label: string; value: React.ReactNode; hint?: string }> = ({ label, value, hint }) => (
    <div title={hint}>
        <div className="text-slate text-xs font-semibold uppercase tracking-wide">{label}</div>
        <div className="text-bone text-sm font-mono mt-0.5">{value}</div>
    </div>
)

export const Refresh: React.FC<{ busy: boolean; onClick: () => void }> = ({ busy, onClick }) => (
    <button onClick={onClick} disabled={busy}
        className="text-xs text-slate hover:text-bone transition-colors disabled:opacity-40">
        {busy ? 'refreshing…' : 'refresh'}
    </button>
)
