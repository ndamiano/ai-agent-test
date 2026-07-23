import React, { useEffect, useState, useCallback, useRef } from 'react'
import { api, ApiError } from '../api/client'
import { useWebSocket } from '../contexts/WebSocketContext'
import { useAuth } from '../contexts/AuthContext'
import type { Game, GameDetail, WebSocketMessage } from '../types'
import { useRunBuildStream } from '../hooks/useRunBuildStream'
import { Badge } from './cockpit/Badge'
import { SpecCard } from './cockpit/SpecCard'
import { AssetGallery } from './cockpit/AssetGallery'
import { BuildFeed, ParkedCard } from './cockpit/BuildFeed'

export const formatElapsed = (secs: number): string => {
    const s = Math.max(0, Math.floor(secs))
    const m = Math.floor(s / 60)
    return `${m}:${String(s % 60).padStart(2, '0')}`
}

export type Stage = 'draft' | 'building' | 'built' | 'ready'

// Lifecycle stage drives which controls show — a draft is a review-and-freeze page, not the full
// build cockpit; `built` unlocks skin/fix/play but still allows a rebuild.
export const stageFor = (frozen: boolean, building: boolean, built: boolean): Stage =>
    !frozen ? 'draft' : building ? 'building' : built ? 'built' : 'ready'

// The build progress header: a step counter (not step/max — a build finishes when the gates
// pass, and a cap read as a countdown), a running elapsed timer, current failing count.
const BuildProgressHeader: React.FC<{
    step: number; nFailing: number; elapsedSec: number
}> = ({ step, nFailing, elapsedSec }) => (
    <div className="bg-[#141414] border border-white/[0.06] rounded-lg px-3 py-2">
        <div className="flex items-center gap-3 text-[11px]">
            <span className="font-mono text-gray-300">step {step}</span>
            <span className="font-mono text-gray-300">{formatElapsed(elapsedSec)}</span>
            <span className={nFailing > 0 ? 'text-amber-400' : 'text-green-400'}>{nFailing} failing</span>
        </div>
    </div>
)

// The compute budget as an obfuscated draining bar, 0..1 — users see a bar, never seconds (the
// plan's contract; seconds_used never reaches the client). null ⇒ uncharged, no bar. Tolerates the
// backend sending either a 0..1 fraction or a 0..100 percentage.
export const budgetFraction = (pct: number | null | undefined): number | null => {
    if (pct == null) return null
    return Math.max(0, Math.min(1, pct > 1 ? pct / 100 : pct))
}

const ComputeBar: React.FC<{ detail: GameDetail }> = ({ detail }) => {
    const frac = budgetFraction(detail.budget_pct_remaining)
    if (frac == null) return null
    const color = frac < 0.1 ? 'bg-red-500' : frac < 0.34 ? 'bg-amber-500' : 'bg-green-500'
    return (
        <div className="flex items-center gap-2" title="compute remaining for this game">
            <span className="text-gray-500 text-[10px] font-semibold uppercase tracking-wide">Compute</span>
            <div className="flex-1 h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
                <div className={`h-full ${color} transition-[width] duration-700`} style={{ width: `${frac * 100}%` }} />
            </div>
        </div>
    )
}

const GameDetailView: React.FC<{ runId: string; onChanged: () => void }> = ({ runId, onChanged }) => {
    const { subscribe } = useWebSocket()
    const { refreshBalance } = useAuth()
    const stream = useRunBuildStream(runId)
    const [detail, setDetail] = useState<GameDetail | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [building, setBuilding] = useState(false)
    const [status, setStatus] = useState<string>('idle')
    const [acting, setActing] = useState(false)
    const [skinPending, setSkinPending] = useState(false)
    const [fixNote, setFixNote] = useState('')
    const [autoPause, setAutoPause] = useState(false)
    const [elapsedSec, setElapsedSec] = useState(0)
    // Bumped on assets_done so the gallery re-reads its manifest off the static mount.
    const [assetsVersion, setAssetsVersion] = useState(0)

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

    // Detail (built/status/budget) reloads on run change; feed/progress now come from the persistent
    // stream, so they survive tab switches and reloads with no local reset here.
    useEffect(() => { setSkinPending(false); return load() }, [load])

    // A running elapsed timer while building — ticks locally off the stream's authoritative start.
    useEffect(() => {
        if (!building || stream.startedAt == null) { setElapsedSec(0); return }
        const start = stream.startedAt
        const tick = () => setElapsedSec(Date.now() / 1000 - start)
        tick()
        const id = setInterval(tick, 1000)
        return () => clearInterval(id)
    }, [building, stream.startedAt])

    // While building, re-pull the detail on a slow tick so the compute bar drains — the budget
    // moves on job completions, which no websocket event carries.
    useEffect(() => {
        if (!building) return
        const id = setInterval(load, 10000)
        return () => clearInterval(id)
    }, [building, load])

    // A slim handler for the few events that change durable state (building/status/detail). The
    // feed/progress/parked display is derived by the stream hook, not mutated here.
    useEffect(() => {
        const unsub = subscribe(runId, (msg: WebSocketMessage) => {
            switch (msg.type) {
                case 'spec_proposed':
                case 'spec_frozen':
                    load(); break
                case 'fix_started':
                    setBuilding(true); setStatus('fixing'); break
                case 'build_started':
                    setBuilding(true); setStatus('running'); break
                case 'build_paused':
                case 'auto_paused':
                    setStatus('paused'); break
                case 'build_resumed':
                    setStatus('running'); break
                case 'build_done':
                    setBuilding(false); setStatus('built'); load(); onChanged(); break
                case 'assets_done':
                    setSkinPending(false); setAssetsVersion(v => v + 1); load(); onChanged(); break
            }
        })
        return unsub
    }, [runId, subscribe, load, onChanged])

    // Once the real skin signal is live, drop the optimistic pending flag.
    useEffect(() => { if (stream.skinning) setSkinPending(false) }, [stream.skinning])

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
    const skin = () => act(async () => { setSkinPending(true); await api.skinAssets(runId) }, 'Skin failed', false)
    const submitFix = () => {
        const note = fixNote.trim()
        if (!note) return
        setFixNote('')
        act(async () => { await api.fixGame(runId, note); setBuilding(true); setStatus('fixing') }, 'Fix failed', false)
    }

    if (loading && !detail) return <div className="p-6 text-gray-500 text-sm">Loading…</div>
    if (error && !detail) return <div className="p-6 text-red-400 text-sm">Error: {error}</div>
    if (!detail) return null

    const stage = stageFor(detail.frozen, building, detail.built)
    const statusTone: 'amber' | 'gray' = (status === 'paused' || building) ? 'amber' : 'gray'
    const skinning = stream.skinning || skinPending
    const showBuildArea = stage === 'building' || stage === 'built' || stream.feed.length > 0

    return (
        <div className="h-full flex flex-col">
            <div className="flex-shrink-0 border-b border-white/[0.08] px-5 pt-3 pb-3 space-y-3">
                <div className="flex items-center gap-2 flex-wrap">
                    <h2 className="text-white text-base font-semibold">{detail.spec.title || detail.run_id}</h2>
                    <Badge label={detail.mode.toUpperCase()} tone="blue" />
                    {detail.frozen ? <Badge label="frozen" tone="blue" /> : <Badge label="draft" tone="gray" />}
                    {detail.built ? <Badge label="built" tone="green" /> : null}
                    {stream.parked ? <Badge label="parked" tone="red" /> : null}
                    {building ? <Badge label={status === 'paused' ? 'paused' : status === 'fixing' ? 'fixing…' : 'building…'} tone={statusTone} /> : null}
                    <span className="text-gray-600 text-[11px] font-mono ml-auto">{detail.run_id}</span>
                </div>

                <div className="flex gap-2 flex-wrap items-center">
                    {stage === 'draft' && (
                        <button onClick={freeze} disabled={acting} title="lock the spec and let the build start"
                            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">Freeze</button>
                    )}
                    {stage !== 'draft' && !building && (
                        <button onClick={build} disabled={acting} className="bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">
                            {detail.built ? 'Rebuild' : 'Build'}
                        </button>
                    )}
                    {building && (status === 'running' || status === 'fixing') && (
                        <button onClick={pause} disabled={acting} className="bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">Pause</button>
                    )}
                    {building && status === 'paused' && (
                        <button onClick={resume} disabled={acting} className="bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs font-medium">Resume</button>
                    )}
                    {(stage === 'ready' || stage === 'building') && (
                        <label className="flex items-center gap-1.5 text-gray-400 text-xs ml-1 cursor-pointer select-none">
                            <input type="checkbox" checked={autoPause} onChange={e => toggleAutoPause(e.target.checked)} className="accent-amber-500" />
                            pause after each step
                        </label>
                    )}
                    {stage === 'built' && detail.play_url && (
                        <a href={detail.play_url} target="_blank" rel="noreferrer"
                            className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-xs font-medium ml-auto">Play</a>
                    )}
                </div>

                {stage === 'draft' && (
                    <div className="text-gray-500 text-xs">Review the spec below, then freeze to start building.</div>
                )}

                <ComputeBar detail={detail} />

                {error && <p className="text-red-400 text-xs">{error}</p>}

                {stream.parked && (
                    <ParkedCard message={stream.parked.message} note={fixNote} setNote={setFixNote}
                        onSubmit={submitFix} busy={acting} />
                )}

                {showBuildArea && <>
                    {building && stream.progress && (
                        <BuildProgressHeader step={stream.progress.step} nFailing={stream.progress.nFailing}
                            elapsedSec={elapsedSec} />
                    )}

                    {stage === 'built' && !stream.parked && (
                        <div className="flex gap-2">
                            <input value={fixNote} onChange={e => setFixNote(e.target.value)}
                                onKeyDown={e => { if (e.key === 'Enter' && fixNote.trim()) submitFix() }}
                                placeholder="Describe what's wrong — patches the built game…"
                                className="flex-1 bg-black/40 border border-white/[0.1] rounded text-xs text-gray-200 px-2 py-1.5" />
                            <button onClick={submitFix} disabled={acting || !fixNote.trim()}
                                className="bg-blue-600/80 hover:bg-blue-700 disabled:opacity-40 text-white px-3 py-1.5 rounded text-xs">Fix</button>
                        </div>
                    )}

                    <BuildFeed feed={stream.feed} />
                </>}
            </div>

            <div className="flex-1 flex flex-col min-h-0">
                <div className="flex-1 overflow-y-auto px-5 py-4 space-y-6">
                    {(stage === 'built' || detail.assets_exist) && (
                        <AssetGallery runId={runId} version={assetsVersion} skinning={skinning}
                            canSkin={stage === 'built'} onSkin={skin} acting={acting} />
                    )}
                    <SpecCard spec={detail.spec} />
                </div>
            </div>
        </div>
    )
}

// Build/spec lifecycle events that change a row's badges or add a row — refresh the list on these.
const LIST_REFRESH_EVENTS = new Set([
    'spec_proposed', 'spec_frozen', 'build_started', 'build_paused', 'build_resumed',
    'component_complete', 'build_done', 'assets_started', 'assets_done',
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
                <div className="flex-shrink-0 px-3 py-2 flex items-center border-b border-white/[0.06]">
                    <span className="text-gray-400 text-xs font-semibold uppercase tracking-wide">Games</span>
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
                                <Badge label={g.mode.toUpperCase()} tone="gray" />
                                {g.frozen ? <Badge label="frozen" tone="blue" /> : <Badge label="draft" tone="gray" />}
                                {g.built ? <Badge label="built" tone="green" /> : null}
                                {g.building ? <Badge label="building…" tone="amber" /> : null}
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
