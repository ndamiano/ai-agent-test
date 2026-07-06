// Streaming chat events emitted by POST /api/chat (SSE, one JSON object per `data:` line).
// Mirrors the events MainAgent.chat_stream yields (src/agents/main_agent.py).

export type ChatStreamEvent =
    | { type: 'token'; content: string }
    | { type: 'tool'; tool_name: string; status: 'start' | 'success' | 'failed' }
    | { type: 'done'; message: string }
    | { type: 'error'; message: string }

export interface ToolProgressEvent {
    tool_name: string
    status: 'start' | 'success' | 'failed'
}
