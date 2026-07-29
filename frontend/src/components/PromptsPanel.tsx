import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { PromptBucket, PromptDetail, PromptMessage, PromptScope, PromptTurn } from '../types'
import { Badge } from './cockpit/Badge'
import type { Tone } from './cockpit/Badge'

// The prompt FILE a turn came from isn't recorded, so the system prompt's first line is the
// closest thing to a turn's name.
export const turnLabel = (turn: PromptTurn): string => {
    const head = (turn.system_head || '').trim().split('\n')[0]
    return head || '(no system prompt)'
}

export const bucketLabel = (b: PromptBucket): string =>
    b.game_id === null ? 'Platform (chat + spec drafts)' : (b.title || b.game_id)

// The server's default index cap, mirrored so a truncated listing says so rather than reading as
// the whole log.
const TURN_CAP = 2000

const STATUS_TONE: Record<string, Tone> = {
    done: 'green', failed: 'red', pending: 'gray', claimed: 'amber',
}

const ROLE_TONE: Record<string, string> = {
    user: 'text-blue-300', assistant: 'text-green-300', tool: 'text-amber-300',
}

const clock = (t: number) => new Date(t * 1000).toLocaleTimeString()
const day = (t: number) => new Date(t * 1000).toLocaleDateString()
const kb = (chars: number | null) => chars == null ? '—' : `${Math.round(chars / 1024)}KB`

const Section: React.FC<{ title: string; sub?: string; body: string; open?: boolean; tone?: string }> =
    ({ title, sub, body, open = true, tone }) => {
        const [show, setShow] = useState(open)
        const [copied, setCopied] = useState(false)
        const copy = async (e: React.MouseEvent) => {
            e.stopPropagation()
            await navigator.clipboard?.writeText(body)
            setCopied(true)
            setTimeout(() => setCopied(false), 1200)
        }
        return (
            <div className="border border-white/[0.06] rounded">
                <div className="flex items-center gap-2 px-2.5 py-1.5 hover:bg-white/[0.03]">
                    <button onClick={() => setShow(s => !s)} className="flex items-center gap-2 flex-1 text-left min-w-0">
                        <span className="text-gray-500 text-[10px]">{show ? '▾' : '▸'}</span>
                        <span className="text-gray-300 text-[11px] font-semibold uppercase tracking-wide">{title}</span>
                        {sub && <span className="text-gray-600 text-[10px] font-mono truncate">{sub}</span>}
                    </button>
                    <button onClick={copy} title="copy this block"
                        className="text-gray-600 hover:text-gray-300 text-[10px] flex-shrink-0">
                        {copied ? 'copied' : 'copy'}
                    </button>
                </div>
                {show && (
                    <pre className={`mx-2.5 mb-2.5 bg-black/40 rounded px-2.5 py-2 text-[11px] leading-relaxed font-mono whitespace-pre-wrap break-words max-h-[60vh] overflow-y-auto ${tone || 'text-gray-300'}`}>
                        {body}
                    </pre>
                )}
            </div>
        )
    }

const messageTitle = (msg: PromptMessage, i: number) =>
    `${i + 1}. ${msg.role}${msg.kind === 'text' ? '' : ` · ${msg.kind}`}`

const TurnDetail: React.FC<{ detail: PromptDetail }> = ({ detail }) => {
    const usage = detail.response?.usage || {}
    const whole = [detail.system, ...detail.messages.map(m => m.text)].join('\n\n---\n\n')
    return (
        <div className="space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
                <Badge label={detail.stage || 'llm'} tone="blue" />
                <Badge label={detail.status} tone={STATUS_TONE[detail.status] || 'gray'} />
                <span className="text-gray-500 text-[11px] font-mono">{detail.model}</span>
                {detail.reasoning && <span className="text-gray-500 text-[11px]">reasoning: {detail.reasoning}</span>}
                {detail.exec_seconds != null && <span className="text-gray-500 text-[11px]">{detail.exec_seconds.toFixed(1)}s</span>}
                {usage.prompt_tokens != null && (
                    <span className="text-gray-500 text-[11px]">{usage.prompt_tokens} in / {usage.completion_tokens} out</span>
                )}
                <span className="text-gray-600 text-[10px] font-mono ml-auto">{detail.id}</span>
            </div>

            {detail.error && <div className="text-red-400 text-[11px] font-mono">{detail.error}</div>}

            <Section title="Whole prompt" sub="system + every message, as sent" body={whole} open={false} />
            <Section title="System prompt" sub={`${detail.system.length} chars`} body={detail.system || '(none)'} />

            {detail.messages.map((m, i) => (
                <Section key={i} title={messageTitle(m, i)} sub={m.name || `${m.text.length} chars`}
                    body={m.text} tone={ROLE_TONE[m.role]} />
            ))}

            {detail.tools.length > 0 && (
                <Section title="Tools offered" sub={detail.tools.map(t => t.name).join(', ')}
                    body={JSON.stringify(detail.tools, null, 2)} open={false} />
            )}

            {detail.response ? (<>
                {detail.response.text && <Section title="Response" body={detail.response.text} />}
                {detail.response.tool_calls.map((tc, i) => (
                    <Section key={i} title={`Response · tool call → ${tc.name}`} body={tc.arguments} tone="text-amber-300" />
                ))}
            </>) : (
                <div className="text-gray-600 text-[11px]">nothing came back for this turn</div>
            )}
        </div>
    )
}

const BucketList: React.FC<{
    buckets: PromptBucket[]
    scope: PromptScope
    gameId: string | null
    onPick: (scope: PromptScope, gameId: string | null) => void
}> = ({ buckets, scope, gameId, onPick }) => {
    const [q, setQ] = useState('')
    const rows = useMemo(() => {
        const needle = q.trim().toLowerCase()
        if (!needle) return buckets
        return buckets.filter(b => bucketLabel(b).toLowerCase().includes(needle) ||
            (b.game_id || '').toLowerCase().includes(needle))
    }, [buckets, q])
    const total = buckets.reduce((n, b) => n + b.turns, 0)

    return (
        <div className="w-64 flex-shrink-0 border-r border-white/[0.06] flex flex-col min-h-0">
            <div className="px-2.5 py-2 flex-shrink-0">
                <input value={q} onChange={e => setQ(e.target.value)} placeholder="filter games…"
                    className="w-full bg-black/40 border border-white/[0.1] rounded text-[11px] text-gray-200 px-2 py-1" />
            </div>
            <button onClick={() => onPick('all', null)}
                className={`text-left px-2.5 py-1.5 border-b border-white/[0.04] ${scope === 'all' ? 'bg-white/[0.07]' : 'hover:bg-white/[0.03]'}`}>
                <div className="text-gray-200 text-[11px]">All turns</div>
                <div className="text-gray-600 text-[10px] font-mono">{total} across {buckets.length} buckets</div>
            </button>
            <div className="flex-1 overflow-y-auto">
                {rows.map(b => {
                    const active = b.game_id === null ? scope === 'platform' : (scope === 'game' && gameId === b.game_id)
                    return (
                        <button key={b.game_id ?? 'platform'}
                            onClick={() => onPick(b.game_id === null ? 'platform' : 'game', b.game_id)}
                            className={`w-full text-left px-2.5 py-1.5 border-b border-white/[0.04] ${active ? 'bg-white/[0.07]' : 'hover:bg-white/[0.03]'}`}>
                            <div className="text-gray-200 text-[11px] truncate">{bucketLabel(b)}</div>
                            <div className="text-gray-600 text-[10px] font-mono flex gap-1.5">
                                <span>{b.turns} turns</span>
                                {b.mode && <span>{b.mode}</span>}
                                <span className="ml-auto">{day(b.last_at)}</span>
                            </div>
                        </button>
                    )
                })}
            </div>
        </div>
    )
}

// A turn keeps its ordinal in the whole bucket, so a row reads the same whether the list is
// filtered, grouped, or neither.
export interface IndexedTurn extends PromptTurn { index: number }

export interface TurnGroup {
    hash: string
    label: string
    systemChars: number
    execSeconds: number
    turns: IndexedTurn[]
}

// Group by the system prompt — one group per prompt the build actually used (measured: a 661-turn
// build ran on 8 distinct system prompts). First-appearance order, so newest-first turns yield
// newest-first groups.
export const groupTurns = (turns: IndexedTurn[]): TurnGroup[] => {
    const by = new Map<string, TurnGroup>()
    for (const t of turns) {
        let g = by.get(t.system_hash)
        if (!g) {
            g = { hash: t.system_hash, label: turnLabel(t), systemChars: t.system_chars, execSeconds: 0, turns: [] }
            by.set(t.system_hash, g)
        }
        g.turns.push(t)
        g.execSeconds += t.exec_seconds ?? 0
    }
    return [...by.values()]
}

const TurnRow: React.FC<{
    turn: IndexedTurn
    index: number
    selected: boolean
    onPick: (id: string) => void
    indent?: boolean
}> = ({ turn, index, selected, onPick, indent }) => (
    <button onClick={() => onPick(turn.id)}
        className={`w-full text-left py-1.5 pr-2.5 border-b border-white/[0.04] ${indent ? 'pl-5' : 'pl-2.5'} ${selected ? 'bg-white/[0.07]' : 'hover:bg-white/[0.03]'}`}>
        <div className="flex items-center gap-1.5">
            <span className="text-gray-600 text-[10px] font-mono w-7">{index}</span>
            <span className="text-gray-400 text-[10px]">{turn.metadata?.stage || 'llm'}</span>
            <span className="text-gray-600 text-[10px] font-mono ml-auto">{kb(turn.payload_chars)}</span>
            {turn.status !== 'done' && <span className="text-amber-400 text-[10px]">{turn.status}</span>}
        </div>
        <div className="text-gray-300 text-[11px] truncate">{turnLabel(turn)}</div>
        <div className="text-gray-600 text-[10px] font-mono">
            {clock(turn.created_at)} · {turn.n_messages ?? 0} msg
            {turn.exec_seconds != null ? ` · ${turn.exec_seconds.toFixed(1)}s` : ''}
        </div>
    </button>
)

const PromptsPanel: React.FC = () => {
    const [buckets, setBuckets] = useState<PromptBucket[]>([])
    const [scope, setScope] = useState<PromptScope>('all')
    const [gameId, setGameId] = useState<string | null>(null)
    const [turns, setTurns] = useState<PromptTurn[]>([])
    const [selected, setSelected] = useState<string | null>(null)
    const [detail, setDetail] = useState<PromptDetail | null>(null)
    const [filter, setFilter] = useState('')
    const [grouped, setGrouped] = useState(true)
    const [open, setOpen] = useState<Set<string>>(new Set())
    const [error, setError] = useState<string | null>(null)
    const [loading, setLoading] = useState(false)

    const fail = (e: unknown, what: string) =>
        setError(e instanceof ApiError && e.status === 403 ? 'Admin access required.' : `Failed to load ${what}.`)

    const loadBuckets = useCallback(() => {
        api.getPromptBuckets().then(b => { setBuckets(b); setError(null) }).catch(e => fail(e, 'the game list'))
    }, [])

    useEffect(() => { loadBuckets() }, [loadBuckets])

    useEffect(() => {
        let cancelled = false
        setSelected(null)
        api.getPromptTurns(scope, gameId)
            .then(t => { if (!cancelled) { setTurns(t); setError(null) } })
            .catch(e => { if (!cancelled) fail(e, 'the turns') })
        return () => { cancelled = true }
    }, [scope, gameId])

    useEffect(() => {
        if (!selected) { setDetail(null); return }
        let cancelled = false
        setLoading(true)
        api.getPromptTurn(selected)
            .then(d => { if (!cancelled) { setDetail(d); setError(null) } })
            .catch(e => { if (!cancelled) fail(e, 'that turn') })
            .finally(() => { if (!cancelled) setLoading(false) })
        return () => { cancelled = true }
    }, [selected])

    const rows = useMemo(() => {
        // The ordinal is taken before the reverse, so a row still says where it sits in the build.
        const indexed: IndexedTurn[] = turns.map((t, i) => ({ ...t, index: i + 1 })).reverse()
        const q = filter.trim().toLowerCase()
        if (!q) return indexed
        return indexed.filter(t =>
            turnLabel(t).toLowerCase().includes(q) ||
            (t.metadata?.stage || '').toLowerCase().includes(q) ||
            (t.model || '').toLowerCase().includes(q) ||
            (t.build_id || '').toLowerCase().includes(q))
    }, [turns, filter])

    const groups = useMemo(() => groupTurns(rows), [rows])

    // One group is no grouping — open it rather than making the reader click through a single header.
    useEffect(() => { setOpen(groups.length === 1 ? new Set([groups[0].hash]) : new Set()) }, [groups])

    const toggleGroup = (hash: string) => setOpen(prev => {
        const next = new Set(prev)
        if (!next.delete(hash)) next.add(hash)
        return next
    })

    const pick = (nextScope: PromptScope, nextGame: string | null) => {
        setScope(nextScope); setGameId(nextGame)
    }

    return (
        <div className="h-full flex min-h-0">
            <BucketList buckets={buckets} scope={scope} gameId={gameId} onPick={pick} />

            <div className="w-80 flex-shrink-0 border-r border-white/[0.06] flex flex-col min-h-0">
                <div className="px-2.5 py-2 flex-shrink-0 flex items-center gap-2">
                    <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="filter turns…"
                        className="flex-1 bg-black/40 border border-white/[0.1] rounded text-[11px] text-gray-200 px-2 py-1" />
                    <button onClick={() => setGrouped(g => !g)} title="group turns by their system prompt"
                        className={`px-1.5 py-1 rounded text-[10px] ${grouped ? 'bg-white/10 text-gray-200' : 'text-gray-500 hover:text-gray-300'}`}>
                        group
                    </button>
                    <button onClick={loadBuckets} title="reload" className="text-gray-500 hover:text-gray-300 text-[11px]">↻</button>
                </div>
                <div className="text-gray-600 text-[10px] px-2.5 pb-1 font-mono">
                    {rows.length} of {turns.length} turns
                    {grouped && ` · ${groups.length} prompts`}
                    {turns.length >= TURN_CAP && ' (newest ' + TURN_CAP + ')'}
                </div>
                <div className="flex-1 overflow-y-auto">
                    {grouped
                        ? groups.map(g => (
                            <div key={g.hash}>
                                <button onClick={() => toggleGroup(g.hash)}
                                    className="w-full text-left px-2.5 py-1.5 bg-white/[0.03] border-b border-white/[0.06] hover:bg-white/[0.06]">
                                    <div className="flex items-center gap-1.5">
                                        <span className="text-gray-500 text-[10px]">{open.has(g.hash) ? '▾' : '▸'}</span>
                                        <span className="text-gray-200 text-[11px] truncate flex-1">{g.label}</span>
                                        <span className="text-gray-500 text-[10px] font-mono">×{g.turns.length}</span>
                                    </div>
                                    <div className="text-gray-600 text-[10px] font-mono pl-3.5">
                                        {/* Two prompts can open with the same line and differ below — the hash is what tells them apart. */}
                                        {g.hash.slice(0, 6)} · {Math.round(g.systemChars / 1024 * 10) / 10}KB system · {g.execSeconds.toFixed(0)}s total
                                    </div>
                                </button>
                                {open.has(g.hash) && g.turns.map(t => (
                                    <TurnRow key={t.id} turn={t} index={t.index} selected={selected === t.id}
                                        onPick={setSelected} indent />
                                ))}
                            </div>
                        ))
                        : rows.map(t => (
                            <TurnRow key={t.id} turn={t} index={t.index} selected={selected === t.id}
                                onPick={setSelected} />
                        ))}
                    {turns.length === 0 && <div className="text-gray-600 text-[11px] px-2.5 py-2">no llm turns here</div>}
                </div>
            </div>

            <div className="flex-1 overflow-y-auto px-3 py-3 min-w-0">
                {error && <div className="text-red-400 text-[11px] mb-2">{error}</div>}
                {loading && <div className="text-gray-500 text-[11px]">loading turn…</div>}
                {detail && <TurnDetail detail={detail} />}
                {!detail && !loading && <div className="text-gray-600 text-[11px]">pick a turn to read its prompt</div>}
            </div>
        </div>
    )
}

export default PromptsPanel
