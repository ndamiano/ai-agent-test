export interface Game {
    run_id: string
    title: string
    status: GameStatus
    built: boolean
    building: boolean
    paused: boolean
}

// 'held': the safety screen held the build for human review — frozen and shown as unavailable.
export type GameStatus = 'idle' | 'queued' | 'building' | 'fixing' | 'paused' | 'built' | 'held'

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
export type AssetKind = 'sprite' | 'tile' | 'scene' | 'mesh'
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
export type PromptScope = 'all' | 'game' | 'platform'

export interface PromptBucket {
    game_id: string | null
    turns: number
    last_at: number
    exec_seconds: number
    title: string | null
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
    system: string
    messages: PromptMessage[]
    tools: { name: string; description: string; parameters: Record<string, any> }[]
    response: {
        text: string
        tool_calls: { name: string; arguments: string }[]
        usage: Record<string, any>
    } | null
}

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

// An invite code as the admin surface lists it (GET /api/admin/invites).
export interface AdminInvite {
    code: string
    created_by: string
    created_at: number
    max_uses: number
    uses: number
    disabled: number
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

// Effective cost (GET /api/admin/costs): RunPod's own billing joined against our job/worker
// logs. `runpod` is null when the ledger is unreachable; derived is {} then too.
export interface CostWindow {
    label: string
    runpod: { amount_usd: number; billed_seconds: number
              by_gpu: { gpu: string; amount_usd: number; billed_seconds: number }[] } | null
    jobs: { done: number; failed: number; exec_seconds: number; failed_exec_seconds: number }
    workers: { count: number; wall_seconds: number }
    derived: { usd_per_gpu_hour?: number; utilization?: number; overhead_seconds?: number }
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
