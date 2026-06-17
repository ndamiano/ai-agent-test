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
    buildGame: (runId: string) =>
        request<{ status: string; run_id: string }>(`/games/${runId}/build`, { method: 'POST' }),

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
