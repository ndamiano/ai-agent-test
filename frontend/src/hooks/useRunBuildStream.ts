import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import { useWebSocket } from '../contexts/WebSocketContext'
import type { DurableEventRow, WebSocketMessage } from '../types'

// The persistent build stream for one run. The disappearing-status bug was structural: the build
// feed lived in component-local useState that React tears down on tab switch/remount. The cure is
// to stop storing and start DERIVING — fold the two durable sources that already survive:
//   1. the global websocket `messages` array (lives above the tab switch, so it persists), and
//   2. the /events replay (the backend's durable log, for reload/reconnect catch-up).
// One pure fold over both yields feed/progress/parked/startedAt, so the view holds no build state.

export type FeedTone = 'info' | 'good' | 'bad' | 'warn'

export interface FeedEntry {
    key: string
    text: string
    tone: FeedTone
    at: number  // ms epoch — the merge sorts by this so replay + live interleave correctly
}

export interface RunStream {
    feed: FeedEntry[]
    progress: { step: number } | null
    startedAt: number | null  // seconds epoch, for the elapsed timer
    parked: { message: string } | null
    skinning: boolean
}

// A source-agnostic event: replay rows and live websocket messages both normalize to this so the
// fold never branches on where an event came from.
interface NormEvent {
    key: string
    type: string
    at: number  // ms epoch
    step?: number
    summary?: string
    elapsed?: number
    started_at?: number
    message?: string
    component_id?: string
    ok?: boolean
    rendered?: number
    note?: string
}

const fromRow = (row: DurableEventRow): NormEvent => ({
    key: `d${row.id}`,
    type: row.kind,
    at: row.created_at * 1000,
    ...row.payload,
})

const parseTs = (ts: string | undefined): number | null => {
    if (!ts) return null
    const ms = Date.parse(ts)
    return Number.isNaN(ms) ? null : ms
}

const fromLive = (msg: WebSocketMessage, i: number): NormEvent => ({
    ...msg,
    key: `l${i}`,
    type: msg.type,
    at: parseTs(msg.timestamp) ?? Date.now(),
})

// Merge replay + live without double-counting. Every emitted event is durable AND (while a socket
// is open) also arrives live, so the two overlap. Split on time: replay owns everything strictly
// before the live window began (the prefix a reload/reconnect missed), live owns the rest. Both
// carry server-stamped times, so the cutoff is exact — no content-based dedup guesswork.
export function mergeEvents(rows: DurableEventRow[], live: WebSocketMessage[]): NormEvent[] {
    const liveEvents = live.map(fromLive)
    const firstLiveTs = liveEvents.reduce(
        (min, e) => (e.at < min ? e.at : min),
        Number.POSITIVE_INFINITY,
    )
    const replayPrefix = rows.map(fromRow).filter(e => e.at < firstLiveTs)
    return [...replayPrefix, ...liveEvents].sort((a, b) => a.at - b.at)
}

const feedLine = (e: NormEvent): { text: string; tone: FeedTone } | null => {
    switch (e.type) {
        case 'fix_started':
            return { text: `⚒ fixing — ${e.note ?? ''}`, tone: 'warn' }
        case 'build_started':
            return { text: 'build started', tone: 'info' }
        case 'build_step':
            return { text: `step ${e.step}: ${e.summary}`, tone: 'info' }
        case 'error_parked':
            return { text: `⚑ parked — needs a fix note: ${e.message ?? ''}`, tone: 'bad' }
        case 'build_paused':
            return { text: '⏸ paused', tone: 'warn' }
        case 'build_resumed':
            return { text: '▶ resumed', tone: 'info' }
        case 'component_complete':
            return { text: `✓ ${e.component_id} complete`, tone: 'good' }
        case 'build_done':
            return e.ok
                ? { text: '✓ build complete', tone: 'good' }
                : { text: '✗ build ended with failures', tone: 'bad' }
        case 'assets_started':
            return { text: '⏳ skinning assets…', tone: 'info' }
        case 'assets_done':
            return e.ok
                ? { text: `✓ assets rendered (${e.rendered ?? 0})`, tone: 'good' }
                : { text: '✗ asset skin failed', tone: 'bad' }
        case 'prompt_updated':
            return { text: '✎ prompt edited', tone: 'info' }
        default:
            return null  // prompt_proposed, build_queued, … only refresh state — no feed line
    }
}

// The pure reducer: an ordered event stream → the streamy display state. Held nowhere; recomputed.
export function foldStream(events: NormEvent[]): RunStream {
    const feed: FeedEntry[] = []
    let progress: RunStream['progress'] = null
    let startedAt: number | null = null
    let parked: RunStream['parked'] = null
    let skinning = false

    for (const e of events) {
        const line = feedLine(e)
        if (line) feed.push({ key: e.key, text: line.text, tone: line.tone, at: e.at })

        switch (e.type) {
            case 'fix_started':
            case 'build_resumed':
                parked = null
                break
            case 'build_started':
                parked = null
                progress = { step: 0 }
                if (e.started_at != null) startedAt = e.started_at
                break
            case 'build_step':
                progress = { step: e.step ?? 0 }
                // build_step carries elapsed since start; recover the absolute start from it so the
                // timer stays accurate even when the build_started event was never seen (reload).
                if (e.elapsed != null) startedAt = e.at / 1000 - e.elapsed
                break
            case 'error_parked':
                parked = { message: e.message ?? '' }
                break
            case 'assets_started':
                skinning = true
                break
            case 'assets_done':
                skinning = false
                break
        }
    }
    return { feed, progress, startedAt, parked, skinning }
}

const EMPTY: RunStream = { feed: [], progress: null, startedAt: null, parked: null, skinning: false }

// The hook. Replay is refetched whenever the run changes or the socket reconnects (a reconnect
// clears the live `messages` buffer, so the durable log must refill the gap). No new context is
// needed: `messages` already outlives the tab switch, and replay is cheap to refetch on remount.
export function useRunBuildStream(runId: string | null): RunStream {
    const { messages, connected } = useWebSocket()
    const [rows, setRows] = useState<DurableEventRow[]>([])
    const reqId = useRef(0)

    useEffect(() => {
        if (!runId) { setRows([]); return }
        const id = ++reqId.current
        api.getGameEvents(runId).then(
            fetched => { if (id === reqId.current) setRows(fetched) },
            () => { if (id === reqId.current) setRows([]) },
        )
        // `connected` in the deps refetches on reconnect (messages was just wiped).
    }, [runId, connected])

    const live = useMemo(
        () => (runId ? messages.filter(m => m.run_id === runId) : []),
        [messages, runId],
    )

    return useMemo(() => {
        if (!runId) return EMPTY
        return foldStream(mergeEvents(rows, live))
    }, [runId, rows, live])
}
