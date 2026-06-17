import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api/client'
import type { Game, GameDetail } from '../types'

const Badge: React.FC<{ label: string; tone: 'green' | 'blue' | 'gray' }> = ({ label, tone }) => {
    const tones = {
        green: 'bg-green-500/15 text-green-400',
        blue: 'bg-blue-500/15 text-blue-400',
        gray: 'bg-white/[0.06] text-gray-400',
    }
    return <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${tones[tone]}`}>{label}</span>
}

const GameDetailView: React.FC<{ runId: string }> = ({ runId }) => {
    const [detail, setDetail] = useState<GameDetail | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        let cancelled = false
        setLoading(true)
        setError(null)
        setDetail(null)
        api.getGame(runId)
            .then(d => { if (!cancelled) setDetail(d) })
            .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load game') })
            .finally(() => { if (!cancelled) setLoading(false) })
        return () => { cancelled = true }
    }, [runId])

    if (loading) return <div className="p-6 text-gray-500 text-sm">Loading…</div>
    if (error) return <div className="p-6 text-red-400 text-sm">Error: {error}</div>
    if (!detail) return null

    const premise = detail.artifact?.premise as Record<string, any> | undefined

    return (
        <div className="h-full overflow-y-auto px-6 py-5 space-y-6">
            <div>
                <div className="flex items-center gap-2">
                    <h2 className="text-white text-lg font-semibold">{detail.spec.title || detail.run_id}</h2>
                    {detail.frozen ? <Badge label="frozen" tone="blue" /> : <Badge label="draft" tone="gray" />}
                    {detail.built ? <Badge label="built" tone="green" /> : null}
                </div>
                {detail.spec.request && <p className="text-gray-400 text-sm mt-1">{detail.spec.request}</p>}
                <p className="text-gray-600 text-xs mt-1 font-mono">{detail.run_id}</p>
            </div>

            {/* Premise concept */}
            {premise && (
                <section className="space-y-3">
                    <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Premise</h3>
                    {premise.central_question && (
                        <p className="text-gray-200 text-sm italic">"{premise.central_question}"</p>
                    )}
                    {Array.isArray(premise.characters) && premise.characters.length > 0 && (
                        <div className="space-y-1">
                            {premise.characters.map((c: any) => (
                                <div key={c.id} className="text-sm">
                                    <span className="text-white font-medium">{c.name}</span>
                                    <span className="text-gray-600"> ({c.id})</span>
                                    {c.voice && <span className="text-gray-400"> — {c.voice}</span>}
                                </div>
                            ))}
                        </div>
                    )}
                    {Array.isArray(premise.endings) && premise.endings.length > 0 && (
                        <div className="text-sm text-gray-400">
                            <span className="text-gray-500">Endings: </span>
                            {premise.endings.map((e: any) => e.id).join(', ')}
                        </div>
                    )}
                </section>
            )}

            {/* Components + done-conditions */}
            <section className="space-y-2">
                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Components</h3>
                {detail.spec.components.map(c => (
                    <div key={c.id} className="bg-[#1a1a1a] border border-white/[0.06] rounded-lg px-3 py-2">
                        <div className="text-white text-sm font-medium">{c.id}</div>
                        {c.description && <div className="text-gray-500 text-xs mt-0.5">{c.description}</div>}
                        <div className="text-gray-600 text-[11px] mt-1">
                            {(c.done_conditions || []).length} done-condition(s)
                        </div>
                    </div>
                ))}
            </section>

            {/* To-do (failing done-conditions) */}
            <section className="space-y-2">
                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">
                    To-do {detail.todo.length === 0 ? '— complete ✓' : `(${detail.todo.length} failing)`}
                </h3>
                {detail.todo.length === 0 ? (
                    <p className="text-green-400 text-sm">Every done-condition passes.</p>
                ) : (
                    <ul className="space-y-1">
                        {detail.todo.map((t, i) => (
                            <li key={i} className="text-sm text-gray-300">
                                <span className="text-amber-400 font-mono text-xs">[{t.component_id}]</span>{' '}
                                <span className="text-gray-500">{String(t.check?.type)}</span>: {t.detail}
                            </li>
                        ))}
                    </ul>
                )}
            </section>

            {detail.built && (
                <p className="text-gray-500 text-sm">
                    Build packaged under the run's <span className="font-mono text-gray-400">game_output/</span>.
                </p>
            )}
        </div>
    )
}

const GamesPanel: React.FC = () => {
    const [games, setGames] = useState<Game[]>([])
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [selected, setSelected] = useState<string | null>(null)

    const refresh = useCallback(() => {
        setLoading(true)
        setError(null)
        api.listGames()
            .then(setGames)
            .catch(e => setError(e instanceof Error ? e.message : 'Failed to load games'))
            .finally(() => setLoading(false))
    }, [])

    useEffect(refresh, [refresh])

    return (
        <div className="h-full flex">
            {/* List */}
            <div className="w-72 flex-shrink-0 border-r border-white/[0.06] flex flex-col">
                <div className="flex-shrink-0 px-3 py-2 flex items-center justify-between border-b border-white/[0.06]">
                    <span className="text-gray-400 text-xs font-semibold uppercase tracking-wide">Games</span>
                    <button onClick={refresh} className="text-gray-600 hover:text-gray-300 text-xs">Refresh</button>
                </div>
                <div className="flex-1 overflow-y-auto">
                    {loading && <div className="p-3 text-gray-500 text-sm">Loading…</div>}
                    {error && <div className="p-3 text-red-400 text-sm">{error}</div>}
                    {!loading && !error && games.length === 0 && (
                        <div className="p-3 text-gray-500 text-sm">No games yet. Ask Maestro in chat to make one.</div>
                    )}
                    {games.map(g => (
                        <button
                            key={g.run_id}
                            onClick={() => setSelected(g.run_id)}
                            className={`w-full text-left px-3 py-2 border-b border-white/[0.04] transition-colors ${
                                selected === g.run_id ? 'bg-white/[0.06]' : 'hover:bg-white/[0.03]'
                            }`}
                        >
                            <div className="text-white text-sm font-medium truncate">{g.title || g.run_id}</div>
                            <div className="flex items-center gap-1.5 mt-1">
                                {g.frozen ? <Badge label="frozen" tone="blue" /> : <Badge label="draft" tone="gray" />}
                                {g.built ? <Badge label="built" tone="green" /> : null}
                                <span className="text-gray-600 text-[10px]">{g.n_components} comp</span>
                            </div>
                        </button>
                    ))}
                </div>
            </div>

            {/* Detail */}
            <div className="flex-1 min-w-0">
                {selected
                    ? <GameDetailView runId={selected} />
                    : <div className="flex items-center justify-center h-full text-gray-500 text-sm">Select a game</div>}
            </div>
        </div>
    )
}

export default GamesPanel
