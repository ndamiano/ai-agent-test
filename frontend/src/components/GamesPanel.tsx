import React, { useEffect, useState, useCallback, useRef } from 'react'
import { api } from '../api/client'
import { useWebSocket } from '../contexts/WebSocketContext'
import type { Game, GameDetail, TodoItem, WebSocketMessage } from '../types'

const Badge: React.FC<{ label: string; tone: 'green' | 'blue' | 'gray' | 'amber' }> = ({ label, tone }) => {
    const tones = {
        green: 'bg-green-500/15 text-green-400',
        blue: 'bg-blue-500/15 text-blue-400',
        amber: 'bg-amber-500/15 text-amber-400',
        gray: 'bg-white/[0.06] text-gray-400',
    }
    return <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${tones[tone]}`}>{label}</span>
}

const GameDetailView: React.FC<{ runId: string; onChanged: () => void }> = ({ runId, onChanged }) => {
    const { subscribe } = useWebSocket()
    const [detail, setDetail] = useState<GameDetail | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [building, setBuilding] = useState(false)
    const [feed, setFeed] = useState<string[]>([])
    const [liveTodo, setLiveTodo] = useState<TodoItem[] | null>(null)
    const [acting, setActing] = useState(false)

    const load = useCallback(() => {
        let cancelled = false
        setLoading(true)
        setError(null)
        api.getGame(runId)
            .then(d => { if (!cancelled) { setDetail(d); setBuilding(d.building) } })
            .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load game') })
            .finally(() => { if (!cancelled) setLoading(false) })
        return () => { cancelled = true }
    }, [runId])

    useEffect(() => { setFeed([]); setLiveTodo(null); return load() }, [load])

    // Live build events for this run.
    useEffect(() => {
        const unsub = subscribe(runId, (msg: WebSocketMessage) => {
            switch (msg.type) {
                case 'build_started':
                    setBuilding(true)
                    setFeed([`build started — ${msg.n_failing} checks failing`])
                    if (msg.todo) setLiveTodo(msg.todo)
                    break
                case 'build_step':
                    setFeed(prev => [
                        ...prev.slice(-40),
                        `step ${msg.step}${msg.mode ? ` [${msg.mode}]` : ''}: ${msg.summary} — ${msg.n_failing} failing`,
                    ])
                    if (msg.todo) setLiveTodo(msg.todo)
                    break
                case 'component_complete':
                    setFeed(prev => [...prev.slice(-40), `✓ ${msg.component_id} complete`])
                    load()  // artifact/to-do changed
                    break
                case 'build_done':
                    setBuilding(false)
                    setLiveTodo(null)  // fall back to the authoritative refetched to-do
                    setFeed(prev => [...prev.slice(-40), msg.ok ? '✓ build complete' : '✗ build ended with failures'])
                    load()
                    onChanged()
                    break
            }
        })
        return unsub
    }, [runId, subscribe, load, onChanged])

    const feedRef = useRef<HTMLDivElement>(null)
    useEffect(() => { feedRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [feed])

    const freeze = async () => {
        setActing(true)
        try { await api.freezeGame(runId); load(); onChanged() }
        catch (e) { setError(e instanceof Error ? e.message : 'Freeze failed') }
        finally { setActing(false) }
    }
    const build = async () => {
        setActing(true)
        setBuilding(true)
        try { await api.buildGame(runId) }
        catch (e) { setBuilding(false); setError(e instanceof Error ? e.message : 'Build failed') }
        finally { setActing(false) }
    }

    if (loading && !detail) return <div className="p-6 text-gray-500 text-sm">Loading…</div>
    if (error && !detail) return <div className="p-6 text-red-400 text-sm">Error: {error}</div>
    if (!detail) return null

    const premise = detail.artifact?.premise as Record<string, any> | undefined

    return (
        <div className="h-full overflow-y-auto px-6 py-5 space-y-6">
            <div>
                <div className="flex items-center gap-2 flex-wrap">
                    <h2 className="text-white text-lg font-semibold">{detail.spec.title || detail.run_id}</h2>
                    {detail.frozen ? <Badge label="frozen" tone="blue" /> : <Badge label="draft" tone="gray" />}
                    {detail.built ? <Badge label="built" tone="green" /> : null}
                    {building ? <Badge label="building…" tone="amber" /> : null}
                </div>
                {detail.spec.request && <p className="text-gray-400 text-sm mt-1">{detail.spec.request}</p>}
                <p className="text-gray-600 text-xs mt-1 font-mono">{detail.run_id}</p>

                <div className="flex gap-2 mt-3">
                    {!detail.frozen && (
                        <button onClick={freeze} disabled={acting}
                            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">
                            Freeze spec
                        </button>
                    )}
                    {detail.frozen && (
                        <button onClick={build} disabled={acting || building}
                            className="bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">
                            {building ? 'Building…' : detail.built ? 'Rebuild' : 'Build'}
                        </button>
                    )}
                </div>
                {error && detail && <p className="text-red-400 text-xs mt-2">{error}</p>}
            </div>

            {/* Live build feed */}
            {feed.length > 0 && (
                <section className="space-y-2">
                    <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Build feed</h3>
                    <div className="bg-black/40 border border-white/[0.06] rounded-lg px-3 py-2 max-h-48 overflow-y-auto font-mono text-[11px] text-gray-400 space-y-0.5">
                        {feed.map((line, i) => <div key={i}>{line}</div>)}
                        <div ref={feedRef} />
                    </div>
                </section>
            )}

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

            {/* Components */}
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

            {/* To-do (live during a build, else the last fetched state) */}
            {(() => {
                const todo = liveTodo ?? detail.todo
                return (
                    <section className="space-y-2">
                        <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">
                            To-do {todo.length === 0 ? '— complete ✓' : `(${todo.length} failing)`}
                            {liveTodo ? <span className="text-amber-400 ml-1 normal-case font-normal">· live</span> : null}
                        </h3>
                        {todo.length === 0 ? (
                            <p className="text-green-400 text-sm">Every done-condition passes.</p>
                        ) : (
                            <ul className="space-y-1">
                                {todo.map((t, i) => (
                                    <li key={i} className="text-sm text-gray-300">
                                        <span className="text-amber-400 font-mono text-xs">[{t.component_id}]</span>{' '}
                                        <span className="text-gray-500">{String(t.check?.type)}</span>: {t.detail}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </section>
                )
            })()}

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
                                {g.building ? <Badge label="building…" tone="amber" /> : null}
                                <span className="text-gray-600 text-[10px]">{g.n_components} comp</span>
                            </div>
                        </button>
                    ))}
                </div>
            </div>

            {/* Detail */}
            <div className="flex-1 min-w-0">
                {selected
                    ? <GameDetailView runId={selected} onChanged={refresh} />
                    : <div className="flex items-center justify-center h-full text-gray-500 text-sm">Select a game</div>}
            </div>
        </div>
    )
}

export default GamesPanel
