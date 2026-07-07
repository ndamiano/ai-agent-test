import React, { useEffect, useState, useCallback, useRef } from 'react'
import { AlertTriangle } from 'lucide-react'
import { api, ApiError } from '../api/client'
import { useWebSocket } from '../contexts/WebSocketContext'
import { useAuth } from '../contexts/AuthContext'
import type { Game, GameDetail, TodoItem, WebSocketMessage } from '../types'
import ComponentBrowser from './browser'

export const formatElapsed = (secs: number): string => {
    const s = Math.max(0, Math.floor(secs))
    const m = Math.floor(s / 60)
    return `${m}:${String(s % 60).padStart(2, '0')}`
}

// error_parked's `identity` is Error.identity() serialized (type/code/component/path/ref, ""
// standing in for None) — convert to the idkey format (JSON array, null for absent path/ref) so
// the same waive endpoint the To-do pane uses can clear a parked error directly from the notice.
export const identityToIdkey = (identity: string[]): string =>
    JSON.stringify([identity[0], identity[1], identity[2], identity[3] || null, identity[4] || null])

type ParkedError = { identity: string[]; message: string }

// The build progress header: step/max as a bar, a running elapsed timer, current failing count,
// component done/failing summary, and — when present — a "parked, needs you" notice (Epic E).
const BuildProgressHeader: React.FC<{
    step: number; maxSteps: number; nFailing: number; elapsedSec: number
    componentsDone: number; componentsTotal: number
    parked: ParkedError[]; onWaive: (identity: string[]) => void
}> = ({ step, maxSteps, nFailing, elapsedSec, componentsDone, componentsTotal, parked, onWaive }) => {
    const pct = maxSteps > 0 ? Math.min(100, Math.round((step / maxSteps) * 100)) : 0
    return (
        <div className="bg-[#141414] border border-white/[0.06] rounded-lg px-3 py-2 space-y-1.5">
            <div className="flex items-center gap-3 text-[11px]">
                <span className="font-mono text-gray-300">step {step}{maxSteps ? ` / ${maxSteps}` : ''}</span>
                <span className="font-mono text-gray-300">{formatElapsed(elapsedSec)}</span>
                <span className={nFailing > 0 ? 'text-amber-400' : 'text-green-400'}>{nFailing} failing</span>
                <span className="ml-auto text-gray-500">{componentsDone}/{componentsTotal} components done</span>
            </div>
            <div className="h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
                <div className="h-full bg-blue-500 transition-[width] duration-500" style={{ width: `${pct}%` }} />
            </div>
            {parked.length > 0 && (
                <div className="bg-red-500/10 border border-red-500/30 rounded px-2.5 py-1.5 space-y-1">
                    <div className="text-red-300 text-[11px] font-semibold flex items-center gap-1">
                        <AlertTriangle size={12} /> Parked — needs you ({parked.length})
                    </div>
                    {parked.map((p, i) => (
                        <div key={i} className="flex items-start justify-between gap-2 text-[11px] text-gray-300">
                            <span><span className="font-mono text-red-400">[{p.identity[2] || '—'}]</span> {p.message}</span>
                            <button onClick={() => onWaive(p.identity)} title="accept as-is (waive)"
                                className="text-gray-500 hover:text-amber-400 shrink-0">waive</button>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
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

const AUTO_REASON = 'auto-included (foundation or required dependency)'
const FOUNDATION = ['human', 'assets']   // always-on, not human-removable

// The plan a freeze decision is made on: the engine + which mechanic-modules build this game and
// WHY each was picked (the proposer's justification, or an auto note for forced/dependency modules).
// This is the spec's actual contract now that done-conditions live in code. On a draft it's
// editable — remove/add a module or change sizing — so the review can act on what it reveals;
// every edit re-resolves the plan server-side (foundation forced, deps expanded, engine re-derived).
const SpecPlan: React.FC<{ spec: Record<string, any>; editable: boolean; busy: boolean; onAmend: (c: Record<string, any>) => void }>
    = ({ spec, editable, busy, onAmend }) => {
    const [catalog, setCatalog] = useState<{ id: string; description: string }[]>([])
    useEffect(() => { if (editable) api.listModules().then(setCatalog).catch(() => { }) }, [editable])

    const modules: string[] = Array.isArray(spec.modules) ? spec.modules : []
    const reasons: Record<string, string> = spec.module_reasons || {}
    const params: Record<string, any> = spec.params || {}
    const sizing = Object.entries(params).filter(([, v]) => typeof v === 'number') as [string, number][]
    const sss: Record<string, any> = spec.story_state_schema || {}
    const facts: any[] = Array.isArray(sss.established_facts) ? sss.established_facts : []
    const threads: any[] = Array.isArray(sss.open_threads) ? sss.open_threads : []
    const entities = sss.entity_states && typeof sss.entity_states === 'object' ? Object.keys(sss.entity_states) : []

    // The set we send on edit is the human-chosen modules (foundation/deps are re-derived server-side).
    const chosen = modules.filter(m => !FOUNDATION.includes(m))
    const available = catalog.filter(c => !modules.includes(c.id))
    const setModules = (ids: string[]) => onAmend({ modules: ids })
    const bumpSizing = (k: string, v: number) => onAmend({ params: { ...params, [k]: Math.max(1, v) } })

    return (
        <section className="space-y-2">
            <div className="flex items-center gap-2">
                <h3 className="text-gray-300 text-xs font-semibold uppercase tracking-wide">Plan</h3>
                {spec.engine && <Badge label={spec.engine} tone="blue" />}
                {spec.substrate && <Badge label={spec.substrate} tone="gray" />}
                {editable && <span className="text-gray-600 text-[10px] ml-auto">editable — engine is derived from your modules</span>}
            </div>
            <div className="bg-[#1a1a1a] border border-white/[0.06] rounded-lg px-3 py-2 space-y-1.5">
                <div className="text-gray-400 text-[10px] font-semibold uppercase tracking-wide">Modules ({modules.length})</div>
                <ul className="space-y-1">
                    {modules.map(id => {
                        const reason = reasons[id]
                        const auto = !reason || reason === AUTO_REASON
                        const missing = reason === '(reason missing)'
                        const removable = editable && !FOUNDATION.includes(id)
                        return (
                            <li key={id} className="flex gap-2 text-[12px] leading-snug items-baseline">
                                <span className="font-mono text-blue-300 shrink-0">{id}</span>
                                <span className={missing ? 'text-amber-400' : auto ? 'text-gray-600 italic' : 'text-gray-300'}>
                                    {auto && !missing ? 'auto-included' : reason}
                                </span>
                                {removable && (
                                    <button onClick={() => setModules(chosen.filter(m => m !== id))} disabled={busy}
                                        title="remove this module" className="ml-auto shrink-0 text-gray-600 hover:text-red-400 disabled:opacity-40">×</button>
                                )}
                            </li>
                        )
                    })}
                </ul>
                {editable && available.length > 0 && (
                    <select value="" disabled={busy} onChange={e => { if (e.target.value) setModules([...chosen, e.target.value]) }}
                        className="bg-black/40 border border-white/[0.1] rounded text-[11px] text-gray-300 px-1.5 py-1 w-full">
                        <option value="">+ add a module…</option>
                        {available.map(c => <option key={c.id} value={c.id} title={c.description}>{c.id} — {c.description.slice(0, 70)}</option>)}
                    </select>
                )}
                {sizing.length > 0 && (
                    <div className="pt-1.5 border-t border-white/[0.05]">
                        <div className="text-gray-400 text-[10px] font-semibold uppercase tracking-wide mb-1">Sizing</div>
                        <div className="flex flex-wrap gap-1.5">
                            {sizing.map(([k, v]) => editable ? (
                                <span key={k} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-white/[0.05] text-gray-300 font-mono">
                                    <button onClick={() => bumpSizing(k, v - 1)} disabled={busy} className="text-gray-500 hover:text-white disabled:opacity-40">−</button>
                                    {k}: {v}
                                    <button onClick={() => bumpSizing(k, v + 1)} disabled={busy} className="text-gray-500 hover:text-white disabled:opacity-40">+</button>
                                </span>
                            ) : (
                                <span key={k} className="px-1.5 py-0.5 rounded text-[10px] bg-white/[0.05] text-gray-400 font-mono">{k}: {v}</span>
                            ))}
                        </div>
                    </div>
                )}
                {(facts.length > 0 || threads.length > 0 || entities.length > 0) && (
                    <div className="pt-1.5 border-t border-white/[0.05] space-y-0.5 text-[11px] text-gray-500">
                        {facts.length > 0 && <div><span className="text-gray-600">facts:</span> {facts.join(' · ')}</div>}
                        {entities.length > 0 && <div><span className="text-gray-600">entities:</span> {entities.join(', ')}</div>}
                        {threads.length > 0 && <div><span className="text-gray-600">threads:</span> {threads.join(' · ')}</div>}
                    </div>
                )}
            </div>
        </section>
    )
}

const GameDetailView: React.FC<{ runId: string; onChanged: () => void }> = ({ runId, onChanged }) => {
    const { subscribe } = useWebSocket()
    const { refreshBalance } = useAuth()
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
    const [autoPause, setAutoPause] = useState(false)
    const [progress, setProgress] = useState<{ step: number; maxSteps: number; nFailing: number } | null>(null)
    const [startedAt, setStartedAt] = useState<number | null>(null)
    const [elapsedSec, setElapsedSec] = useState(0)
    const [parked, setParked] = useState<ParkedError[]>([])

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

    useEffect(() => {
        setFeed([]); setLiveTodo(null)
        setProgress(null); setStartedAt(null); setElapsedSec(0); setParked([])
        return load()
    }, [load])

    // A running elapsed timer while the build is live — ticks locally between build_step events
    // (which only fire per LLM call, i.e. irregularly) rather than sitting frozen between them.
    useEffect(() => {
        if (!building || startedAt == null) return
        const id = setInterval(() => setElapsedSec(Date.now() / 1000 - startedAt), 1000)
        return () => clearInterval(id)
    }, [building, startedAt])

    const waiveParked = (identity: string[]) => {
        const key = identityToIdkey(identity)
        setParked(prev => prev.filter(p => identityToIdkey(p.identity) !== key))
        act(() => api.waiveCheck(runId, key), 'Waive failed')
    }

    // Live build events for this run.
    useEffect(() => {
        const unsub = subscribe(runId, (msg: WebSocketMessage) => {
            switch (msg.type) {
                case 'build_started':
                    setBuilding(true); setStatus('running')
                    setFeed([`build started — ${msg.n_failing} checks failing`])
                    if (msg.todo) setLiveTodo(msg.todo)
                    setProgress({ step: 0, maxSteps: msg.max_steps ?? 0, nFailing: msg.n_failing ?? 0 })
                    setParked([])
                    if (msg.started_at != null) { setStartedAt(msg.started_at); setElapsedSec(0) }
                    break
                case 'build_step':
                    setFeed(prev => [...prev.slice(-60), `step ${msg.step}${msg.mode ? ` [${msg.mode}]` : ''}: ${msg.summary} — ${msg.n_failing} failing`])
                    if (msg.todo) setLiveTodo(msg.todo)
                    setProgress({ step: msg.step ?? 0, maxSteps: msg.max_steps ?? 0, nFailing: msg.n_failing ?? 0 })
                    // Re-sync the local timer to the backend's authoritative elapsed so drift
                    // between build_step events (which fire irregularly) never compounds.
                    if (msg.elapsed != null) { setStartedAt(Date.now() / 1000 - msg.elapsed); setElapsedSec(msg.elapsed) }
                    break
                case 'error_parked':
                    if (msg.identity) {
                        setParked(prev => prev.some(p => identityToIdkey(p.identity) === identityToIdkey(msg.identity!))
                            ? prev : [...prev, { identity: msg.identity!, message: msg.message ?? '' }])
                    }
                    setFeed(prev => [...prev.slice(-60), `⚑ parked — needs you: ${msg.message ?? ''}`])
                    break
                case 'build_paused':
                    setStatus('paused'); setFeed(prev => [...prev.slice(-60), '⏸ paused'])
                    break
                case 'auto_paused':
                    setFeed(prev => [...prev.slice(-60), `⏸ auto-paused after ${msg.component_id}`])
                    break
                case 'build_resumed':
                    setStatus('running'); setFeed(prev => [...prev.slice(-60), '▶ resumed'])
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
                    if (msg.ok) setParked([])   // nothing failing left, so nothing stays parked
                    load(); onChanged()
                    break
            }
        })
        return unsub
    }, [runId, subscribe, load, onChanged])

    const feedRef = useRef<HTMLDivElement>(null)
    useEffect(() => { feedRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [feed])

    // A build is actively writing only when running — when paused the executor is parked, so
    // hand-edits / compile / regenerate are safe.
    const running = building && status !== 'paused'
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
        catch (e) {
            setBuilding(false); setStatus('idle')
            if (e instanceof ApiError && e.status === 402) {
                const b = e.body ?? {}
                setError(`Out of credits — this build costs ${b.cost}, your balance is ${b.balance}.`)
            } else {
                setError(e instanceof Error ? e.message : 'Build failed')
            }
        }
        finally { setActing(false); refreshBalance() }  // a build spends credits — resync the header
    }
    const toggleAutoPause = async (enabled: boolean) => {
        setAutoPause(enabled)
        if (building) { try { await api.setAutoPause(runId, enabled) } catch { /* best effort */ } }
    }
    const pause = () => { setStatus('paused'); act(() => api.pauseGame(runId), 'Pause failed', false) }
    const resume = () => { setStatus('running'); act(() => api.resumeGame(runId), 'Resume failed', false) }
    const cancel = () => act(() => api.cancelGame(runId), 'Cancel failed', false)
    const compile = () => act(async () => {
        const r = await api.compileGame(runId, true)
        setFeed(prev => [...prev.slice(-60), r.ok ? '✓ packaged' : `✗ package failed: ${r.reason}`])
    }, 'Package failed')
    const regenerate = () => act(async () => {
        await api.regenerateAssets(runId)
        setFeed(prev => [...prev.slice(-60), '✓ images regenerated (recompile to repackage)'])
    }, 'Regenerate failed')
    const download = () => act(async () => {
        // Package into a self-contained build (engine bundled) before pulling it, so the user
        // never needs Ren'Py or Godot installed to play.
        setFeed(prev => [...prev.slice(-60), '⏳ packaging self-contained build…'])
        const r = await api.compileGame(runId, true)
        if (!r.ok) { setFeed(prev => [...prev.slice(-60), `✗ package failed: ${r.reason}`]); return }
        // Pull the packaged build over an authed fetch (token on the header) and save the blob —
        // no token in the URL. Revoke the object URL once the click has fired.
        const blob = await api.fetchDownloadBlob(runId)
        const href = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = href
        a.download = `${runId}.zip`
        document.body.appendChild(a)
        a.click()
        a.remove()
        URL.revokeObjectURL(href)
    }, 'Download failed', false)
    const componentIds: string[] = Object.keys(detail?.artifact ?? {})
    const addTodo = () => act(async () => {
        await api.addTodo(runId, newTodoComp || componentIds[0] || '', newTodoText)
        setNewTodoText('')
    }, 'Add todo failed')
    const resolveTodo = (id: string, done: boolean) => act(() => api.resolveTodo(runId, id, done), 'Update failed')
    const waive = (t: TodoItem) => act(() => api.waiveCheck(runId, t.idkey), 'Waive failed')
    const unwaive = (idkey: string) => act(() => api.unwaiveCheck(runId, idkey), 'Unwaive failed')
    const amend = (changes: Record<string, any>) => act(async () => {
        const d = await api.amendSpec(runId, changes)
        setDetail(d); setBuilding(d.building); setStatus(d.status)
    }, 'Edit failed', false)

    if (loading && !detail) return <div className="p-6 text-gray-500 text-sm">Loading…</div>
    if (error && !detail) return <div className="p-6 text-red-400 text-sm">Error: {error}</div>
    if (!detail) return null

    const statusTone = status === 'cancelled' ? 'gray' : (status === 'paused' || running) ? 'amber' : 'gray'
    const todo = liveTodo ?? detail.todo

    // Epic E1: a light per-component summary derived from data already on the to-do (no new
    // per-asset event plumbing) — components with an open error/todo are "failing", everything
    // else on disk counts as "done".
    const failingComponentIds = new Set(todo.map(t => t.component).filter(Boolean))
    const allComponentIds = new Set([...componentIds, ...failingComponentIds])
    const componentsTotal = allComponentIds.size
    const componentsDone = Math.max(0, componentsTotal - failingComponentIds.size)

    // Lifecycle stage drives which controls show — a draft is a review-and-approve page, not the
    // full build cockpit.
    const stage: 'draft' | 'building' | 'built' | 'ready' =
        !detail.frozen ? 'draft' : building ? 'building' : detail.built ? 'built' : 'ready'
    const planEditable = stage === 'draft'
    const showBuildArea = stage === 'building' || stage === 'built'

    return (
        <div className="h-full flex flex-col">
            {/* ── FIXED TOP: controls + input bar + side-by-side feeds ────────────── */}
            <div className="flex-shrink-0 border-b border-white/[0.08] px-5 pt-3 pb-3 space-y-3">
                <div className="flex items-center gap-2 flex-wrap">
                    <h2 className="text-white text-base font-semibold">{detail.spec.title || detail.run_id}</h2>
                    {detail.frozen ? <Badge label="frozen" tone="blue" /> : <Badge label="draft" tone="gray" />}
                    {detail.built ? <Badge label="built" tone="green" /> : null}
                    {building ? <Badge label={status === 'paused' ? 'paused' : 'building…'} tone={statusTone as any} /> : null}
                    <span className="text-gray-600 text-[11px] font-mono ml-auto">{detail.run_id}</span>
                </div>

                <div className="flex gap-2 flex-wrap items-center">
                    {stage === 'draft' && (
                        <button onClick={freeze} disabled={acting} title="lock the plan and let the build start"
                            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">Approve &amp; freeze</button>
                    )}
                    {stage === 'ready' && (
                        <button onClick={build} disabled={acting} className="bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">Generate game</button>
                    )}
                    {stage === 'built' && (
                        <button onClick={build} disabled={acting} className="bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">Regenerate</button>
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
                    {stage === 'built' && (
                        <button onClick={compile} disabled={acting || !editable} title={editable ? 'rebuild the package from the current components' : 'pause the build first'}
                            className="bg-white/[0.08] hover:bg-white/[0.14] disabled:opacity-40 text-gray-200 px-3 py-1.5 rounded text-xs font-medium">Package</button>
                    )}
                    {stage === 'built' && (
                        <button onClick={regenerate} disabled={acting || !editable || !detail.assets_exist}
                            title={!detail.assets_exist ? 'no images yet' : editable ? 'regenerate all images from the manifest' : 'pause the build first'}
                            className="bg-white/[0.08] hover:bg-white/[0.14] disabled:opacity-40 text-gray-200 px-3 py-1.5 rounded text-xs font-medium">Regenerate images</button>
                    )}
                    {(stage === 'ready' || stage === 'building') && (
                        <label className="flex items-center gap-1.5 text-gray-400 text-xs ml-1 cursor-pointer select-none">
                            <input type="checkbox" checked={autoPause} onChange={e => toggleAutoPause(e.target.checked)} className="accent-amber-500" />
                            pause after each part
                        </label>
                    )}
                    {stage !== 'draft' && (
                        <button onClick={download} disabled={acting || !editable} title={editable ? 'package a self-contained build and download it' : 'pause the build first'}
                            className="bg-white/[0.08] hover:bg-white/[0.14] disabled:opacity-40 text-gray-200 px-3 py-1.5 rounded text-xs font-medium ml-auto">Download</button>
                    )}
                </div>

                {stage === 'draft' && (
                    <div className="text-gray-500 text-xs">Review the plan below, edit it if needed, then approve to start building.</div>
                )}

                {error && <p className="text-red-400 text-xs">{error}</p>}

                {showBuildArea && <>
                    {progress && (
                        <BuildProgressHeader step={progress.step} maxSteps={progress.maxSteps} nFailing={progress.nFailing}
                            elapsedSec={elapsedSec} componentsDone={componentsDone} componentsTotal={componentsTotal}
                            parked={parked} onWaive={waiveParked} />
                    )}

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
                                {todo.length === 0 ? <div className="text-green-400">Every check passes.</div> : todo.map((t, i) => {
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

                    {detail.waivers.length > 0 && (
                        <div className="flex flex-col">
                            <div className="text-gray-400 text-[10px] font-semibold uppercase tracking-wide mb-1">Waived ({detail.waivers.length})</div>
                            <ul className="space-y-1">
                                {detail.waivers.map(w => (
                                    <li key={w.idkey} className="text-[11px] text-gray-400 flex items-center justify-between gap-2">
                                        <span className="font-mono text-gray-600 truncate">{w.note || w.idkey}</span>
                                        <button onClick={() => unwaive(w.idkey)} disabled={acting} className="text-gray-600 hover:text-gray-300 shrink-0">Reinstate</button>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                </>}
            </div>

            {/* ── SCROLLABLE BODY: one browser — a leading Spec tab (the plan/draft review) then a
                tab per component. Draft opens on Spec (no components yet); built opens on the first
                component. ── */}
            <div className="flex-1 flex flex-col min-h-0">
                <div className="flex-1 overflow-y-auto px-5 py-4 space-y-6">
                    <ComponentBrowser runId={runId} componentIds={componentIds} editable={editable}
                        specNode={<>
                            <SpecPlan spec={detail.spec as Record<string, any>} editable={planEditable} busy={acting} onAmend={amend} />
                            {detail.spec.request && <p className="text-gray-400 text-sm">{detail.spec.request}</p>}
                        </>} />
                </div>
            </div>
        </div>
    )
}

// Build/spec lifecycle events that change a row's badges or add a row — refresh the list on these.
const LIST_REFRESH_EVENTS = new Set([
    'spec_proposed', 'spec_frozen', 'build_started', 'build_paused', 'build_resumed',
    'component_complete', 'build_cancelled', 'build_done',
])

const GamesPanel: React.FC<{ focusRunId?: string | null }> = ({ focusRunId }) => {
    const { messages } = useWebSocket()
    const [games, setGames] = useState<Game[]>([])
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [selected, setSelected] = useState<string | null>(null)
    const seenMsgs = useRef(0)

    // F2 — chat→build continuity: when a chat-created run is surfaced (Layout switches here on the
    // spec_proposed event), auto-select it. The list-refresh effect below pulls it into the rail;
    // GameDetailView loads it by run_id directly, so selection works even before the list catches up.
    useEffect(() => { if (focusRunId) setSelected(focusRunId) }, [focusRunId])

    const refresh = useCallback(() => {
        setLoading(true)
        setError(null)
        api.listGames()
            .then(setGames)
            .catch(e => setError(e instanceof Error ? e.message : 'Failed to load games'))
            .finally(() => setLoading(false))
    }, [])

    useEffect(refresh, [refresh])

    // Live: re-pull the list when a build/spec event arrives, so badges track reality without a
    // manual Refresh. (A brand-new chat-created run still needs the chat→games link — deferred.)
    useEffect(() => {
        if (messages.length <= seenMsgs.current) { seenMsgs.current = messages.length; return }
        const fresh = messages.slice(seenMsgs.current)
        seenMsgs.current = messages.length
        if (fresh.some(m => LIST_REFRESH_EVENTS.has(m.type))) refresh()
    }, [messages, refresh])

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
