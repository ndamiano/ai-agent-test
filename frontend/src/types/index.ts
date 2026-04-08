export type MaestroPhase = 'planning' | 'evaluation' | 'error_recovery'

export interface Subtask {
    id: string
    agent_id: string
    goal: string
    name: string | null
    description: string | null
    status: 'pending' | 'in_progress' | 'completed' | 'failed'
    position: number
    output_preview: string | null
    depends_on: string[]
}

export interface TaskEvent {
    id: string
    event_type: string
    message: string
    subtask_id: string | null
    created_at: string
}

export interface Task {
    id: string
    goal: string
    status: 'pending' | 'planning' | 'in_progress' | 'completed' | 'failed' | 'cancelled' | 'archived'
    created_at: string
    updated_at: string
    subtasks: Subtask[]
}

export interface TaskDetail extends Task {
    events: TaskEvent[]
    context_keys: string[]
}

export interface Agent {
    id: string
    name: string
    description: string
    tools: string[]
}

export interface SystemStatus {
    status: string
    lmstudio_connected: boolean
    embedding_connected: boolean
    lmstudio_url: string
    agent_count: number
    task_count: number
}

export interface AskResponse {
    question: string
    answer: string
    context_used: string[]
}

export interface ArtifactManifest {
    summary: string
    artifacts: Array<{ type: string; label: string; path: string }>
}

export interface ToolUsage {
    tool_name: string
    arguments: Record<string, any>
    status: 'success' | 'failed'
    timestamp: string
}

export interface AgentMessage {
    agent_id: string
    phase: MaestroPhase
    message: string
    timestamp: string
}

export type WebSocketMessage =
    | { type: 'connected'; timestamp: string }
    | { type: 'task_created'; task_id: string; goal: string; timestamp: string }
    | { type: 'task_status'; task_id: string; task: Task }
    | { type: 'subtask_started'; task_id: string; subtask_id: string; agent_id: string; timestamp: string }
    | { type: 'subtask_completed'; task_id: string; subtask_id: string; agent_id: string; timestamp: string }
    | { type: 'subtask_failed'; task_id: string; subtask_id: string; error: string; timestamp: string }
    | { type: 'task_completed'; task_id: string }
    | { type: 'task_failed'; task_id: string; error: string }
    | { type: 'agent_message'; task_id: string; agent_id: string; phase: MaestroPhase; message: string; timestamp: string; subtask_id?: string }
    | { type: 'tool_usage'; task_id: string; subtask_id: string; tool_name: string; arguments: Record<string, any>; status: 'success' | 'failed'; timestamp: string }

export interface LMStudioSettings {
    base_url: string
    model: string
    temperature?: number
    max_tokens?: number
}

export interface ClineSettings {
    api_key: string
    model: string
    temperature?: number
    max_tokens?: number
}

export interface Settings {
    connector_type: string
    working_directory?: string
    lmstudio?: LMStudioSettings
    cline?: ClineSettings
}