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
    child_task_id?: string | null
}

export interface TaskEvent {
    id: string
    event_type: string
    message: string
    subtask_id: string | null
    created_at: string
}

export interface RefinementMessage {
    role: 'user' | 'assistant'
    content: string
    timestamp: string
}

export interface Task {
    id: string
    goal: string
    status: 'pending' | 'refining' | 'synthesizing' | 'planning' | 'in_progress' | 'completed' | 'failed' | 'cancelled' | 'archived'
    created_at: string
    updated_at: string
    subtasks: Subtask[]
    parent_task_id?: string | null
}

export interface TaskDetail extends Task {
    events: TaskEvent[]
    context_keys: string[]
    child_tasks: Task[]
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

export type PipelineEventType =
    | 'pipeline_started'
    | 'pipeline_node_started'
    | 'pipeline_node_completed'
    | 'pipeline_node_failed'
    | 'pipeline_stage_started'
    | 'pipeline_stage_completed'
    | 'pipeline_stage_retrying'
    | 'pipeline_stage_failed'
    | 'pipeline_completed'
    | 'pipeline_failed'

export interface PipelineEvent {
    type: PipelineEventType
    task_id: string
    subtask_id?: string | null
    pipeline: string
    pipeline_path?: string[]
    parent_pipeline?: string | null
    working_dir: string
    timestamp: string
    node_count?: number
    node_id?: string
    stage_count?: number
    stage_id?: string
    stage_type?: 'llm' | 'function'
    attempt?: number
    max_attempts?: number
    output?: string
    error?: string
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
    | { type: 'refine_message'; task_id: string; messages: RefinementMessage[]; timestamp: string }
    | PipelineEvent

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
