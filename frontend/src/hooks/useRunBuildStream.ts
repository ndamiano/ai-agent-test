import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import { useWebSocket } from '../contexts/WebSocketContext'
import type { DurableEventRow, WebSocketMessage } from '../types'

export type FeedTone = 'info' | 'good' | 'bad' | 'warn'

export interface FeedEntry {
    key: string
    text: string
    tone: FeedTone
    at: number  // ms epoch — the merge sorts by this so replay + live interleave correctly
}

export interface RunStream {
    feed: FeedEntry[]
    progress: { step: number; summary: string | null } | null
    startedAt: number | null  // seconds epoch, for the elapsed timer
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
    ok?: boolean
    error?: string
    rendered?: number
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
        case 'build_started':
            return { text: 'build started', tone: 'info' }
        case 'build_step':
            return { text: `step ${e.step}: ${e.summary}`, tone: 'info' }
        case 'build_paused':
            return { text: '⏸ paused', tone: 'warn' }
        case 'build_resumed':
            return { text: '▶ resumed', tone: 'info' }
        case 'build_done':
            return e.ok
                ? { text: '✓ build complete', tone: 'good' }
                : { text: `✗ build failed${e.error ? ` — ${e.error}` : ''}`, tone: 'bad' }
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

export function foldStream(events: NormEvent[]): RunStream {
    const feed: FeedEntry[] = []
    let progress: RunStream['progress'] = null
    let startedAt: number | null = null
    let skinning = false

    for (const e of events) {
        const line = feedLine(e)
        if (line) feed.push({ key: e.key, text: line.text, tone: line.tone, at: e.at })

        switch (e.type) {
            case 'build_started':
                progress = { step: 0, summary: null }
                if (e.started_at != null) startedAt = e.started_at
                break
            case 'build_step':
                progress = { step: e.step ?? 0, summary: e.summary ?? null }
                // build_step carries elapsed since start; recover the absolute start from it so the
                // timer stays accurate even when the build_started event was never seen (reload).
                if (e.elapsed != null) startedAt = e.at / 1000 - e.elapsed
                break
            case 'assets_started':
                skinning = true
                break
            case 'assets_done':
                skinning = false
                break
        }
    }
    return { feed, progress, startedAt, skinning }
}

const EMPTY: RunStream = { feed: [], progress: null, startedAt: null, skinning: false }

// Replay is refetched whenever the run changes or the socket reconnects (a reconnect clears the
// live `messages` buffer, so the durable log must refill the gap).
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
