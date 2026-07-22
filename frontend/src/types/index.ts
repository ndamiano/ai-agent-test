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

export type GameStatus = 'idle' | 'queued' | 'building' | 'paused' | 'built'

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
    seconds_granted: number
    seconds_used: number
}

export interface SystemStatus {
    status: string
    llm_connected: boolean
    llm_model: string
    agent_count: number
}

// Generic envelope — the event bus broadcasts many shapes. Known fields are typed as optional so
// a consumer can narrow by `type` without a full discriminated union; anything else still falls
// through the index signature.
export type WebSocketMessage = {
    type: string
    run_id?: string
    task_id?: string
    timestamp?: string
    // build_queued
    position?: number
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
    // spec_amend_requested
    note?: string
    // assets_done
    rendered?: number
    [key: string]: any
}
