// A "game" is one build run (runs/<run_id>/), surfaced read-only by /api/games.

export interface Game {
    run_id: string
    title: string
    mode: string
    frozen: boolean
    built: boolean
    building: boolean
    mtime: number
}

// The brief (runs/<id>/spec.json) — `design` is freeform (genre/look/audio/scope/mechanics/
// win-lose), rendered read-only.
export interface Spec {
    request: string
    title: string
    design: Record<string, any>
    frozen: boolean
}

export type GameStatus = 'idle' | 'queued' | 'building' | 'fixing' | 'paused' | 'built'

export interface GameDetail {
    run_id: string
    spec: Spec
    frozen: boolean
    built: boolean
    building: boolean
    status: GameStatus
    queue_position: number | null
    auto_pause: boolean
    assets_exist: boolean
    play_url: string | null
    credits_spent: number
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
export type AssetKind = 'image' | 'mesh'
export type AssetStatus = 'ready' | 'rendering' | 'pending'
export interface GameAsset {
    id: string
    kind: AssetKind
    status: AssetStatus
    prompt: string
}

// The prompt log (admin-only): every llm turn any game spent, reconstructed from the durable jobs
// rows. The index carries sizes and the head of the system prompt; the bodies are tens of KB each,
// so a turn's full text is fetched on click.
export type PromptScope = 'all' | 'game' | 'platform'

// A bucket is one game's turns; game_id null is the platform's own (chat + spec drafting, which
// run before a game exists), so those turns are reachable too.
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

export interface SystemStatus {
    status: string
    llm_connected: boolean
    llm_model: string
    agent_count: number
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

// Generic envelope — the event bus broadcasts many shapes. Known fields are typed as optional so
// a consumer can narrow by `type` without a full discriminated union; anything else still falls
// through the index signature.
export type WebSocketMessage = {
    type: string
    run_id?: string
    task_id?: string
    timestamp?: string
    // build_queued — kind is 'build' | 'fix'
    position?: number
    kind?: string
    // fix_started / spec_amend_requested
    note?: string
    // build_started / build_step
    n_failing?: number
    step?: number
    max_steps?: number
    summary?: string
    // build_started carries wall-clock seconds (time.time()) the build began; build_step carries
    // `elapsed` seconds since that start, so the progress header can show a running timer.
    started_at?: number
    elapsed?: number
    // error_parked — an error that survived the fix-attempt cap without clearing.
    identity?: string[]
    message?: string
    // component_complete / auto_paused
    component_id?: string
    // build_done
    ok?: boolean
    steps?: number
    // spec_proposed / spec_frozen
    title?: string
    mode?: string
    // assets_done
    rendered?: number
    [key: string]: any
}
