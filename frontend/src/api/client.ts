import type { Game, GameDetail, SystemStatus, Settings, Asset } from '../types'
import type { ChatStreamEvent } from '../types/chat'

const base = '/api'

async function sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms))
}

async function request<T>(path: string, init?: RequestInit, retries = 2): Promise<T> {
    for (let attempt = 0; attempt <= retries; attempt++) {
        const res = await fetch(`${base}${path}`, {
            headers: { 'Content-Type': 'application/json' },
            ...init,
        })

        // Handle rate limiting with exponential backoff
        if (res.status === 429 && attempt < retries) {
            const retryAfter = res.headers.get('Retry-After')
            const waitTime = retryAfter ? parseInt(retryAfter) * 1000 : Math.pow(2, attempt) * 500
            console.warn(`Rate limited, retrying after ${waitTime}ms (attempt ${attempt + 1}/${retries})`)
            await sleep(waitTime)
            continue
        }

        if (!res.ok) {
            if (res.status === 429) {
                throw new Error('Rate limit exceeded. Please try again in a moment.')
            }
            throw new Error(`${res.status}`)
        }

        return res.json() as Promise<T>
    }

    throw new Error('Rate limit exceeded after retries')
}

// Splits an accumulated SSE text buffer on blank lines into complete `data: ...` frames
// plus whatever partial frame is still trailing (a chunk boundary can land mid-frame).
export function splitSseFrames(buffer: string): { frames: string[]; rest: string } {
    const parts = buffer.split('\n\n')
    const rest = parts.pop() ?? ''
    return { frames: parts, rest }
}

function parseSseFrame(frame: string): ChatStreamEvent | null {
    const dataLine = frame.split('\n').find(line => line.startsWith('data: '))
    if (!dataLine) return null
    try {
        return JSON.parse(dataLine.slice('data: '.length)) as ChatStreamEvent
    } catch {
        return null
    }
}

// Streams a chat turn's tokens + tool-progress markers as they arrive (SSE over POST —
// EventSource can't send a body, so this parses the stream by hand).
export async function* streamChatMessage(
    message: string,
    session_id = 'default',
): AsyncGenerator<ChatStreamEvent> {
    const res = await fetch(`${base}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, session_id }),
    })
    if (!res.ok || !res.body) {
        throw new Error(`${res.status}`)
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const { frames, rest } = splitSseFrames(buffer)
        buffer = rest
        for (const frame of frames) {
            const event = parseSseFrame(frame)
            if (event) yield event
        }
    }
    const { frames } = splitSseFrames(buffer + '\n\n')
    for (const frame of frames) {
        const event = parseSseFrame(frame)
        if (event) yield event
    }
}

export const api = {
    // Games
    listGames: () =>
        request<Game[]>('/games'),
    getGame: (runId: string) =>
        request<GameDetail>(`/games/${runId}`),
    amendSpec: (runId: string, changes: Record<string, any>, reason = 'human edited the plan') =>
        request<GameDetail>(`/games/${runId}/spec`, { method: 'PATCH', body: JSON.stringify({ changes, reason }) }),
    freezeGame: (runId: string) =>
        request<{ ok: boolean; frozen: boolean }>(`/games/${runId}/freeze`, { method: 'POST' }),
    listModules: () =>
        request<{ id: string; description: string }[]>('/system/modules'),
    buildGame: (runId: string, autoPause = false) =>
        request<{ status: string; run_id: string }>(`/games/${runId}/build`, { method: 'POST', body: JSON.stringify({ auto_pause: autoPause }) }),

    // Human-in-the-loop build control
    pauseGame: (runId: string) =>
        request<{ status: string }>(`/games/${runId}/pause`, { method: 'POST' }),
    resumeGame: (runId: string) =>
        request<{ status: string }>(`/games/${runId}/resume`, { method: 'POST' }),
    cancelGame: (runId: string) =>
        request<{ status: string }>(`/games/${runId}/cancel`, { method: 'POST' }),
    setAutoPause: (runId: string, enabled: boolean) =>
        request<{ auto_pause: boolean }>(`/games/${runId}/auto-pause`, { method: 'POST', body: JSON.stringify({ enabled }) }),
    compileGame: (runId: string, distribute = false) =>
        request<{ ok: boolean; reason?: string }>(`/games/${runId}/compile`, { method: 'POST', body: JSON.stringify({ distribute }) }),
    regenerateAssets: (runId: string) =>
        request<Record<string, any>>(`/games/${runId}/regenerate-assets`, { method: 'POST' }),
    regenerateAsset: (runId: string, filename: string) =>
        request<Record<string, any>>(`/games/${runId}/regenerate-asset`, {
            method: 'POST', body: JSON.stringify({ filename }),
        }),
    assetFileUrl: (runId: string, filename: string) =>
        `${base}/games/${runId}/asset-file/${encodeURIComponent(filename)}`,
    revealGame: (runId: string) =>
        request<{ run_id: string; path: string }>(`/games/${runId}/reveal`, { method: 'POST' }),
    editComponent: (runId: string, componentId: string, content: Record<string, any>) =>
        request<{ ok: boolean }>(`/games/${runId}/component/${componentId}`, { method: 'PUT', body: JSON.stringify({ content }) }),
    editNode: (runId: string, nodeId: string, content: Record<string, any>) =>
        request<{ ok: boolean }>(`/games/${runId}/node/${nodeId}`, { method: 'PUT', body: JSON.stringify({ content }) }),
    rewriteNode: (runId: string, nodeId: string, note: string) =>
        request<{ status: string }>(`/games/${runId}/node/${nodeId}/rewrite`, { method: 'POST', body: JSON.stringify({ note }) }),

    // Done arbitration
    addTodo: (runId: string, component_id: string, text: string) =>
        request<{ id: string }>(`/games/${runId}/todos`, { method: 'POST', body: JSON.stringify({ component_id, text }) }),
    resolveTodo: (runId: string, todoId: string, done = true) =>
        request<{ done: boolean }>(`/games/${runId}/todos/${todoId}`, { method: 'PATCH', body: JSON.stringify({ done }) }),
    waiveCheck: (runId: string, idkey: string) =>
        request<{ idkey: string }>(`/games/${runId}/waive`, { method: 'POST', body: JSON.stringify({ idkey }) }),
    unwaiveCheck: (runId: string, idkey: string) =>
        request<{ idkey: string }>(`/games/${runId}/unwaive`, { method: 'POST', body: JSON.stringify({ idkey }) }),

    // Asset browser (Epic C) — component-blind: works for any component id the artifact has
    // (nodes, characters, places, combat, items, story, asset_manifest, ...).
    listAssets: (runId: string, componentId: string) =>
        request<Asset[]>(`/games/${runId}/assets/${componentId}`),
    getAsset: (runId: string, componentId: string, itemId: string) =>
        request<Asset>(`/games/${runId}/assets/${componentId}/${itemId}`),
    editAsset: (runId: string, componentId: string, itemId: string, content: Record<string, any>) =>
        request<{ ok: boolean; cleared_own_dirty: boolean; flagged_dependents: string[] }>(
            `/games/${runId}/assets/${componentId}/${itemId}`,
            { method: 'PUT', body: JSON.stringify({ content }) }),
    setAssetDirty: (runId: string, idkey: string, note = '') =>
        request<{ ok: boolean; idkey: string; note: string }>(
            `/games/${runId}/assets/dirty`, { method: 'POST', body: JSON.stringify({ idkey, note }) }),
    thumbsUpAsset: (runId: string, idkey: string) =>
        request<{ ok: boolean; idkey: string; cleared: boolean }>(
            `/games/${runId}/assets/thumbs-up`, { method: 'POST', body: JSON.stringify({ idkey }) }),
    thumbsDownAsset: (runId: string, idkey: string, note = '') =>
        request<{ ok: boolean; idkey: string; note: string }>(
            `/games/${runId}/assets/thumbs-down`, { method: 'POST', body: JSON.stringify({ idkey, note }) }),

    // System
    getStatus: () =>
        request<SystemStatus>('/system/status'),

    // Chat
    streamChatMessage,
    clearChatSession: (session_id = 'default') =>
        fetch(`${base}/chat/${session_id}`, { method: 'DELETE' }),

    // Settings
    getSettings: () =>
        request<Settings>('/settings'),
    updateSettings: (settings: Settings) =>
        request<Settings>('/settings', { method: 'PUT', body: JSON.stringify(settings) }),
}
