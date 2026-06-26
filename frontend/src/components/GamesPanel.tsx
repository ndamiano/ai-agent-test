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

// A card that shows a read view and swaps to a raw-JSON editor on demand. The shared spine of
// every editable artifact card (premise, character, asset manifest, places, matches).
const JsonEditCard: React.FC<{
    title: React.ReactNode; subtitle?: string; value: any; editable: boolean
    onSave: (v: any) => Promise<void> | void; children?: React.ReactNode
}> = ({ title, subtitle, value, editable, onSave, children }) => {
    const [editing, setEditing] = useState(false)
    const [text, setText] = useState('')
    const [busy, setBusy] = useState(false)
    const [err, setErr] = useState<string | null>(null)

    const save = async () => {
        setBusy(true); setErr(null)
        try { await onSave(JSON.parse(text)); setEditing(false) }
        catch (e) { setErr(e instanceof Error ? e.message : 'save failed') }
        finally { setBusy(false) }
    }

    return (
        <div className="bg-[#1a1a1a] border border-white/[0.06] rounded-lg px-3 py-2">
            <div className="flex items-center justify-between gap-2">
                <div className="text-white text-sm font-medium min-w-0">{title}</div>
                {editable && (editing ? (
                    <div className="flex gap-2 shrink-0">
                        <button onClick={save} disabled={busy} className="text-green-400 hover:text-green-300 text-[11px]">Save</button>
                        <button onClick={() => { setEditing(false); setErr(null) }} className="text-gray-500 hover:text-gray-300 text-[11px]">Cancel</button>
                    </div>
                ) : (
                    <button onClick={() => { setEditing(true); setText(JSON.stringify(value, null, 2)) }}
                        className="text-gray-500 hover:text-gray-300 text-[11px] shrink-0">Edit</button>
                ))}
            </div>
            {subtitle && <div className="text-gray-500 text-xs mt-0.5">{subtitle}</div>}
            {editing
                ? <textarea value={text} onChange={e => setText(e.target.value)} spellCheck={false}
                    className="w-full mt-2 bg-black/50 border border-white/[0.1] rounded p-2 font-mono text-[11px] text-gray-200 h-64" />
                : <div className="mt-1.5">{children}</div>}
            {err && <p className="text-red-400 text-[11px] mt-1">{err}</p>}
        </div>
    )
}

type BodyTabId = 'story' | 'scenes' | 'contract'
const BodyTab: React.FC<{ id: BodyTabId; active: BodyTabId; onPick: (id: BodyTabId) => void; label: string }> = ({ id, active, onPick, label }) => (
    <button onClick={() => onPick(id)}
        className={`px-2 pb-2 -mb-px text-xs font-semibold uppercase tracking-wide border-b-2 transition-colors ${
            id === active ? 'text-white border-blue-500' : 'text-gray-500 border-transparent hover:text-gray-300'
        }`}>
        {label}
    </button>
)

const CharacterCard: React.FC<{ char: any; editable: boolean; onSave: (c: any) => Promise<void> | void }> = ({ char, editable, onSave }) => (
    <JsonEditCard editable={editable} value={char} onSave={onSave}
        title={<span>{char.name || char.id} <span className="text-gray-600 font-normal text-xs">({char.id})</span></span>}>
        {char.voice && <p className="text-gray-400 text-xs">{char.voice}</p>}
        {char.description && <p className="text-gray-400 text-xs mt-0.5">{char.description}</p>}
        {char.appearance && <p className="text-gray-500 text-[11px] mt-0.5">look: {char.appearance}</p>}
    </JsonEditCard>
)

const ArtifactCard: React.FC<{ id: string; description?: string; doneConditions: any[]; value: any; editable: boolean; onSave: (v: any) => Promise<void> | void }>
    = ({ id, description, doneConditions, value, editable, onSave }) => (
        <JsonEditCard editable={editable && value != null} value={value} onSave={onSave} title={id} subtitle={description}>
            {(doneConditions || []).length === 0 ? (
                <div className="text-red-400 text-[11px]">⚠ no done-conditions — nothing checks this</div>
            ) : (
                <ul className="space-y-0.5">
                    {doneConditions.map((dc, i) => (
                        <li key={i} className="text-gray-400 text-[11px] flex gap-1"><span className="text-gray-600">✓</span>{describeCheck(dc)}</li>
                    ))}
                </ul>
            )}
        </JsonEditCard>
    )

// One scene/node: read its lines, edit them by hand, or hand the agent a note and have it
// rewrite the scene. The read/edit/rewrite trio the human asked for, per node.
const NodeCard: React.FC<{
    runId: string; nodeId: string; node: any; editable: boolean; rewriting: boolean
    onRewrite: (nodeId: string, note: string) => void; onSaved: (nodeId: string) => void
}> = ({ runId, nodeId, node, editable, rewriting, onRewrite, onSaved }) => {
    const [editing, setEditing] = useState(false)
    const [text, setText] = useState('')
    const [note, setNote] = useState('')
    const [busy, setBusy] = useState(false)
    const [err, setErr] = useState<string | null>(null)

    // A fresh scene selected → drop any half-finished edit of the previous one.
    useEffect(() => { setEditing(false); setErr(null) }, [nodeId])

    const save = async () => {
        setBusy(true); setErr(null)
        try {
            await api.editNode(runId, nodeId, JSON.parse(text))
            setEditing(false); onSaved(nodeId)
        } catch (e) { setErr(e instanceof Error ? e.message : 'save failed') }
        finally { setBusy(false) }
    }

    const lines: any[] = Array.isArray(node?.lines) ? node.lines : []
    return (
        <div className="bg-[#1a1a1a] border border-white/[0.06] rounded-lg px-3 py-2.5">
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
                    className="w-full mt-2 bg-black/50 border border-white/[0.1] rounded p-2 font-mono text-[11px] text-gray-200 h-72" />
            ) : (
                <div className="mt-2 space-y-1">
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
                <div className="flex gap-2 mt-2.5 pt-2.5 border-t border-white/[0.05]">
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

// One scene at a time. A scrollable strip of scene chips picks the active one; the most
// recently touched (rewritten / edited / newest) sits first and is auto-selected.
const SceneNavigator: React.FC<{
    runId: string; nodesComp: any; orderedIds: string[]; activeId: string; editable: boolean
    rewriting: string[]; onPick: (id: string) => void; onRewrite: (id: string, note: string) => void; onSaved: (id: string) => void
}> = ({ runId, nodesComp, orderedIds, activeId, editable, rewriting, onPick, onRewrite, onSaved }) => {
    const idx = orderedIds.indexOf(activeId)
    return (
        <div className="space-y-2">
            <div className="flex items-center gap-2">
                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Scenes ({orderedIds.length})</h3>
                <div className="flex gap-1 ml-auto">
                    <button onClick={() => onPick(orderedIds[Math.max(0, idx - 1)])} disabled={idx <= 0}
                        className="text-gray-500 hover:text-gray-200 disabled:opacity-30 text-xs px-1">‹ prev</button>
                    <span className="text-gray-600 text-xs">{idx + 1}/{orderedIds.length}</span>
                    <button onClick={() => onPick(orderedIds[Math.min(orderedIds.length - 1, idx + 1)])} disabled={idx >= orderedIds.length - 1}
                        className="text-gray-500 hover:text-gray-200 disabled:opacity-30 text-xs px-1">next ›</button>
                </div>
            </div>
            <div className="flex gap-1.5 overflow-x-auto pb-1">
                {orderedIds.map(id => (
                    <button key={id} onClick={() => onPick(id)}
                        className={`shrink-0 px-2 py-1 rounded text-[11px] font-mono border transition-colors ${
                            id === activeId ? 'bg-white/[0.1] border-white/20 text-white' : 'bg-white/[0.03] border-white/[0.06] text-gray-500 hover:text-gray-300'
                        }`}>
                        {rewriting.includes(id) ? '✎ ' : ''}{id}
                    </button>
                ))}
            </div>
            {activeId && (
                <NodeCard runId={runId} nodeId={activeId} node={nodesComp.nodes?.[activeId]}
                    editable={editable} rewriting={rewriting.includes(activeId)} onRewrite={onRewrite} onSaved={onSaved} />
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
    const [rewriting, setRewriting] = useState<string[]>([])
    const [autoPause, setAutoPause] = useState(false)
    const [lastTouched, setLastTouched] = useState<string | null>(null)
    const [activeScene, setActiveScene] = useState<string | null>(null)
    const [bodyTab, setBodyTab] = useState<'story' | 'scenes' | 'contract'>('scenes')

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

    useEffect(() => { setFeed([]); setLiveTodo(null); setActiveScene(null); setLastTouched(null); return load() }, [load])

    const touch = (id: string) => { setLastTouched(id); setActiveScene(id) }

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
                    setFeed(prev => [...prev.slice(-60), `step ${msg.step}${msg.mode ? ` [${msg.mode}]` : ''}: ${msg.summary} — ${msg.n_failing} failing`])
                    if (msg.todo) setLiveTodo(msg.todo)
                    break
                case 'build_paused':
                    setStatus('paused'); setFeed(prev => [...prev.slice(-60), '⏸ paused'])
                    break
                case 'auto_paused':
                    setFeed(prev => [...prev.slice(-60), `⏸ auto-paused after ${msg.component_id}`])
                    break
                case 'node_rewrite_started':
                    setRewriting(prev => prev.includes(msg.node_id) ? prev : [...prev, msg.node_id])
                    setFeed(prev => [...prev.slice(-60), `✎ rewriting ${msg.node_id}${msg.note ? `: "${msg.note}"` : ''}`])
                    break
                case 'node_rewrite_step':
                    setFeed(prev => [...prev.slice(-60), `  ${msg.node_id}: ${msg.summary}`])
                    break
                case 'node_rewrite_done':
                    setRewriting(prev => prev.filter(n => n !== msg.node_id))
                    setFeed(prev => [...prev.slice(-60), msg.ok ? `✓ rewrote ${msg.node_id}` : `✗ rewrite failed: ${msg.error}`])
                    if (msg.ok) touch(msg.node_id)
                    load()
                    break
                case 'build_resumed':
                    setStatus('running'); setFeed(prev => [...prev.slice(-60), '▶ resumed'])
                    break
                case 'awaiting_human':
                    setStatus('awaiting_human')
                    setFeed(prev => [...prev.slice(-60), '⏳ machine checks pass — awaiting your todos'])
                    load()
                    break
                case 'human_cleared':
                    setStatus('running'); setFeed(prev => [...prev.slice(-60), '▶ todos cleared'])
                    break
                case 'build_cancelled':
                    setBuilding(false); setStatus('cancelled'); setLiveTodo(null)
                    setFeed(prev => [...prev.slice(-60), '✗ build cancelled'])
                    load(); onChanged()
                    break
                case 'component_complete':
                    setFeed(prev => [...prev.slice(-60), `✓ ${msg.component_id} complete`])
                    load()
                    break
                case 'build_done':
                    setBuilding(false); setStatus('built'); setLiveTodo(null)
                    setFeed(prev => [...prev.slice(-60), msg.ok ? '✓ build complete' : '✗ build ended with failures'])
                    load(); onChanged()
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
        setFeed(prev => [...prev.slice(-60), r.ok ? '✓ compiled' : `✗ compile failed: ${r.reason}`])
    }, 'Compile failed')
    const regenerate = () => act(async () => {
        await api.regenerateAssets(runId)
        setFeed(prev => [...prev.slice(-60), '✓ images regenerated (recompile to repackage)'])
    }, 'Regenerate failed')
    const reveal = () => act(() => api.revealGame(runId), 'Open folder failed', false)
    const componentIds: string[] = Object.keys(detail?.artifact ?? {})
    const addTodo = () => act(async () => {
        await api.addTodo(runId, newTodoComp || componentIds[0] || '', newTodoText)
        setNewTodoText('')
    }, 'Add todo failed')
    const resolveTodo = (id: string, done: boolean) => act(() => api.resolveTodo(runId, id, done), 'Update failed')
    const waive = (t: TodoItem) => act(() => api.waiveCheck(runId, t.idkey), 'Waive failed')
    const unwaive = (idkey: string) => act(() => api.unwaiveCheck(runId, idkey), 'Unwaive failed')
    const saveComponent = (id: string, v: any) => act(() => api.editComponent(runId, id, v), `Save ${id} failed`)
    const saveChar = (idx: number, char: any) => act(() => {
        const p = { ...premise, characters: (premise!.characters as any[]).map((c, i) => i === idx ? char : c) }
        return api.editComponent(runId, 'premise', p)
    }, 'Save character failed')

    if (loading && !detail) return <div className="p-6 text-gray-500 text-sm">Loading…</div>
    if (error && !detail) return <div className="p-6 text-red-400 text-sm">Error: {error}</div>
    if (!detail) return null

    const premise = detail.artifact?.premise as Record<string, any> | undefined
    const characters: any[] = Array.isArray(premise?.characters) ? premise!.characters : []
    const statusTone = status === 'cancelled' ? 'gray' : (status === 'paused' || status === 'awaiting_human' || running) ? 'amber' : 'gray'
    const todo = liveTodo ?? detail.todo

    // Scenes, most-recently-touched first; the active scene resolves to the touched one, else newest.
    const nodesComp = detail.artifact?.nodes as Record<string, any> | undefined
    const rawSceneIds: string[] = nodesComp?.node_ids ?? Object.keys(nodesComp?.nodes ?? {})
    const orderedIds = lastTouched && rawSceneIds.includes(lastTouched)
        ? [lastTouched, ...rawSceneIds.filter(id => id !== lastTouched)] : rawSceneIds
    const activeId = (activeScene && rawSceneIds.includes(activeScene)) ? activeScene
        : (lastTouched && rawSceneIds.includes(lastTouched)) ? lastTouched : rawSceneIds[rawSceneIds.length - 1]

    // Non-premise / non-nodes on-disk components are shown as editable JSON cards.
    const otherComponents = componentIds.filter(id => id !== 'premise' && id !== 'nodes')

    return (
        <div className="h-full flex flex-col">
            {/* ── FIXED TOP: controls + input bar + side-by-side feeds ────────────── */}
            <div className="flex-shrink-0 border-b border-white/[0.08] px-5 pt-3 pb-3 space-y-3">
                <div className="flex items-center gap-2 flex-wrap">
                    <h2 className="text-white text-base font-semibold">{detail.spec.title || detail.run_id}</h2>
                    {detail.frozen ? <Badge label="frozen" tone="blue" /> : <Badge label="draft" tone="gray" />}
                    {detail.built ? <Badge label="built" tone="green" /> : null}
                    {building ? <Badge label={status === 'paused' ? 'paused' : status === 'awaiting_human' ? 'awaiting you' : 'building…'} tone={statusTone as any} /> : null}
                    <span className="text-gray-600 text-[11px] font-mono ml-auto">{detail.run_id}</span>
                </div>

                <div className="flex gap-2 flex-wrap items-center">
                    {!detail.frozen && (
                        <button onClick={freeze} disabled={acting} className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">Freeze spec</button>
                    )}
                    {detail.frozen && !building && (
                        <button onClick={build} disabled={acting} className="bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">{detail.built ? 'Rebuild' : 'Build'}</button>
                    )}
                    {building && status === 'running' && (
                        <button onClick={pause} disabled={acting} className="bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">Pause</button>
                    )}
                    {building && status === 'paused' && (
                        <button onClick={resume} disabled={acting} className="bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">Resume</button>
                    )}
                    {building && (
                        <button onClick={cancel} disabled={acting} className="bg-red-600/80 hover:bg-red-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">Cancel</button>
                    )}
                    {detail.frozen && (
                        <button onClick={compile} disabled={acting || !editable} title={editable ? 'compile/package now' : 'pause the build first'}
                            className="bg-white/[0.08] hover:bg-white/[0.14] disabled:opacity-40 text-gray-200 px-3 py-1.5 rounded text-xs font-medium">Build now</button>
                    )}
                    {detail.frozen && (
                        <button onClick={regenerate} disabled={acting || !editable || !detail.assets_exist}
                            title={!detail.assets_exist ? 'no images yet — build first' : editable ? 'regenerate all images from the manifest' : 'pause the build first'}
                            className="bg-white/[0.08] hover:bg-white/[0.14] disabled:opacity-40 text-gray-200 px-3 py-1.5 rounded text-xs font-medium">Regenerate images</button>
                    )}
                    {detail.frozen && (
                        <label className="flex items-center gap-1.5 text-gray-400 text-xs ml-1 cursor-pointer select-none">
                            <input type="checkbox" checked={autoPause} onChange={e => toggleAutoPause(e.target.checked)} className="accent-amber-500" />
                            auto-pause per component
                        </label>
                    )}
                    <button onClick={reveal} disabled={acting} title="open this run's folder in your file manager"
                        className="bg-white/[0.08] hover:bg-white/[0.14] disabled:opacity-40 text-gray-200 px-3 py-1.5 rounded text-xs font-medium ml-auto">Open folder</button>
                </div>

                {status === 'awaiting_human' && (
                    <div className="bg-amber-500/10 border border-amber-500/30 rounded px-3 py-1.5 text-amber-300 text-xs">
                        Machine checks pass — the build is parked until you resolve your open todos.
                    </div>
                )}
                {error && <p className="text-red-400 text-xs">{error}</p>}

                {/* Single full-width human input bar: add a todo against any component */}
                <div className="flex gap-2">
                    <select value={newTodoComp} onChange={e => setNewTodoComp(e.target.value)}
                        className="bg-black/40 border border-white/[0.1] rounded text-xs text-gray-300 px-1.5 py-1.5">
                        {componentIds.map(id => <option key={id} value={id}>{id}</option>)}
                    </select>
                    <input value={newTodoText} onChange={e => setNewTodoText(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter' && newTodoText.trim()) addTodo() }}
                        placeholder="Add a todo for yourself (you decide when it's done) — blocks completion until resolved…"
                        className="flex-1 bg-black/40 border border-white/[0.1] rounded text-xs text-gray-200 px-2 py-1.5" />
                    <button onClick={addTodo} disabled={acting || !newTodoText.trim()}
                        className="bg-blue-600/80 hover:bg-blue-700 disabled:opacity-40 text-white px-3 py-1.5 rounded text-xs">Add todo</button>
                </div>

                {/* Two side-by-side scrollable feeds: build log | to-do */}
                <div className="grid grid-cols-2 gap-3">
                    <div className="flex flex-col">
                        <div className="text-gray-400 text-[10px] font-semibold uppercase tracking-wide mb-1">Build feed</div>
                        <div className="bg-black/40 border border-white/[0.06] rounded h-36 overflow-y-auto px-2.5 py-1.5 font-mono text-[11px] text-gray-400 space-y-0.5">
                            {feed.length === 0 ? <div className="text-gray-600">no activity yet</div> : feed.map((line, i) => <div key={i}>{line}</div>)}
                            <div ref={feedRef} />
                        </div>
                    </div>
                    <div className="flex flex-col">
                        <div className="text-gray-400 text-[10px] font-semibold uppercase tracking-wide mb-1">
                            To-do {todo.length === 0 ? '— complete ✓' : `(${todo.length})`}{liveTodo ? <span className="text-amber-400 ml-1 normal-case">· live</span> : null}
                        </div>
                        <div className="bg-black/40 border border-white/[0.06] rounded h-36 overflow-y-auto px-2.5 py-1.5 text-[11px] space-y-1">
                            {todo.length === 0 ? <div className="text-green-400">Every done-condition passes.</div> : todo.map((t, i) => {
                                const human = t.type === 'human'
                                return (
                                <div key={i} className="flex items-start justify-between gap-2 group text-gray-300">
                                    <span><span className={`font-mono ${human ? 'text-blue-400' : 'text-amber-400'}`}>[{t.component || '—'}]</span>{' '}
                                        <span className="text-gray-500">{human ? 'todo' : t.code}</span>: {t.detail}</span>
                                    {human ? (
                                        <button onClick={() => t.path && resolveTodo(t.path, true)} disabled={acting} className="text-green-400 hover:text-green-300 shrink-0">done</button>
                                    ) : (
                                        <button onClick={() => waive(t)} disabled={acting} title="accept as-is (waive)"
                                            className="text-gray-600 hover:text-amber-400 shrink-0 opacity-0 group-hover:opacity-100">waive</button>
                                    )}
                                </div>
                            )})}
                        </div>
                    </div>
                </div>
            </div>

            {/* ── SCROLLABLE BODY: tabbed artifact cards ─────────────────────────── */}
            <div className="flex-1 flex flex-col min-h-0">
                <div className="flex-shrink-0 flex gap-1 px-5 pt-3 border-b border-white/[0.08]">
                    <BodyTab id="story" active={bodyTab} onPick={setBodyTab} label="Story" />
                    <BodyTab id="scenes" active={bodyTab} onPick={setBodyTab} label={`Scenes${rawSceneIds.length ? ` (${rawSceneIds.length})` : ''}`} />
                    <BodyTab id="contract" active={bodyTab} onPick={setBodyTab}
                        label={`Contract${otherComponents.length + detail.waivers.length ? ` (${otherComponents.length + detail.waivers.length})` : ''}`} />
                </div>

                <div className="flex-1 overflow-y-auto px-5 py-4 space-y-6">
                    {bodyTab === 'story' && <>
                        {detail.spec.request && <p className="text-gray-400 text-sm">{detail.spec.request}</p>}

                        {premise && (
                            <section className="space-y-2">
                                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Premise</h3>
                                <JsonEditCard editable={editable} value={premise} onSave={v => saveComponent('premise', v)}
                                    title={detail.spec.title || 'premise'}>
                                    {premise.central_question && <p className="text-gray-200 text-sm italic">"{premise.central_question}"</p>}
                                    {Array.isArray(premise.endings) && premise.endings.length > 0 && (
                                        <p className="text-gray-400 text-xs mt-1"><span className="text-gray-500">Endings: </span>{premise.endings.map((e: any) => e.id).join(', ')}</p>
                                    )}
                                </JsonEditCard>
                            </section>
                        )}

                        {characters.length > 0 && (
                            <section className="space-y-2">
                                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Characters ({characters.length})</h3>
                                <div className="grid grid-cols-2 gap-2">
                                    {characters.map((c, i) => <CharacterCard key={c.id ?? i} char={c} editable={editable} onSave={ch => saveChar(i, ch)} />)}
                                </div>
                            </section>
                        )}
                    </>}

                    {bodyTab === 'scenes' && (
                        nodesComp && rawSceneIds.length > 0 ? (
                            <SceneNavigator runId={runId} nodesComp={nodesComp} orderedIds={orderedIds} activeId={activeId}
                                editable={editable} rewriting={rewriting} onPick={setActiveScene} onRewrite={rewrite} onSaved={touch} />
                        ) : (
                            <p className="text-gray-600 text-sm">No scenes yet — they appear here once the build writes nodes.</p>
                        )
                    )}

                    {bodyTab === 'contract' && <>
                        {otherComponents.length > 0 && (
                            <section className="space-y-2">
                                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Components</h3>
                                {otherComponents.map(id => (
                                    <ArtifactCard key={id} id={id} description={undefined} doneConditions={[]}
                                        value={detail.artifact?.[id]} editable={editable} onSave={v => saveComponent(id, v)} />
                                ))}
                            </section>
                        )}

                        {detail.waivers.length > 0 && (
                            <section className="space-y-1">
                                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Waived ({detail.waivers.length})</h3>
                                <ul className="space-y-1">
                                    {detail.waivers.map(w => (
                                        <li key={w.idkey} className="text-sm text-gray-400 flex items-center justify-between gap-2">
                                            <span className="font-mono text-xs text-gray-600 truncate">{w.note || w.idkey}</span>
                                            <button onClick={() => unwaive(w.idkey)} disabled={acting} className="text-gray-600 hover:text-gray-300 text-[11px] shrink-0">Reinstate</button>
                                        </li>
                                    ))}
                                </ul>
                            </section>
                        )}

                        {detail.built && (
                            <p className="text-gray-500 text-sm">Build packaged under the run's <span className="font-mono text-gray-400">game_output/</span>.</p>
                        )}

                        {otherComponents.length === 0 && detail.waivers.length === 0 && !detail.built && (
                            <p className="text-gray-600 text-sm">No extra components or waivers — the contract lives in the spec's done-conditions.</p>
                        )}
                    </>}
                </div>
            </div>
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
