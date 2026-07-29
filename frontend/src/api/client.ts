import type { AdminQueues, DurableEventRow, Game, GameAsset, GameDetail, PromptBucket, PromptDetail, PromptScope, PromptTurn, SystemStatus } from '../types'

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
    // Make a new game: the prompt becomes the run and the build starts. Nothing exists server-side
    // until this call, so an abandoned box leaves nothing behind.
    createGame: (prompt: string) =>
        request<{ run_id: string; status: string }>('/games', { method: 'POST', body: JSON.stringify({ prompt }) }),
    getGame: (runId: string) =>
        request<GameDetail>(`/games/${runId}`),
    // The durable build/spec event log — catch-up after a reload or a websocket gap. `after` is an
    // event-id cursor (0 = from the start); the rows carry `id` for incremental follow-up.
    getGameEvents: (runId: string, after = 0) =>
        request<DurableEventRow[]>(`/games/${runId}/events?after=${after}`),
    // The game's own asset manifest with per-asset render status. Authed + ownership-checked;
    // returns [] before the game declares any art.
    getGameAssets: (runId: string) =>
        request<GameAsset[]>(`/games/${runId}/assets`),
    // Fetch one asset's bytes through the authed blob route and hand back an object URL — <img>/
    // download can't attach a bearer header, so the header-only auth design loads binaries this way.
    // The caller owns the URL and must revokeObjectURL it when done.
    getAssetBlobUrl: async (runId: string, assetId: string): Promise<string> => {
        const res = await fetch(`${base}/games/${runId}/assets/${assetId}`, { headers: authHeaders() })
        if (res.status === 401) handleUnauthorized()
        if (!res.ok) throw new ApiError(res.status, await errorBody(res))
        return URL.createObjectURL(await res.blob())
    },
    // `prompt` is the text in the box: pressing Build is what approves it, so the build call is
    // the only thing that writes it.
    buildGame: (runId: string, prompt?: string) =>
        request<{ status: string; run_id: string }>(`/games/${runId}/build`, { method: 'POST', body: JSON.stringify({ prompt }) }),

    // Human-in-the-loop build control
    pauseGame: (runId: string) =>
        request<{ status: string }>(`/games/${runId}/pause`, { method: 'POST' }),
    resumeGame: (runId: string) =>
        request<{ status: string }>(`/games/${runId}/resume`, { method: 'POST' }),
    stopGame: (runId: string) =>
        request<{ status: string }>(`/games/${runId}/stop`, { method: 'POST' }),
    // Free-text patch of a built game — re-runs the build loop from a human note.
    fixGame: (runId: string, note: string) =>
        request<{ status: string; run_id: string }>(`/games/${runId}/fix`, { method: 'POST', body: JSON.stringify({ note }) }),
    // Render the art the game declared in its assets.json (additive; missing art renders as shapes).
    renderAssets: (runId: string) =>
        request<{ status: string; run_id: string }>(`/games/${runId}/assets`, { method: 'POST' }),
    // Re-render ONE asset from a change note — a single-asset swap, no whole-game re-render. The
    // batch's assets_done fires on completion, which is what refetches the gallery.
    regenerateAsset: (runId: string, assetId: string, prompt: string, mode: 'full' | 'img2img' = 'full') =>
        request<{ status: string; run_id: string; asset_id: string }>(
            `/games/${runId}/assets/${assetId}/regenerate`, { method: 'POST', body: JSON.stringify({ prompt, mode }) }),

    // System
    getStatus: () =>
        request<SystemStatus>('/system/status'),

    // Admin (role-gated server-side; a 403 means not an admin)
    getAdminQueues: () =>
        request<AdminQueues>('/admin/queues'),

    // The prompt log — every llm turn any game spent. The index is cheap; one turn's full text
    // (system + messages + tools + reply) is its own fetch.
    getPromptBuckets: () =>
        request<PromptBucket[]>('/admin/prompts/games'),
    getPromptTurns: (scope: PromptScope, gameId?: string | null) =>
        request<PromptTurn[]>(`/admin/prompts/turns?scope=${scope}` +
            (scope === 'game' && gameId ? `&game_id=${encodeURIComponent(gameId)}` : '')),
    getPromptTurn: (jobId: string) =>
        request<PromptDetail>(`/admin/prompts/turns/${jobId}`),

}
