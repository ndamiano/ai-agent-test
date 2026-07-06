import type { AssetRenderer } from '../types'
import { Pill, Row, RawFieldsFallback } from './shared'
import type { Tone } from './shared'

type CombatKind = 'stat' | 'ability' | 'combatant' | 'encounter' | 'status'

// combat.py flattens 5 item shapes (stats/abilities/combatants/encounters/statuses) into ONE
// list with no discriminator field (the backend's `_component_items` just concatenates them in
// that fixed order) — so the card infers which one an item is from its shape: an encounter has
// `combatants`, a combatant has a `stats` array of {stat,value}, an ability declares `targeting`,
// a stat has `role`+`default`, and anything left is a status.
function detectKind(c: Record<string, unknown>): CombatKind {
    if (Array.isArray(c.combatants)) return 'encounter'
    if (Array.isArray(c.stats)) return 'combatant'
    if (c.targeting) return 'ability'
    if (c.role !== undefined && c.default !== undefined) return 'stat'
    return 'status'
}

const KIND_TONE: Record<CombatKind, Tone> = {
    stat: 'gray', ability: 'purple', combatant: 'blue', encounter: 'red', status: 'amber',
}

const endSummary = (end: any) => end && `${end.type}${end.target ? ` → ${end.target}` : ''}`

// Stat block — one card body per combat item shape (ability/combatant/encounter/stat/status),
// keyed off `detectKind`. Numbers and ids read as a compact stat sheet rather than raw JSON.
const EncounterCard: AssetRenderer = ({ asset, editable, onSave }) => {
    const c = asset.content ?? {}
    const kind = detectKind(c)

    return (
        <div>
            <div className="mb-2"><Pill tone={KIND_TONE[kind]}>{kind}</Pill></div>

            {kind === 'stat' && (
                <div className="space-y-0.5">
                    <Row label="role" value={String(c.role)} />
                    <Row label="default" value={String(c.default)} />
                    {(c.min !== undefined || c.max !== undefined) && (
                        <Row label="range" value={`${c.min ?? '−∞'} … ${c.max ?? '∞'}`} />
                    )}
                </div>
            )}

            {kind === 'ability' && (
                <div className="space-y-1.5">
                    {c.name != null && <div className="text-white text-sm font-medium">{String(c.name)}</div>}
                    {Array.isArray(c.cost) && c.cost.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                            {c.cost.map((x: any, i: number) => <Pill key={i} tone="amber">{x.stat} −{x.amount}</Pill>)}
                        </div>
                    )}
                    {c.targeting != null && (
                        <Row label="targeting" value={`${(c.targeting as any).shape ?? '?'} → ${(c.targeting as any).faction ?? '?'}`} />
                    )}
                    {Array.isArray(c.effects) && c.effects.length > 0 && (
                        <div className="space-y-0.5">
                            {c.effects.map((e: any, i: number) => (
                                <div key={i} className="text-[12px] text-gray-300 flex gap-1.5 items-baseline">
                                    <span className="text-purple-300">▸</span>
                                    <span>{e.stat}</span>
                                    <span className="text-gray-500">{e.op}</span>
                                    {e.formula && <span className="font-mono text-gray-500 text-[11px]">{JSON.stringify(e.formula)}</span>}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {kind === 'combatant' && (
                <div className="space-y-1.5">
                    {c.character != null && <Row label="character" value={String(c.character)} />}
                    {Array.isArray(c.stats) && c.stats.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                            {c.stats.map((s: any, i: number) => <Pill key={i}>{s.stat}: {s.value}</Pill>)}
                        </div>
                    )}
                    {Array.isArray(c.abilities) && c.abilities.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                            {c.abilities.map((a: string) => <Pill key={a} tone="purple">{a}</Pill>)}
                        </div>
                    )}
                    {c.xp_yield !== undefined && <Row label="xp_yield" value={String(c.xp_yield)} />}
                </div>
            )}

            {kind === 'encounter' && (
                <div className="space-y-1.5">
                    {c.background != null && <Row label="background" value={String(c.background)} />}
                    {Array.isArray(c.combatants) && c.combatants.length > 0 && (
                        <div className="space-y-0.5">
                            {c.combatants.map((cb: any, i: number) => (
                                <div key={i} className="text-[12px] flex gap-1.5 items-baseline">
                                    <span className="text-purple-300">▸</span>
                                    <span className="font-mono text-gray-300">{cb.ref}</span>
                                    {cb.faction && <Pill tone={cb.faction === 'player' ? 'blue' : 'red'}>{cb.faction}</Pill>}
                                </div>
                            ))}
                        </div>
                    )}
                    {c.victory != null && <Row label="victory" value={<span className="font-mono text-[11px]">{JSON.stringify(c.victory)}</span>} />}
                    {endSummary(c.on_victory) && <Row label="on_victory" value={endSummary(c.on_victory)} />}
                    {endSummary(c.on_defeat) && <Row label="on_defeat" value={endSummary(c.on_defeat)} />}
                </div>
            )}

            {kind === 'status' && (
                <div className="space-y-1.5">
                    {c.name != null && <div className="text-white text-sm font-medium">{String(c.name)}</div>}
                    {c.blocks_action === true && <Pill tone="red">blocks action</Pill>}
                    {Array.isArray(c.tick) && c.tick.length > 0 && (
                        <div className="space-y-0.5">
                            {c.tick.map((e: any, i: number) => (
                                <div key={i} className="text-[12px] text-gray-300 flex gap-1.5">
                                    <span>{e.stat}</span><span className="text-gray-500">{e.op}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            <RawFieldsFallback asset={asset} editable={editable} onSave={onSave} />
        </div>
    )
}

export default EncounterCard
