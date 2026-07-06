import type { AssetRenderer } from '../types'
import { Pill, RawFieldsFallback } from './shared'
import type { Tone } from './shared'

const ROLE_TONE: Record<string, Tone> = { protagonist: 'blue', antagonist: 'red', npc: 'gray' }

// Portrait (initial avatar, tinted by the character's authored `color`) + name/role + bio traits
// (voice/temperament/drive) + history/competencies/example lines + expression list — porting
// GamesPanel's CharacterCard read treatment (voice/description/appearance) but against the
// richer cast.py schema (role/temperament/drive/history/competencies/example_lines/color).
const CharacterCard: AssetRenderer = ({ asset, editable, onSave }) => {
    const c = asset.content ?? {}
    const history: string[] = Array.isArray(c.history) ? c.history : []
    const competencies: string[] = Array.isArray(c.competencies) ? c.competencies : []
    const exampleLines: string[] = Array.isArray(c.example_lines) ? c.example_lines : []
    const expressions: string[] = c.expressions && typeof c.expressions === 'object' ? Object.keys(c.expressions) : []
    const initial = String(c.name || c.id || '?').slice(0, 1).toUpperCase()

    return (
        <div>
            <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-sm font-semibold border border-white/[0.08]"
                    style={{ background: c.color ? `${c.color}26` : undefined, color: c.color || undefined }}>
                    {initial}
                </div>
                <div className="min-w-0">
                    <div className="text-white text-sm font-medium truncate">{c.name || c.id}</div>
                    <div className="flex gap-1.5 mt-0.5">
                        {c.role && <Pill tone={ROLE_TONE[c.role] ?? 'gray'}>{c.role}</Pill>}
                        {c.sex && <Pill>{c.sex}</Pill>}
                    </div>
                </div>
            </div>

            <div className="mt-2 space-y-1">
                {c.voice && <p className="text-[12px]"><span className="text-gray-500">voice: </span><span className="text-gray-300">{c.voice}</span></p>}
                {c.temperament && <p className="text-[12px]"><span className="text-gray-500">temperament: </span><span className="text-gray-300">{c.temperament}</span></p>}
                {c.drive && <p className="text-[12px]"><span className="text-gray-500">drive: </span><span className="text-gray-300">{c.drive}</span></p>}
            </div>

            {history.length > 0 && (
                <div className="mt-2">
                    <div className="text-gray-500 text-[10px] uppercase tracking-wide">History</div>
                    <ul className="list-disc list-inside text-gray-300 text-[12px] space-y-0.5">
                        {history.map((h, i) => <li key={i}>{h}</li>)}
                    </ul>
                </div>
            )}

            {competencies.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                    {competencies.map((s, i) => <Pill key={i} tone="purple">{s}</Pill>)}
                </div>
            )}

            {exampleLines.length > 0 && (
                <div className="mt-2 space-y-0.5">
                    {exampleLines.map((l, i) => <p key={i} className="text-gray-400 text-[12px] italic">"{l}"</p>)}
                </div>
            )}

            {expressions.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1">
                    <span className="text-gray-600 text-[10px] uppercase tracking-wide mr-1">expressions:</span>
                    {expressions.map(e => <Pill key={e} tone="green">{e}</Pill>)}
                </div>
            )}

            <RawFieldsFallback asset={asset} editable={editable} onSave={onSave} />
        </div>
    )
}

export default CharacterCard
