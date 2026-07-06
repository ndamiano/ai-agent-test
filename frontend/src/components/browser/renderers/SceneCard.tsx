import type { AssetRenderer } from '../types'
import { Pill, RawFieldsFallback } from './shared'

type Line = { speaker?: string | null; emotion?: string; text: string; effects?: unknown[] }
type Choice = { text: string; target: string; requires?: unknown; effects?: unknown[] }
type NodeEnd = { type: string; target?: string; choices?: Choice[]; ending?: string }

// Screenplay view — `NAME [emotion]: line`, menu choices listed below — porting GamesPanel's
// NodeCard read treatment (see the old monolith's NodeCard) into the D4 renderer registry.
// Meaningful structure (lines, end/choices) is read-only here; editing rides the raw-fields
// fallback (SchemaCard), which already handles a node's nested lines/end as JSON.
const SceneCard: AssetRenderer = ({ asset, editable, onSave }) => {
    const content = asset.content ?? {}
    const lines: Line[] = Array.isArray(content.lines) ? content.lines : []
    const end: NodeEnd | undefined = content.end

    return (
        <div>
            {content.location && (
                <div className="text-gray-600 text-[11px] font-mono mb-1.5">location: {content.location}</div>
            )}

            {lines.length === 0 ? (
                <p className="text-gray-600 text-sm">No dialogue lines.</p>
            ) : (
                <div className="space-y-1">
                    {lines.map((ln, i) => (
                        <div key={i} className="text-[13px] leading-snug">
                            <span className={ln.speaker ? 'text-blue-300 font-medium' : 'text-gray-500 italic font-medium'}>
                                {ln.speaker ?? 'NARR'}
                            </span>
                            {ln.emotion && ln.emotion !== 'neutral' && (
                                <span className="text-gray-600 text-[10px]"> [{ln.emotion}]</span>
                            )}
                            <span className="text-gray-300">: {ln.text}</span>
                            {Array.isArray(ln.effects) && ln.effects.length > 0 && (
                                <span className="ml-1.5"><Pill tone="green">fx×{ln.effects.length}</Pill></span>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {end && (
                <div className="mt-2.5 pt-2 border-t border-white/[0.05]">
                    <div className="flex items-center gap-1.5 mb-1">
                        <Pill tone="blue">{end.type}</Pill>
                        {end.target && <span className="font-mono text-gray-400 text-[11px]">→ {end.target}</span>}
                        {end.ending && <span className="text-gray-400 text-[11px]">({end.ending})</span>}
                    </div>
                    {end.type === 'menu' && Array.isArray(end.choices) && (
                        <div className="space-y-1">
                            <div className="text-gray-500 text-[10px] uppercase tracking-wide">Choices</div>
                            {end.choices.map((c, i) => (
                                <div key={i} className="text-[13px] leading-snug flex gap-1.5 items-baseline">
                                    <span className="text-purple-300">▸</span>
                                    <span className="text-gray-200">{c.text}</span>
                                    <span className="text-gray-600 font-mono text-[11px]">→ {c.target}</span>
                                    {c.requires != null && <Pill tone="amber">if</Pill>}
                                    {Array.isArray(c.effects) && c.effects.length > 0 && <Pill tone="green">fx</Pill>}
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

export default SceneCard
