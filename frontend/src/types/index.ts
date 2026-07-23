// A "game" is one build run (runs/<run_id>/), surfaced read-only by /api/games.

export type GameMode = '2d' | '3d'

export interface Game {
    run_id: string
    title: string
    mode: GameMode
    frozen: boolean
    built: boolean
    building: boolean
    mtime: number
}

// The codegen spec (runs/<id>/spec.json) — `design` is freeform (genre/entities/controls/
// mechanics/win-lose/...), rendered read-only. There is no engine/substrate/modules/params.
export interface Spec {
    request: string
    title: string
    mode: GameMode
    design: Record<string, any>
    frozen: boolean
}

export type GameStatus = 'idle' | 'queued' | 'building' | 'fixing' | 'paused' | 'built'

export interface GameDetail {
    run_id: string
    spec: Spec
    mode: GameMode
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

// An asset the built game uses (GET /api/games/:id/assets). The bytes are fetched separately from
// the authed blob route (never the public /play mount), so there is no url here — the gallery builds
// an object URL from an authed fetch. `status`: ready (on disk), rendering (a skin batch is live),
// or pending (planned, not yet rendered).
export type AssetKind = 'sprite' | 'mesh'
export type AssetStatus = 'ready' | 'rendering' | 'pending'
export interface GameAsset {
    id: string
    kind: AssetKind
    status: AssetStatus
    w?: number
    h?: number
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
    mode?: GameMode
    // assets_done
    rendered?: number
    [key: string]: any
}
