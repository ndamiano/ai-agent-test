import React from 'react'
import type { Asset } from '../../../types'
import SchemaCard from '../SchemaCard'

export type Tone = 'blue' | 'gray' | 'amber' | 'green' | 'purple' | 'red'

const TONES: Record<Tone, string> = {
    blue: 'bg-blue-500/15 text-blue-300',
    gray: 'bg-white/[0.06] text-gray-400',
    amber: 'bg-amber-500/15 text-amber-300',
    green: 'bg-emerald-500/15 text-emerald-300',
    purple: 'bg-purple-500/15 text-purple-300',
    red: 'bg-red-500/15 text-red-300',
}

// A small pill, the common unit every bespoke card uses for badges (role/kind/emotion/faction/…).
export const Pill: React.FC<{ tone?: Tone; children: React.ReactNode }> = ({ tone = 'gray', children }) => (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium whitespace-nowrap ${TONES[tone]}`}>{children}</span>
)

// One label: value line, the common shape a stat block/detail row takes across cards.
export const Row: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
    <p className="text-[12px]"><span className="text-gray-500">{label}: </span><span className="text-gray-300">{value}</span></p>
)

// Every bespoke renderer's edit story: a pretty read-only view up top, and — always, since
// SchemaCard itself no-ops the edit affordances when `editable` is false — the generic
// per-field editor collapsed underneath, so no field the pretty view doesn't have a dedicated
// control for is ever unreachable. This is the "fall back to SchemaCard's field editor for the
// raw content beneath your pretty read view" contract every D4 renderer satisfies the same way.
export const RawFieldsFallback: React.FC<{
    asset: Asset
    editable: boolean
    onSave: (content: Record<string, any>) => Promise<void>
}> = ({ asset, editable, onSave }) => (
    <details className="mt-2.5 pt-2 border-t border-white/[0.05]">
        <summary className="text-gray-600 hover:text-gray-400 text-[10px] uppercase tracking-wide cursor-pointer select-none">
            Raw fields
        </summary>
        <div className="mt-2">
            <SchemaCard asset={asset} editable={editable} onSave={onSave} />
        </div>
    </details>
)
