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
}

export interface GameDetail {
    run_id: string
    spec: Spec
    artifact: Record<string, any>
    todo: TodoItem[]
    frozen: boolean
    built: boolean
    building: boolean
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
