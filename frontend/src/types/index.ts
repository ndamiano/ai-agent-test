// A "game" is one build run (runs/<run_id>/), surfaced read-only by /api/games.

export interface Game {
    run_id: string
    title: string
    frozen: boolean
    built: boolean
    building: boolean
    n_components: number
    mtime: number
}

export interface Spec {
    title: string
    request?: string
    frozen: boolean
    modules: string[]
    module_reasons?: Record<string, string>
    params?: Record<string, any>
    engine?: string
    substrate?: string
    story_state_schema?: Record<string, any>
}

// One effective failure: a typed Error serialized. `type` is 'human' | 'build' | 'fix'.
export interface TodoItem {
    component: string
    code: string
    type: string
    detail: string | null
    idkey: string
    path?: string | null   // the human-todo id when type === 'human'
}

export interface HumanTodo {
    id: string
    component_id: string
    text: string
    done: boolean
}

export interface Waiver {
    idkey: string
    note?: string
}

export interface GameDetail {
    run_id: string
    spec: Spec
    artifact: Record<string, any>
    todo: TodoItem[]
    human_todos: HumanTodo[]
    waivers: Waiver[]
    frozen: boolean
    built: boolean
    building: boolean
    status: string
    auto_pause: boolean
    assets_exist: boolean
}

export interface SystemStatus {
    status: string
    lmstudio_connected: boolean
    lmstudio_url: string
    agent_count: number
}

// One buildable thing within a component — the component-blind browser's atom (Epic C1).
// `component` is the on-disk component id (e.g. 'nodes', 'characters', 'combat'); `id` is the
// item id within it ('scene_3', 'mara', 'firebolt'); `idkey` is `${component}:${id}`, the durable
// key every dirty/thumb/edit call takes.
export interface Asset {
    component: string
    id: string
    idkey: string
    content: Record<string, any>
    dirty: boolean
    review_note: string
}

// Generic envelope — the event bus broadcasts many shapes. Known fields are typed as optional so
// a consumer can narrow by `type` without a full discriminated union; anything else still falls
// through the index signature.
export type WebSocketMessage = {
    type: string
    run_id?: string
    task_id?: string
    timestamp?: string
    // build_started / build_step (Epic C5) — the live effective to-do, so the board never shows
    // a stale/empty list while a build runs.
    todo?: TodoItem[]
    n_failing?: number
    step?: number
    max_steps?: number
    summary?: string
    // build_started (Epic E2) — wall-clock seconds (time.time()) the build began; build_step
    // carries `elapsed` seconds since that start, so the progress header can show a running timer.
    started_at?: number
    elapsed?: number
    // error_parked (Epic E3) — an error that survived _ATTEMPT_CAP fix attempts without clearing;
    // `identity` is the Error's identity tuple (component/code/path-ish), serialized as strings.
    identity?: string[]
    message?: string
    // asset_dirty_set / asset_dirty_cleared / asset_updated (Epic C4)
    idkey?: string
    component?: string
    item_id?: string
    note?: string
    cleared_own_dirty?: boolean
    flagged_dependents?: string[]
    [key: string]: any
}

// error_parked: an error survived _ATTEMPT_CAP fix attempts — it needs a human, not more retries.
export interface ErrorParkedEvent extends WebSocketMessage {
    type: 'error_parked'
    run_id: string
    identity: string[]
    message: string
}

// asset_dirty_set: a human (or a downstream-propagation cascade from an edit) flagged one asset.
export interface AssetDirtySetEvent extends WebSocketMessage {
    type: 'asset_dirty_set'
    run_id: string
    idkey: string
    component: string
    item_id: string
    note?: string
}

// asset_dirty_cleared: thumbs-up (or a rewrite) cleared one asset's dirty flag.
export interface AssetDirtyClearedEvent extends WebSocketMessage {
    type: 'asset_dirty_cleared'
    run_id: string
    idkey: string
    component: string
    item_id: string
}

// asset_updated: an asset's content changed (a human edit); `flagged_dependents` lists every
// downstream idkey the edit just reflagged dirty (Epic B's propagation, made visible live).
export interface AssetUpdatedEvent extends WebSocketMessage {
    type: 'asset_updated'
    run_id: string
    idkey: string
    component: string
    item_id: string
}

