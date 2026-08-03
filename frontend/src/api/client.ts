import type { AdminAnalytics, AdminCosts, AdminInvite, AdminQueues, DurableEventRow, Game, GameAsset, GameDetail, PromptBucket, PromptDetail, PromptScope, PromptTurn } from '../types'

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

export const buildErrorMessage = (e: unknown, fallback: string): string => {
    if (e instanceof ApiError && e.status === 402) {
        const { cost, balance } = e.body ?? {}
        return `Out of credits — this build costs ${cost}, your balance is ${balance}.`
    }
    return e instanceof Error ? e.message : fallback
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

// Hands back the raw Response so the binary route can read .blob() while everything else goes
// through request().
async function send(path: string, init?: RequestInit, retries = 2): Promise<Response> {
    for (let attempt = 0; ; attempt++) {
        const res = await fetch(path, { ...init, headers: authHeaders(init?.headers) })

        if (res.status === 429 && attempt < retries) {
            const retryAfter = res.headers.get('Retry-After')
            const waitMs = retryAfter ? parseInt(retryAfter) * 1000 : 2 ** attempt * 500
            console.warn(`Rate limited, retrying after ${waitMs}ms (attempt ${attempt + 1}/${retries})`)
            await sleep(waitMs)
            continue
        }

        if (res.ok) return res
        if (res.status === 401) handleUnauthorized()
        if (res.status === 429) throw new ApiError(429, 'Rate limit exceeded. Please try again in a moment.')
        throw new ApiError(res.status, await errorBody(res))
    }
}

async function request<T>(path: string, init?: RequestInit, retries?: number): Promise<T> {
    return (await send(path, init, retries)).json() as Promise<T>
}


export interface CreditPackage {
    id: string
    credits: number
    usd_cents: number
}

export interface PurchaseRow {
    id: string
    package_id: string
    credits: number
    usd_cents: number
    status: string
    created_at: number
    completed_at: number | null
}

export const api = {
    // No 429 retry on the auth forms: the throttle's Retry-After runs to minutes, and sleeping it
    // out would hang the form where "throttled" should show.
    login: (handle: string, password: string) =>
        request<{ token: string; user: { id: string; handle: string; role: string } }>(
            '/auth/login', { method: 'POST', body: JSON.stringify({ handle, password }) }, 0),
    // Beta signup: the invite code is what admits the account; success signs the new user in.
    signup: (handle: string, password: string, inviteCode: string) =>
        request<{ token: string; user: { id: string; handle: string; role: string } }>(
            '/auth/signup', { method: 'POST', body: JSON.stringify({ handle, password, invite_code: inviteCode }) }, 0),
    me: () =>
        request<{ id: string; handle: string; role: string; balance: number }>('/auth/me'),
    logout: async () => {
        try { await fetch('/auth/logout', { method: 'POST', headers: authHeaders() }) }
        catch { /* offline / already-dead token — local clear still applies */ }
    },
    changePassword: (currentPassword: string, newPassword: string) =>
        request<{ ok: boolean }>('/auth/password', {
            method: 'POST',
            body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
        }),

    listGames: () =>
        request<Game[]>('/api/games'),
    // The landing page's demo surface — public, owner-curated server-side.
    listDemos: () =>
        request<{ run_id: string; title: string; prompt: string }[]>('/api/demos'),
    demoPlaySession: (runId: string) =>
        request<{ url: string; origin: string }>(`/api/demos/${runId}/play-session`, { method: 'POST' }),
    // Make a new game: the prompt becomes the run and the build starts. Nothing exists server-side
    // until this call, so an abandoned box leaves nothing behind.
    createGame: (prompt: string) =>
        request<{ run_id: string; status: string }>('/api/games', { method: 'POST', body: JSON.stringify({ prompt }) }),
    getGame: (runId: string) =>
        request<GameDetail>(`/api/games/${runId}`),
    // The durable build/spec event log — catch-up after a reload or a websocket gap. `after` is an
    // event-id cursor (0 = from the start); the rows carry `id` for incremental follow-up.
    getGameEvents: (runId: string, after = 0) =>
        request<DurableEventRow[]>(`/api/games/${runId}/events?after=${after}`),
    getGameAssets: (runId: string) =>
        request<GameAsset[]>(`/api/games/${runId}/assets`),
    // Fetch one asset's bytes through the authed blob route and hand back an object URL — <img>/
    // download can't attach a bearer header, so the header-only auth design loads binaries this way.
    // The caller owns the URL and must revokeObjectURL it when done.
    getAssetBlobUrl: async (runId: string, assetId: string): Promise<string> =>
        URL.createObjectURL(await (await send(`/api/games/${runId}/assets/${assetId}`)).blob()),
    // Plan the prompt into build stages without building — the user reads and edits the plan,
    // and pressing Build on it is the approval. Planning is where the credit is charged; passing
    // runId re-plans that already-charged run (no second charge).
    enhancePrompt: (prompt: string, runId?: string) =>
        request<{ run_id: string; prompt: string; stages: string[] }>('/api/games/enhance', { method: 'POST', body: JSON.stringify({ prompt, run_id: runId }) }),
    // `prompt` is the text in the box: pressing Build is what approves it, so the build call is
    // the only thing that writes it.
    buildGame: (runId: string, prompt?: string) =>
        request<{ status: string; run_id: string }>(`/api/games/${runId}/build`, { method: 'POST', body: JSON.stringify({ prompt }) }),
    // The staged variant: the stage texts exactly as shown in the box. Stage 1 is the build's
    // prompt; the rest auto-advance server-side after each clean finalize.
    buildStages: (runId: string, stages: string[]) =>
        request<{ status: string; run_id: string }>(`/api/games/${runId}/build`, { method: 'POST', body: JSON.stringify({ stages }) }),
    // The same build, over an emptied game folder: the model opens on nothing instead of on the
    // last attempt's files.
    regenerateGame: (runId: string, prompt?: string) =>
        request<{ status: string; run_id: string }>(`/api/games/${runId}/build`, { method: 'POST', body: JSON.stringify({ prompt, fresh: true }) }),

    // A play session: a single-use handoff URL for the iframe (or a new tab) — each mount of the
    // game needs a fresh one. `origin` is the game origin ('' ⇒ games share the app origin); the
    // reporter-message listener verifies against it.
    playSession: (runId: string) =>
        request<{ url: string; origin: string }>(`/api/games/${runId}/play-session`, { method: 'POST' }),

    pauseGame: (runId: string) =>
        request<{ status: string }>(`/api/games/${runId}/pause`, { method: 'POST' }),
    resumeGame: (runId: string) =>
        request<{ status: string }>(`/api/games/${runId}/resume`, { method: 'POST' }),
    stopGame: (runId: string) =>
        request<{ status: string }>(`/api/games/${runId}/stop`, { method: 'POST' }),
    fixGame: (runId: string, note: string) =>
        request<{ status: string; run_id: string }>(`/api/games/${runId}/fix`, { method: 'POST', body: JSON.stringify({ note }) }),
    renderAssets: (runId: string) =>
        request<{ status: string; run_id: string }>(`/api/games/${runId}/assets`, { method: 'POST' }),
    // The batch's assets_done fires on completion, which is what refetches the gallery.
    regenerateAsset: (runId: string, assetId: string, prompt: string, mode: 'full' | 'img2img' = 'full') =>
        request<{ status: string; run_id: string; asset_id: string }>(
            `/api/games/${runId}/assets/${assetId}/regenerate`, { method: 'POST', body: JSON.stringify({ prompt, mode }) }),

    // `enabled: false` (no payment provider configured) hides the storefront.
    listPackages: () =>
        request<{ enabled: boolean; packages: CreditPackage[] }>('/api/billing/packages'),
    // Answers with the provider's hosted checkout URL; the user pays there and returns to
    // /credits, where the result params drive completePurchase.
    startPurchase: (packageId: string) =>
        request<{ purchase_id: string; status: string; checkout_url: string }>(
            '/api/billing/purchase', { method: 'POST', body: JSON.stringify({ package_id: packageId }) }),
    completePurchase: (purchaseId: string) =>
        request<{ status: string; credits: number; balance: number }>(
            `/api/billing/purchase/${purchaseId}/complete`, { method: 'POST' }),
    listPurchases: () =>
        request<PurchaseRow[]>('/api/billing/purchases'),

    getAdminQueues: () =>
        request<AdminQueues>('/api/admin/queues'),
    getAdminInvites: () =>
        request<{ invites: AdminInvite[] }>('/api/admin/invites'),
    createAdminInvites: (count: number, maxUses: number) =>
        request<{ codes: string[] }>('/api/admin/invites', {
            method: 'POST', body: JSON.stringify({ count, max_uses: maxUses }),
        }),
    disableAdminInvite: (code: string) =>
        request<{ ok: boolean }>(`/api/admin/invites/${encodeURIComponent(code)}/disable`, { method: 'POST' }),
    getAdminCosts: () =>
        request<AdminCosts>('/api/admin/costs'),
    getAdminAnalytics: (days = 14) =>
        request<AdminAnalytics>(`/api/admin/analytics?days=${days}`),

    getPromptBuckets: () =>
        request<PromptBucket[]>('/api/admin/prompts/games'),
    getPromptTurns: (scope: PromptScope, gameId?: string | null) =>
        request<PromptTurn[]>(`/api/admin/prompts/turns?scope=${scope}` +
            (scope === 'game' && gameId ? `&game_id=${encodeURIComponent(gameId)}` : '')),
    getPromptTurn: (jobId: string) =>
        request<PromptDetail>(`/api/admin/prompts/turns/${jobId}`),
}
