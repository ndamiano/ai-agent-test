import type { AdminQueues, Game, GameDetail, SystemStatus } from '../types'
import type { ChatStreamEvent } from '../types/chat'

const base = '/api'

// ── auth token (bearer) ────────────────────────────────────────────────────────
// The token lives in localStorage under `maestro_token` and is injected on every request.
// A 401 clears it and fires the unauthorized handler so the app drops back to login centrally.
let authToken: string | null = localStorage.getItem('maestro_token')
let onUnauthorized: (() => void) | null = null

export function setAuthToken(token: string | null): void {
    authToken = token
    if (token) localStorage.setItem('maestro_token', token)
    else localStorage.removeItem('maestro_token')
}

export function getAuthToken(): string | null {
    return authToken
}

export function setUnauthorizedHandler(fn: (() => void) | null): void {
    onUnauthorized = fn
}

function authHeaders(extra?: HeadersInit): HeadersInit {
    return {
        'Content-Type': 'application/json',
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
        ...extra,
    }
}

// Carries the HTTP status + parsed body so callers can react to a specific failure — notably a
// 402 build gate ({reason, balance, cost}) — instead of a bare status string. `body` is FastAPI's
// unwrapped `detail`: an object for the structured gates, a plain string for everything else.
export class ApiError extends Error {
    status: number
    body: any
    constructor(status: number, body: any) {
        super(typeof body === 'string' ? body : (body?.reason ?? String(status)))
        this.name = 'ApiError'
        this.status = status
        this.body = body
    }
}

// FastAPI wraps every error payload in `detail` — the structured gates (402 credits, 402 compute)
// live one level down, so read through it or every field on the parsed body is undefined.
async function errorBody(res: Response): Promise<any> {
    let body: any = null
    try { body = await res.json() } catch { /* no/empty body */ }
    return (body && typeof body === 'object' && 'detail' in body) ? body.detail : body
}

function handleUnauthorized(): void {
    setAuthToken(null)
    onUnauthorized?.()
}

async function sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms))
}

async function request<T>(path: string, init?: RequestInit, retries = 2): Promise<T> {
    for (let attempt = 0; attempt <= retries; attempt++) {
        const res = await fetch(`${base}${path}`, {
            ...init,
            headers: authHeaders(init?.headers),
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
            if (res.status === 401) handleUnauthorized()
            throw new ApiError(res.status, await errorBody(res))
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
        headers: authHeaders(),
        body: JSON.stringify({ message, session_id }),
    })
    if (res.status === 401) handleUnauthorized()
    if (!res.ok || !res.body) {
        // Chat is free but gated on a balance, so a 402 lands here with the numbers to explain
        // itself — dropping the body would surface it as a bare "402".
        throw new ApiError(res.status, res.ok ? null : await errorBody(res))
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
    // Auth — login lives at /auth (not under /api); no signup endpoint exists.
    login: async (handle: string, password: string) => {
        const res = await fetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ handle, password }),
        })
        if (!res.ok) {
            let body: any = null
            try { body = await res.json() } catch { /* no body */ }
            throw new ApiError(res.status, body)
        }
        return res.json() as Promise<{ token: string; user: { id: string; handle: string; role: string } }>
    },
    me: async () => {
        const res = await fetch('/auth/me', { headers: authHeaders() })
        if (res.status === 401) handleUnauthorized()
        if (!res.ok) throw new ApiError(res.status, null)
        return res.json() as Promise<{ id: string; handle: string; role: string; balance: number }>
    },
    // Revoke the current token server-side. Best-effort: the caller clears local state regardless.
    logout: async () => {
        try { await fetch('/auth/logout', { method: 'POST', headers: authHeaders() }) }
        catch { /* offline / already-dead token — local clear still applies */ }
    },

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
    setAutoPause: (runId: string, enabled: boolean) =>
        request<{ auto_pause: boolean }>(`/games/${runId}/auto-pause`, { method: 'POST', body: JSON.stringify({ enabled }) }),
    // Free-text patch of a built game — re-runs the build loop from a human note.
    fixGame: (runId: string, note: string) =>
        request<{ status: string; run_id: string }>(`/games/${runId}/fix`, { method: 'POST', body: JSON.stringify({ note }) }),
    // Skin the shapes: plan + render assets for a built game (additive, re-gates after).
    skinAssets: (runId: string) =>
        request<{ status: string; run_id: string }>(`/games/${runId}/assets`, { method: 'POST' }),

    // System
    getStatus: () =>
        request<SystemStatus>('/system/status'),

    // Admin (role-gated server-side; a 403 means not an admin)
    getAdminQueues: () =>
        request<AdminQueues>('/admin/queues'),

    // Chat
    streamChatMessage,
    clearChatSession: (session_id = 'default') =>
        fetch(`${base}/chat/${session_id}`, { method: 'DELETE', headers: authHeaders() }),
}
