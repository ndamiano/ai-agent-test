import React, { useEffect, useState, useCallback, useRef } from 'react'
import { api } from '../api/client'
import { useWebSocket } from '../contexts/WebSocketContext'
import type { Game, GameDetail, TodoItem, WebSocketMessage } from '../types'

// Render a done-condition as a plain-English line so the human reviews the actual contract
// at freeze time — not just "N done-condition(s)", which hid that quality checks were missing.
const describeCheck = (c: Record<string, any>): string => {
    const t = c.check?.type ?? c.type
    const path = c.check?.path ?? c.path
    const min = c.check?.min ?? c.min
    switch (t) {
        case 'count': return `≥ ${min} ${path}`
        case 'exists': return `${path} present`
        case 'distinct': return `${path} all distinct`
        case 'each_has': return `each ${path} has ${(c.fields || []).join(', ')}`
        case 'refs_resolve': return `${c.from ?? ''} all resolve to ${c.to ?? ''}`
        case 'each_node_min_lines': return `every node ≥ ${min ?? 3} lines`
        case 'min_branches': return `≥ ${min ?? 1} player choice(s)`
        case 'reachable_from_start': return 'every node reachable from start'
        case 'all_characters_speak': return 'every character speaks'
        case 'compiles': return 'builds + lints clean'
        default: return String(t)
    }
}

const Badge: React.FC<{ label: string; tone: 'green' | 'blue' | 'gray' | 'amber' }> = ({ label, tone }) => {
    const tones = {
        green: 'bg-green-500/15 text-green-400',
        blue: 'bg-blue-500/15 text-blue-400',
        amber: 'bg-amber-500/15 text-amber-400',
        gray: 'bg-white/[0.06] text-gray-400',
    }
    return <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${tones[tone]}`}>{label}</span>
}

// One scene/node: read its lines, edit them by hand, or hand the agent a note and have it
// rewrite the scene. The read/edit/rewrite trio the human asked for, per node.
const NodeCard: React.FC<{
    runId: string; nodeId: string; node: any; editable: boolean; rewriting: boolean
    onRewrite: (nodeId: string, note: string) => void; onSaved: () => void
}> = ({ runId, nodeId, node, editable, rewriting, onRewrite, onSaved }) => {
    const [editing, setEditing] = useState(false)
    const [text, setText] = useState('')
    const [note, setNote] = useState('')
    const [busy, setBusy] = useState(false)
    const [err, setErr] = useState<string | null>(null)

    const save = async () => {
        setBusy(true); setErr(null)
        try {
            const content = JSON.parse(text)
            await api.editNode(runId, nodeId, content)
            setEditing(false); onSaved()
        } catch (e) { setErr(e instanceof Error ? e.message : 'save failed') }
        finally { setBusy(false) }
    }

    const lines: any[] = Array.isArray(node?.lines) ? node.lines : []
    return (
        <div className="bg-[#1a1a1a] border border-white/[0.06] rounded-lg px-3 py-2">
            <div className="flex items-center justify-between">
                <div className="text-white text-sm font-medium font-mono">{nodeId}
                    {node?.end?.type && <span className="text-gray-600 text-xs ml-2">→ {node.end.type}{node.end.target ? ` ${node.end.target}` : ''}</span>}
                    {rewriting && <span className="text-amber-400 text-xs ml-2">rewriting…</span>}
                </div>
                {editable && (editing ? (
                    <div className="flex gap-2">
                        <button onClick={save} disabled={busy} className="text-green-400 hover:text-green-300 text-[11px]">Save</button>
                        <button onClick={() => { setEditing(false); setErr(null) }} className="text-gray-500 hover:text-gray-300 text-[11px]">Cancel</button>
                    </div>
                ) : (
                    <button onClick={() => { setEditing(true); setText(JSON.stringify(node, null, 2)) }}
                        className="text-gray-500 hover:text-gray-300 text-[11px]">Edit</button>
                ))}
            </div>

            {editing ? (
                <textarea value={text} onChange={e => setText(e.target.value)} spellCheck={false}
                    className="w-full mt-2 bg-black/50 border border-white/[0.1] rounded p-2 font-mono text-[11px] text-gray-200 h-56" />
            ) : (
                <div className="mt-1.5 space-y-0.5">
                    {lines.map((ln, i) => (
                        <div key={i} className="text-[13px] leading-snug">
                            <span className="text-blue-300 font-medium">{ln.speaker ?? '—'}</span>
                            {ln.emotion && <span className="text-gray-600 text-[10px]"> ({ln.emotion})</span>}
                            <span className="text-gray-300">: {ln.text}</span>
                        </div>
                    ))}
                </div>
            )}
            {err && <p className="text-red-400 text-[11px] mt-1">{err}</p>}

            {editable && !editing && (
                <div className="flex gap-2 mt-2 pt-2 border-t border-white/[0.05]">
                    <input value={note} onChange={e => setNote(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter' && note.trim() && !rewriting) { onRewrite(nodeId, note); setNote('') } }}
                        placeholder="Direction for a rewrite (e.g. make it tenser)…"
                        className="flex-1 bg-black/40 border border-white/[0.1] rounded text-xs text-gray-200 px-2 py-1" />
                    <button onClick={() => { onRewrite(nodeId, note); setNote('') }} disabled={rewriting || !note.trim()}
                        className="bg-purple-600/80 hover:bg-purple-700 disabled:opacity-40 text-white px-2.5 py-1 rounded text-xs whitespace-nowrap">
                        Rewrite scene
                    </button>
                </div>
            )}
        </div>
    )
}

const GameDetailView: React.FC<{ runId: string; onChanged: () => void }> = ({ runId, onChanged }) => {
    const { subscribe } = useWebSocket()
    const [detail, setDetail] = useState<GameDetail | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [building, setBuilding] = useState(false)
    const [status, setStatus] = useState<string>('idle')
    const [feed, setFeed] = useState<string[]>([])
    const [liveTodo, setLiveTodo] = useState<TodoItem[] | null>(null)
    const [acting, setActing] = useState(false)
    const [newTodoText, setNewTodoText] = useState('')
    const [newTodoComp, setNewTodoComp] = useState('')
    const [editing, setEditing] = useState<string | null>(null)
    const [editText, setEditText] = useState('')
    const [rewriting, setRewriting] = useState<string[]>([])
    const [autoPause, setAutoPause] = useState(false)

    const load = useCallback(() => {
        let cancelled = false
        setLoading(true)
        setError(null)
        api.getGame(runId)
            .then(d => { if (!cancelled) { setDetail(d); setBuilding(d.building); setStatus(d.status); setAutoPause(d.auto_pause) } })
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
                    setBuilding(true); setStatus('running')
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
                case 'build_paused':
                    setStatus('paused'); setFeed(prev => [...prev.slice(-40), '⏸ paused'])
                    break
                case 'auto_paused':
                    setFeed(prev => [...prev.slice(-40), `⏸ auto-paused after ${msg.component_id}`])
                    break
                case 'node_rewrite_started':
                    setRewriting(prev => prev.includes(msg.node_id) ? prev : [...prev, msg.node_id])
                    setFeed(prev => [...prev.slice(-40), `✎ rewriting ${msg.node_id}${msg.note ? `: "${msg.note}"` : ''}`])
                    break
                case 'node_rewrite_step':
                    setFeed(prev => [...prev.slice(-40), `  ${msg.node_id}: ${msg.summary}`])
                    break
                case 'node_rewrite_done':
                    setRewriting(prev => prev.filter(n => n !== msg.node_id))
                    setFeed(prev => [...prev.slice(-40), msg.ok ? `✓ rewrote ${msg.node_id}` : `✗ rewrite failed: ${msg.error}`])
                    load()
                    break
                case 'build_resumed':
                    setStatus('running'); setFeed(prev => [...prev.slice(-40), '▶ resumed'])
                    break
                case 'awaiting_human':
                    setStatus('awaiting_human')
                    setFeed(prev => [...prev.slice(-40), '⏳ machine checks pass — awaiting your todos'])
                    load()
                    break
                case 'human_cleared':
                    setStatus('running'); setFeed(prev => [...prev.slice(-40), '▶ todos cleared'])
                    break
                case 'build_cancelled':
                    setBuilding(false); setStatus('cancelled'); setLiveTodo(null)
                    setFeed(prev => [...prev.slice(-40), '✗ build cancelled'])
                    load(); onChanged()
                    break
                case 'component_complete':
                    setFeed(prev => [...prev.slice(-40), `✓ ${msg.component_id} complete`])
                    load()  // artifact/to-do changed
                    break
                case 'build_done':
                    setBuilding(false); setStatus('built'); setLiveTodo(null)
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

    // A build is actively writing only when running — when paused / awaiting_human the
    // executor is parked, so hand-edits / compile / regenerate are safe.
    const running = building && status !== 'paused' && status !== 'awaiting_human'
    const editable = !!detail?.frozen && !running

    const act = async (fn: () => Promise<unknown>, errMsg: string, reload = true) => {
        setActing(true); setError(null)
        try { await fn(); if (reload) load() }
        catch (e) { setError(e instanceof Error ? `${errMsg}: ${e.message}` : errMsg) }
        finally { setActing(false) }
    }

    const freeze = () => act(async () => { await api.freezeGame(runId); onChanged() }, 'Freeze failed')
    const build = async () => {
        setActing(true); setBuilding(true); setStatus('running')
        try { await api.buildGame(runId, autoPause) }
        catch (e) { setBuilding(false); setStatus('idle'); setError(e instanceof Error ? e.message : 'Build failed') }
        finally { setActing(false) }
    }
    const toggleAutoPause = async (enabled: boolean) => {
        setAutoPause(enabled)
        if (building) { try { await api.setAutoPause(runId, enabled) } catch { /* best effort */ } }
    }
    const rewrite = (nodeId: string, note: string) => {
        setRewriting(prev => prev.includes(nodeId) ? prev : [...prev, nodeId])
        act(() => api.rewriteNode(runId, nodeId, note), 'Rewrite failed', false)
    }
    const pause = () => { setStatus('paused'); act(() => api.pauseGame(runId), 'Pause failed', false) }
    const resume = () => { setStatus('running'); act(() => api.resumeGame(runId), 'Resume failed', false) }
    const cancel = () => act(() => api.cancelGame(runId), 'Cancel failed', false)
    const compile = () => act(async () => {
        const r = await api.compileGame(runId)
        setFeed(prev => [...prev.slice(-40), r.ok ? '✓ compiled' : `✗ compile failed: ${r.reason}`])
    }, 'Compile failed')
    const regenerate = () => act(async () => {
        await api.regenerateAssets(runId)
        setFeed(prev => [...prev.slice(-40), '✓ assets regenerated (recompile to repackage)'])
    }, 'Regenerate failed')
    const addTodo = () => act(async () => {
        await api.addTodo(runId, newTodoComp || (detail?.spec.components[0]?.id ?? ''), newTodoText)
        setNewTodoText('')
    }, 'Add todo failed')
    const resolveTodo = (id: string, done: boolean) => act(() => api.resolveTodo(runId, id, done), 'Update failed')
    const waive = (t: TodoItem) => act(() => api.waiveCheck(runId, t.component_id, t.check), 'Waive failed')
    const unwaive = (sig: string) => act(() => api.unwaiveCheck(runId, sig), 'Unwaive failed')
    const saveEdit = (componentId: string) => act(async () => {
        let parsed: Record<string, any>
        try { parsed = JSON.parse(editText) } catch { throw new Error('not valid JSON') }
        await api.editComponent(runId, componentId, parsed)
        setEditing(null)
    }, 'Edit failed')

    if (loading && !detail) return <div className="p-6 text-gray-500 text-sm">Loading…</div>
    if (error && !detail) return <div className="p-6 text-red-400 text-sm">Error: {error}</div>
    if (!detail) return null

    const premise = detail.artifact?.premise as Record<string, any> | undefined
    const statusTone = status === 'paused' ? 'amber' : status === 'awaiting_human' ? 'amber'
        : status === 'cancelled' ? 'gray' : running ? 'amber' : 'gray'

    return (
        <div className="h-full overflow-y-auto px-6 py-5 space-y-6">
            <div>
                <div className="flex items-center gap-2 flex-wrap">
                    <h2 className="text-white text-lg font-semibold">{detail.spec.title || detail.run_id}</h2>
                    {detail.frozen ? <Badge label="frozen" tone="blue" /> : <Badge label="draft" tone="gray" />}
                    {detail.built ? <Badge label="built" tone="green" /> : null}
                    {building ? <Badge label={status === 'paused' ? 'paused' : status === 'awaiting_human' ? 'awaiting you' : 'building…'} tone={statusTone as any} /> : null}
                </div>
                {detail.spec.request && <p className="text-gray-400 text-sm mt-1">{detail.spec.request}</p>}
                <p className="text-gray-600 text-xs mt-1 font-mono">{detail.run_id}</p>

                <div className="flex gap-2 mt-3 flex-wrap">
                    {!detail.frozen && (
                        <button onClick={freeze} disabled={acting}
                            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">
                            Freeze spec
                        </button>
                    )}
                    {detail.frozen && !building && (
                        <button onClick={build} disabled={acting}
                            className="bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">
                            {detail.built ? 'Rebuild' : 'Build'}
                        </button>
                    )}
                    {building && status === 'running' && (
                        <button onClick={pause} disabled={acting}
                            className="bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">
                            Pause
                        </button>
                    )}
                    {building && status === 'paused' && (
                        <button onClick={resume} disabled={acting}
                            className="bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">
                            Resume
                        </button>
                    )}
                    {building && (
                        <button onClick={cancel} disabled={acting}
                            className="bg-red-600/80 hover:bg-red-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">
                            Cancel
                        </button>
                    )}
                    {detail.frozen && (
                        <button onClick={compile} disabled={acting || !editable} title={editable ? '' : 'pause the build first'}
                            className="bg-white/[0.08] hover:bg-white/[0.14] disabled:opacity-40 text-gray-200 px-3 py-1.5 rounded text-xs font-medium">
                            Build now
                        </button>
                    )}
                    {detail.frozen && (
                        <button onClick={regenerate} disabled={acting || !editable || !detail.assets_exist}
                            title={!detail.assets_exist ? 'no images yet — build first' : editable ? 'regenerate all images from the manifest' : 'pause the build first'}
                            className="bg-white/[0.08] hover:bg-white/[0.14] disabled:opacity-40 text-gray-200 px-3 py-1.5 rounded text-xs font-medium">
                            Regenerate images
                        </button>
                    )}
                    {detail.frozen && (
                        <label className="flex items-center gap-1.5 text-gray-400 text-xs ml-1 cursor-pointer select-none">
                            <input type="checkbox" checked={autoPause} onChange={e => toggleAutoPause(e.target.checked)} className="accent-amber-500" />
                            auto-pause after each component
                        </label>
                    )}
                </div>
                {status === 'awaiting_human' && (
                    <div className="mt-3 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2 text-amber-300 text-xs">
                        Machine checks pass — the build is parked until you resolve your open todos below.
                    </div>
                )}
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

            {/* Components (nodes get their own per-scene section below) */}
            <section className="space-y-2">
                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Components</h3>
                {detail.spec.components.filter(c => c.id !== 'nodes').map(c => (
                    <div key={c.id} className="bg-[#1a1a1a] border border-white/[0.06] rounded-lg px-3 py-2">
                        <div className="flex items-center justify-between">
                            <div className="text-white text-sm font-medium">{c.id}</div>
                            {editable && detail.artifact?.[c.id] && (
                                editing === c.id ? (
                                    <div className="flex gap-2">
                                        <button onClick={() => saveEdit(c.id)} disabled={acting}
                                            className="text-green-400 hover:text-green-300 text-[11px]">Save</button>
                                        <button onClick={() => setEditing(null)}
                                            className="text-gray-500 hover:text-gray-300 text-[11px]">Cancel</button>
                                    </div>
                                ) : (
                                    <button
                                        onClick={() => { setEditing(c.id); setEditText(JSON.stringify(detail.artifact[c.id], null, 2)) }}
                                        className="text-gray-500 hover:text-gray-300 text-[11px]">Edit</button>
                                )
                            )}
                        </div>
                        {c.description && <div className="text-gray-500 text-xs mt-0.5">{c.description}</div>}
                        {editing === c.id ? (
                            <textarea value={editText} onChange={e => setEditText(e.target.value)} spellCheck={false}
                                className="w-full mt-2 bg-black/50 border border-white/[0.1] rounded p-2 font-mono text-[11px] text-gray-200 h-64" />
                        ) : (c.done_conditions || []).length === 0 ? (
                            <div className="text-red-400 text-[11px] mt-1">⚠ no done-conditions — nothing checks this</div>
                        ) : (
                            <ul className="mt-1 space-y-0.5">
                                {(c.done_conditions || []).map((dc, i) => (
                                    <li key={i} className="text-gray-400 text-[11px] flex gap-1">
                                        <span className="text-gray-600">✓</span>{describeCheck(dc)}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                ))}
            </section>

            {/* Scenes — per-node read / edit / rewrite */}
            {(() => {
                const nc = detail.artifact?.nodes as Record<string, any> | undefined
                const ids: string[] = nc?.node_ids ?? Object.keys(nc?.nodes ?? {})
                if (!nc || ids.length === 0) return null
                return (
                    <section className="space-y-2">
                        <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Scenes ({ids.length})</h3>
                        {ids.map(id => (
                            <NodeCard key={id} runId={runId} nodeId={id} node={nc.nodes?.[id]}
                                editable={editable} rewriting={rewriting.includes(id)}
                                onRewrite={rewrite} onSaved={load} />
                        ))}
                    </section>
                )
            })()}

            {/* To-do (live during a build, else the last fetched state) */}
            {(() => {
                const todo = liveTodo ?? detail.todo
                return (
                    <section className="space-y-2">
                        <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">
                            To-do {todo.length === 0 ? '— complete ✓' : `(${todo.length} open)`}
                            {liveTodo ? <span className="text-amber-400 ml-1 normal-case font-normal">· live</span> : null}
                        </h3>
                        {todo.length === 0 ? (
                            <p className="text-green-400 text-sm">Every done-condition passes.</p>
                        ) : (
                            <ul className="space-y-1">
                                {todo.map((t, i) => (
                                    <li key={i} className="text-sm text-gray-300 flex items-start justify-between gap-2 group">
                                        <span>
                                            <span className={`font-mono text-xs ${t.human ? 'text-blue-400' : 'text-amber-400'}`}>[{t.component_id}]</span>{' '}
                                            <span className="text-gray-500">{t.human ? 'human todo' : String(t.check?.type)}</span>: {t.detail}
                                        </span>
                                        {t.human ? (
                                            <button onClick={() => resolveTodo(t.check.id, true)} disabled={acting}
                                                className="text-green-400 hover:text-green-300 text-[11px] shrink-0">Mark done</button>
                                        ) : (
                                            <button onClick={() => waive(t)} disabled={acting}
                                                className="text-gray-600 hover:text-amber-400 text-[11px] shrink-0 opacity-0 group-hover:opacity-100"
                                                title="Accept this check as-is (waive)">Waive</button>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        )}

                        {/* Add a human todo — the human is the arbiter of done */}
                        <div className="flex gap-2 pt-1">
                            <select value={newTodoComp} onChange={e => setNewTodoComp(e.target.value)}
                                className="bg-black/40 border border-white/[0.1] rounded text-xs text-gray-300 px-1.5 py-1">
                                {detail.spec.components.map(c => <option key={c.id} value={c.id}>{c.id}</option>)}
                            </select>
                            <input value={newTodoText} onChange={e => setNewTodoText(e.target.value)}
                                onKeyDown={e => { if (e.key === 'Enter' && newTodoText.trim()) addTodo() }}
                                placeholder="Add a todo (you decide when it's done)…"
                                className="flex-1 bg-black/40 border border-white/[0.1] rounded text-xs text-gray-200 px-2 py-1" />
                            <button onClick={addTodo} disabled={acting || !newTodoText.trim()}
                                className="bg-blue-600/80 hover:bg-blue-700 disabled:opacity-40 text-white px-2.5 py-1 rounded text-xs">Add</button>
                        </div>
                    </section>
                )
            })()}

            {/* Waived checks — accepted red checks, reinstatable */}
            {detail.waivers.length > 0 && (
                <section className="space-y-1">
                    <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Waived ({detail.waivers.length})</h3>
                    <ul className="space-y-1">
                        {detail.waivers.map(w => (
                            <li key={w.sig} className="text-sm text-gray-400 flex items-center justify-between gap-2">
                                <span><span className="font-mono text-xs text-gray-600">[{w.component_id}]</span> {String(w.check?.type)} {w.check?.path ?? ''}</span>
                                <button onClick={() => unwaive(w.sig)} disabled={acting}
                                    className="text-gray-600 hover:text-gray-300 text-[11px] shrink-0">Reinstate</button>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

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
