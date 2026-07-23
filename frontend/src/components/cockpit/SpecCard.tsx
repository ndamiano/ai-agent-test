import React, { useState } from 'react'
import type { GameDetail } from '../../types'
import { Badge } from './Badge'

// `design` is freeform JSON, but the drafts cluster around a known vocabulary (genre/entities/
// controls/mechanics/win-lose/…). Render those in a deliberate order as titled sections; anything
// unrecognized still renders after, and the whole raw object stays one click away. This replaces the
// old undifferentiated recursive dump — the "giant JSON blob" the build page used to be.
const PREFERRED = [
    'pitch', 'summary', 'genre', 'setting', 'player', 'entities', 'enemies',
    'controls', 'mechanics', 'objectives', 'goal', 'win', 'lose', 'win_lose',
    'progression', 'levels', 'notes',
]

const orderKeys = (keys: string[]): string[] => {
    const known = PREFERRED.filter(k => keys.includes(k))
    const rest = keys.filter(k => !PREFERRED.includes(k)).sort()
    return [...known, ...rest]
}

const label = (k: string) => k.replace(/[_-]/g, ' ')

const isScalarArray = (v: any[]): boolean => v.every(x => x == null || typeof x !== 'object')

const Chip: React.FC<{ children: React.ReactNode }> = ({ children }) => (
    <span className="inline-block px-2 py-0.5 rounded bg-white/[0.06] text-gray-300 text-xs">{children}</span>
)

const SpecValue: React.FC<{ value: any }> = ({ value }) => {
    if (value == null || value === '') return <span className="text-gray-600 italic">—</span>

    if (Array.isArray(value)) {
        if (value.length === 0) return <span className="text-gray-600 italic">—</span>
        if (isScalarArray(value)) {
            return (
                <div className="flex flex-wrap gap-1.5">
                    {value.map((v, i) => <Chip key={i}>{String(v)}</Chip>)}
                </div>
            )
        }
        return (
            <div className="space-y-1.5">
                {value.map((v, i) => (
                    <div key={i} className="bg-black/20 border border-white/[0.05] rounded px-2.5 py-1.5">
                        <SpecValue value={v} />
                    </div>
                ))}
            </div>
        )
    }

    if (typeof value === 'object') {
        const entries = Object.entries(value)
        if (entries.length === 0) return <span className="text-gray-600 italic">—</span>
        return (
            <div className="space-y-1">
                {entries.map(([k, v]) => (
                    <div key={k} className="flex gap-2 text-sm">
                        <span className="text-gray-500 shrink-0 capitalize">{label(k)}</span>
                        <div className="text-gray-300 min-w-0"><SpecValue value={v} /></div>
                    </div>
                ))}
            </div>
        )
    }

    return <span className="text-gray-300 text-sm whitespace-pre-wrap">{String(value)}</span>
}

export const SpecCard: React.FC<{ spec: GameDetail['spec'] }> = ({ spec }) => {
    const [showRaw, setShowRaw] = useState(false)
    const design = spec.design ?? {}
    const keys = orderKeys(Object.keys(design))

    return (
        <section className="space-y-3">
            <div className="flex items-center gap-2">
                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Spec</h3>
                <Badge label={spec.mode.toUpperCase()} tone="blue" />
                {spec.frozen ? <Badge label="frozen" tone="blue" /> : <Badge label="draft" tone="gray" />}
            </div>

            {spec.request && (
                <div className="border-l-2 border-white/[0.1] pl-3 text-gray-400 text-sm italic">{spec.request}</div>
            )}

            {keys.length === 0 ? (
                <div className="text-gray-600 text-xs italic">no design detail yet</div>
            ) : (
                <div className="grid sm:grid-cols-2 gap-2.5">
                    {keys.map(key => (
                        <div key={key} className="bg-[#1a1a1a] border border-white/[0.06] rounded-lg px-3 py-2.5 space-y-1.5">
                            <div className="text-gray-500 text-[10px] font-semibold uppercase tracking-wide capitalize">{label(key)}</div>
                            <SpecValue value={design[key]} />
                        </div>
                    ))}
                </div>
            )}

            <div>
                <button onClick={() => setShowRaw(v => !v)}
                    className="text-gray-500 hover:text-gray-300 text-[11px] font-medium">
                    {showRaw ? '▾ hide raw JSON' : '▸ raw JSON'}
                </button>
                {showRaw && (
                    <pre className="mt-1.5 bg-black/40 border border-white/[0.06] rounded p-2.5 text-[11px] text-gray-400 overflow-x-auto">
                        {JSON.stringify(spec.design ?? {}, null, 2)}
                    </pre>
                )}
            </div>

            <p className="text-gray-600 text-[11px]">Read-only — ask Maestro in chat to amend the spec before freezing.</p>
        </section>
    )
}
