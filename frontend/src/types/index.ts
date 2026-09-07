export interface Game {
    run_id: string
    title: string
    status: GameStatus
    built: boolean
    building: boolean
    paused: boolean
}

// 'held': the safety screen held the build for human review — frozen and shown as unavailable.
export type GameStatus = 'idle' | 'queued' | 'building' | 'fixing' | 'changing' | 'paused' | 'built' | 'held'

export interface GameDetail {
    run_id: string
    // The user's request, verbatim — kept beside whatever builds.
    ask: string
    // The text the build sends as its user message — editable here, byte for byte what runs.
    // null while the designer is still writing it from `ask`.
    prompt: string | null
    title: string
    built: boolean
    building: boolean
    status: GameStatus
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
export type AssetKind = 'sprite' | 'tile' | 'scene' | 'mesh' | 'anim'
// 'blocked': the render was refused by the safety screen; a redraw with a new prompt clears it.
export type AssetStatus = 'ready' | 'rendering' | 'pending' | 'blocked'
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

// A safety refusal as the admin surface lists it (GET /api/admin/violations). `matched` is the
// matched term(s) or refusal reason — never the flagged content itself.
export interface AdminViolation {
    id: number
    user_id: string | null
    handle: string | null
    game_id: string | null
    source: string
    category: string
    matched: string
    created_at: number
}

// Admin queue snapshot (GET /api/admin/queues): depth, the next jobs in claim order, and the
// fleet with what each worker holds. Visualization only — no spend.
export interface QueuedJob {
    id: string
    game_id: string | null
    build_id: string | null
    waiting_seconds: number
    est_seconds: number
}

// `booting` is a pod the scaler lists that no worker has registered from yet: id is the pod's
// name, gpu_type and usd_per_hour are unknown, uptime is the pod's age.
export interface WorkerRow {
    id: string
    state: 'busy' | 'idle' | 'booting'
    gpu_type: string | null
    usd_per_hour: number | null
    source: string | null
    pod_id: string | null
    spawned_at: number
    last_seen_seconds: number
    busy_seconds: number
    job: { id: string; game_id: string | null; build_id: string | null
           running_seconds: number; est_seconds: number } | null
}

// Provider stock refusals (the scaler wanted a pod and RunPod had none to give), from the durable
// record: counts per window, the latest, and the outage under way — active when the last refusal
// is within two scaler ticks, active_since/active_count describing the unbroken run.
// Every StartPod the scaler executed, by kind of outcome, from a daily rollup that is never
// pruned; since is the earliest day with a row (null when none).
export interface PodRequestTotals {
    attempts: number
    stock_refusals: number
    other_refusals: number
    since: string | null
}

export interface Stockouts {
    totals_60d: PodRequestTotals
    totals_all: PodRequestTotals
    last_1h: number
    last_24h: number
    last_7d: number
    last_at: number | null
    last_error: string | null
    active: boolean
    active_since: number | null
    active_count: number
}

export interface QueueRow {
    queue: string
    pending: number
    claimed: number
    oldest_pending_age_seconds: number | null
    workers_live: number
    workers_max: number
    est_seconds: number
    backlog_seconds: number
    next: QueuedJob[]
    workers: WorkerRow[]
    stockouts: Stockouts
}

// Effective cost (GET /api/admin/costs), per window and per card. alive_* is RunPod's ledger
// (pod wall-clock and what it billed), null when the ledger is unreachable or has no row for the
// card; worked_* is the seconds our jobs ran on it, priced at our rate table. games counts games
// whose full build finished in the window, averaged over every job they ever ran and every
// change round they asked for.
export interface CostGpu {
    gpu: string
    alive_seconds: number | null
    alive_usd: number | null
    worked_seconds: number
    worked_usd: number
}

export interface CostWindow {
    label: string
    gpus: CostGpu[]
    games: { n: number; avg_gpu_hours: number | null; avg_usd: number | null; avg_changes: number | null }
}

export interface AdminCosts {
    runpod_reachable: boolean
    windows: CostWindow[]
    ghost_30d: { pods: number; amount_usd: number; billed_seconds: number } | null
}

export interface AdminAnalytics {
    kinds: string[]
    days: { day: string; users: number; kinds: Record<string, number> }[]
}

export interface AdminQueues {
    queues: QueueRow[]
    totals: { pending: number; claimed: number; workers_live: number; backlog_seconds: number }
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

// docs/game_rubric.md. A dimension's null score is `n/a` — not what this game is for — and is not
// a zero; nothing here sums, the verdicts are separate holistic calls.
export interface GradeClaim {
    claim: string
    verdict: 'delivered' | 'partial' | 'absent'
    note: string
}

export interface Grade {
    loads: boolean
    takes_input: boolean
    crashed: boolean
    crash_note: string
    first_impression: number | null
    first_impression_note: string
    show_someone: string
    what_is_it: string
    biggest_gap: string
    dimensions: Record<string, { score: number | null; note: string }>
    considered: number | null
    considered_note: string
    what_moved_it: string
    claims: GradeClaim[]
    unrequested: string
    play_ended: string
    play_minutes: number | null
}

export interface GradeTarget {
    run_id: string
    request: string
    previous: number
}

export interface Demo {
    run_id: string
    title: string
    prompt: string
    tier: 'showcase' | 'oneshot'
    thumb_url: string | null
}
