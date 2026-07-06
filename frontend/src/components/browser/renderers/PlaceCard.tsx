import React from 'react'
import type { AssetRenderer } from '../types'
import { Pill, RawFieldsFallback } from './shared'

type LegendEntry = { role: string; theme?: string }
type Interactable = {
    id: string
    label?: string
    position?: { rect?: unknown; cell?: { x: number; y: number } }
    action?: { type: string }
}

const DEFAULT_LEGEND: Record<string, LegendEntry> = {
    '.': { role: 'open', theme: 'grass' },
    ',': { role: 'open', theme: 'path' },
    '#': { role: 'blocked', theme: 'wall' },
    T: { role: 'blocked', theme: 'tree' },
    '~': { role: 'blocked', theme: 'water' },
    '%': { role: 'blocked', theme: 'rock' },
}

const WALKABLE_KINDS = new Set(['world_map', 'town', 'interior'])

// The tile char-grid a walkable place (world_map/town/interior) authors — monospace, blocked
// tiles dimmed, and any interactable that sits on a cell drawn as its label's initial so the
// legend + hotspots read together at a glance.
const TileGrid: React.FC<{ rows: string[]; legend?: Record<string, LegendEntry>; interactables: Interactable[] }>
    = ({ rows, legend, interactables }) => {
    const byCell = new Map<string, Interactable>()
    for (const it of interactables) {
        const cell = it.position?.cell
        if (cell) byCell.set(`${cell.x},${cell.y}`, it)
    }
    return (
        <div className="overflow-x-auto bg-black/30 rounded p-2">
            <pre className="font-mono text-[11px] leading-[1.15]">
                {rows.map((row, y) => (
                    <div key={y}>
                        {row.split('').map((ch, x) => {
                            const hot = byCell.get(`${x},${y}`)
                            if (hot) {
                                return (
                                    <span key={x} title={hot.label || hot.id} className="text-amber-300 font-bold">
                                        {(hot.label || hot.id || '?').slice(0, 1).toUpperCase()}
                                    </span>
                                )
                            }
                            const entry = legend?.[ch] ?? DEFAULT_LEGEND[ch]
                            const blocked = entry?.role === 'blocked'
                            return <span key={x} className={blocked ? 'text-gray-600' : 'text-gray-400'}>{ch}</span>
                        })}
                    </div>
                ))}
            </pre>
        </div>
    )
}

// The map / layout plan. Walkable RPG places (world_map/town/interior) render the rasterized
// `tiles.rows` char-grid; PnC `room` places have no grid and list their hotspots instead. Both
// kinds show the authored `interactables`, plus the layout plan's `features`/`exits` when present
// (map_builder's inputs, kept alongside the rasterized output on the same place dict).
const PlaceCard: AssetRenderer = ({ asset, editable, onSave }) => {
    const p = asset.content ?? {}
    const interactables: Interactable[] = Array.isArray(p.interactables) ? p.interactables : []
    const features: { id?: string; kind?: string; label?: string }[] = Array.isArray(p.layout?.features) ? p.layout.features : []
    const exits: { id?: string; edge?: string }[] = Array.isArray(p.layout?.exits) ? p.layout.exits : []
    const rows: string[] = Array.isArray(p.tiles?.rows) ? p.tiles.rows : []
    const walkable = WALKABLE_KINDS.has(p.kind) && rows.length > 0

    return (
        <div>
            <div className="flex items-center gap-1.5 mb-2">
                {p.kind && <Pill tone="blue">{p.kind}</Pill>}
                {p.background && <span className="text-gray-600 text-[11px] font-mono">bg: {p.background}</span>}
            </div>

            {walkable && <TileGrid rows={rows} legend={p.tiles?.legend} interactables={interactables} />}

            {interactables.length > 0 ? (
                <div className="mt-2 space-y-1">
                    <div className="text-gray-500 text-[10px] uppercase tracking-wide">Hotspots</div>
                    {interactables.map(it => (
                        <div key={it.id} className="text-[12px] flex gap-1.5 items-baseline">
                            <span className="text-purple-300">▸</span>
                            <span className="text-gray-200">{it.label || it.id}</span>
                            {it.action?.type && <Pill>{it.action.type}</Pill>}
                        </div>
                    ))}
                </div>
            ) : !walkable && (
                <p className="text-gray-600 text-sm">No hotspots.</p>
            )}

            {features.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1">
                    <span className="text-gray-600 text-[10px] uppercase tracking-wide mr-1">features:</span>
                    {features.map((f, i) => <Pill key={f.id ?? i} tone="green">{f.label || f.kind || f.id}</Pill>)}
                </div>
            )}

            {exits.length > 0 && (
                <div className="mt-1 flex flex-wrap items-center gap-1">
                    <span className="text-gray-600 text-[10px] uppercase tracking-wide mr-1">exits:</span>
                    {exits.map((e, i) => <Pill key={e.id ?? i}>{e.edge ?? e.id}</Pill>)}
                </div>
            )}

            <RawFieldsFallback asset={asset} editable={editable} onSave={onSave} />
        </div>
    )
}

export default PlaceCard
