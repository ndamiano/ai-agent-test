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

export interface SpecComponent {
    id: string
    description?: string
    deps?: string[]
    done_conditions?: Array<Record<string, any>>
}

export interface Spec {
    title: string
    request?: string
    frozen: boolean
    components: SpecComponent[]
    story_state_schema?: Record<string, any>
}

export interface TodoItem {
    component_id: string
    check: Record<string, any>
    detail: string | null
    human?: boolean
}

export interface HumanTodo {
    id: string
    component_id: string
    text: string
    done: boolean
}

export interface Waiver {
    sig: string
    component_id: string
    check: Record<string, any>
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

// Generic envelope — the event bus broadcasts many shapes; the per-stage phase
// will tag build events with run_id and we'll narrow then.
export type WebSocketMessage = { type: string; [key: string]: any }

export interface LMStudioSettings {
    base_url: string
    model: string
    max_tokens?: number
}

export interface ClineSettings {
    api_key: string
    base_url?: string
    model: string
    max_tokens?: number
}

export interface ComfyUISettings {
    endpoint: string
    vram_management: boolean
}

export interface Settings {
    connector_type: string
    model_category?: 'large' | 'medium' | 'small'
    working_directory?: string
    refine_before_execution?: boolean
    renpy_sdk_path?: string
    lmstudio?: LMStudioSettings
    cline?: ClineSettings
    comfyui?: ComfyUISettings
}
