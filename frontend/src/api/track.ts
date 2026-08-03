import { getAuthToken } from './client'

// Fire-and-forget usage analytics: queue locally, flush as one batch. Nothing here may ever
// break or slow the app — every failure is swallowed, and a row the server dislikes is the
// server's to drop (POST /api/events).

type Payload = Record<string, unknown>
interface Row { kind: string; payload: Payload; ts: number; run_id?: string }

const FLUSH_MS = 10_000
const MAX_QUEUE = 100

let queue: Row[] = []
let timer: number | null = null

function send(rows: Row[]): void {
    // The API is bearer-header-only, which rules out sendBeacon (no headers) — `keepalive` is
    // what lets this fetch outlive a closing page instead.
    const token = getAuthToken()
    if (!token) return
    try {
        void fetch('/api/events', {
            method: 'POST',
            keepalive: true,
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
            body: JSON.stringify(rows),
        }).catch(() => { /* analytics is never the app's problem */ })
    } catch { /* fetch itself can throw synchronously (e.g. no window.fetch) */ }
}

export function flush(): void {
    if (timer != null) { clearTimeout(timer); timer = null }
    if (queue.length === 0) return
    const rows = queue
    queue = []
    send(rows)
}

export function track(kind: string, payload: Payload = {}): void {
    if (queue.length >= MAX_QUEUE) return
    const { run_id, ...rest } = payload
    const row: Row = { kind, payload: rest, ts: Date.now() }
    if (typeof run_id === 'string') row.run_id = run_id
    queue.push(row)
    if (timer == null) timer = window.setTimeout(flush, FLUSH_MS)
}

// pagehide is the last reliable moment before the tab dies; hidden covers mobile tab switches
// that never fire pagehide.
if (typeof window !== 'undefined') {
    window.addEventListener('pagehide', flush)
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') flush()
    })
}
