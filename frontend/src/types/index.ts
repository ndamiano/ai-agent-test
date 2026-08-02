export interface Game {
    run_id: string
    title: string
    built: boolean
    building: boolean
    paused: boolean
}

export type GameStatus = 'idle' | 'queued' | 'building' | 'fixing' | 'paused' | 'built'

export interface GameDetail {
    run_id: string
    // The text the build sends as its user message — editable here, byte for byte what runs.
    prompt: string
    title: string
    built: boolean
    building: boolean
    status: GameStatus
    assets_exist: boolean
    // A previous attempt left files in the game folder — what a from-scratch build discards.
    has_game: boolean
    // Compute budget for the numberless bar: fraction remaining, 0..1, or null when the game is
    // uncharged (no bar). Raw seconds — especially seconds_used — are deliberately never surfaced.
    budget_pct_remaining: number | null
}

// One row of the durable build/spec event log (GET /api/games/:id/events). Written by the backend's
// _emit: the event name is in `kind` and the fields live in `payload` (flattened) — a different
// shape from the live websocket copy, so both are normalized to a common event before folding.
export interface DurableEventRow {
    id: number
    game_id: string
    build_id: string | null
    kind: string
    payload: Record<string, any>
    created_at: number
}

// An asset the game declared in its own assets.json (GET /api/games/:id/assets). The bytes are
// fetched separately from the authed blob route (never the public /play mount), so there is no url
// here — the gallery builds an object URL from an authed fetch. `status`: ready (on disk),
// rendering (a batch is live), or pending (declared, not yet rendered).
export type AssetKind = 'sprite' | 'tile' | 'scene' | 'mesh'
export type AssetStatus = 'ready' | 'rendering' | 'pending'
export interface GameAsset {
    id: string
    kind: AssetKind
    status: AssetStatus
    prompt: string
    // What the render came back BROKEN as, if anything — never whether it is any good.
    defect: string | null
}

// The prompt log (admin-only): every llm turn any game spent, reconstructed from the durable jobs
// rows. The index carries sizes and the head of the system prompt; the bodies are tens of KB each,
// so a turn's full text is fetched on click.
export type PromptScope = 'all' | 'game' | 'platform'

export interface PromptBucket {
    game_id: string | null
    turns: number
    first_at: number
    last_at: number
    exec_seconds: number
    title: string | null
    mode: string | null
    status: string | null
    user_id: string | null
}

export interface PromptTurn {
    id: string
    game_id: string | null
    build_id: string | null
    status: string
    model: string | null
    created_at: number
    started_at: number | null
    finished_at: number | null
    exec_seconds: number | null
    error: string | null
    metadata: Record<string, any>
    payload_chars: number | null
    // A turn's system prompt is its prompt FILE rendered, so the hash groups a log into the handful
    // of prompts that produced it — the file name itself is never recorded anywhere.
    system_hash: string
    system_head: string | null
    system_chars: number
    n_messages: number | null
}

export interface PromptMessage {
    role: string
    kind: 'text' | 'tool_call' | 'tool_result'
    name: string | null
    text: string
}

export interface PromptDetail {
    id: string
    game_id: string | null
    build_id: string | null
    status: string
    model: string | null
    created_at: number
    exec_seconds: number | null
    error: string | null
    stage: string | null
    reasoning: string | null
    max_output_tokens: number | null
    system: string
    messages: PromptMessage[]
    tools: { name: string; description: string; parameters: Record<string, any> }[]
    response: {
        text: string
        tool_calls: { name: string; arguments: string }[]
        usage: Record<string, any>
    } | null
}

// Admin queue snapshot (GET /api/admin/queues). GPU-SECONDS only — no dollar conversion.
export interface QueueRow {
    queue: string
    pending: number
    claimed: number
    oldest_pending_age_seconds: number | null
    workers_live: number
    workers_max: number
    est_seconds: number
    backlog_seconds: number
    paid_all: number
    billed_all: number
    paid_24h: number
    billed_24h: number
}

export interface AdminQueues {
    queues: QueueRow[]
    totals: {
        pending: number
        claimed: number
        workers_live: number
        backlog_seconds: number
        paid_all: number
        billed_all: number
        paid_24h: number
        billed_24h: number
    }
}

// Generic envelope — the event bus broadcasts many shapes. The fields the UI actually reads are
// typed as optional so a consumer can narrow by `type` without a full discriminated union; the rest
// of what the bus sends still falls through the index signature.
export type WebSocketMessage = {
    type: string
    run_id?: string
    timestamp?: string
    // build_started / build_step
    step?: number
    summary?: string
    // build_started carries wall-clock seconds (time.time()) the build began; build_step carries
    // `elapsed` seconds since that start, so the progress header can show a running timer.
    started_at?: number
    elapsed?: number
    // build_done / assets_done
    ok?: boolean
    error?: string
    rendered?: number
    [key: string]: any
}
