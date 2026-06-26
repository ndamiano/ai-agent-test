import type { Game, GameDetail, SystemStatus, Settings } from '../types'

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

export const api = {
    // Games
    listGames: () =>
        request<Game[]>('/games'),
    getGame: (runId: string) =>
        request<GameDetail>(`/games/${runId}`),
    freezeGame: (runId: string) =>
        request<{ ok: boolean; frozen: boolean }>(`/games/${runId}/freeze`, { method: 'POST' }),
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

    // System
    getStatus: () =>
        request<SystemStatus>('/system/status'),

    // Chat
    sendChatMessage: (message: string, session_id = 'default') =>
        request<{ message: string; session_id: string }>('/chat', { method: 'POST', body: JSON.stringify({ message, session_id }) }),
    clearChatSession: (session_id = 'default') =>
        fetch(`${base}/chat/${session_id}`, { method: 'DELETE' }),

    // Settings
    getSettings: () =>
        request<Settings>('/settings'),
    updateSettings: (settings: Settings) =>
        request<Settings>('/settings', { method: 'PUT', body: JSON.stringify(settings) }),
}
