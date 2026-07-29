import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, buildErrorMessage, setAuthToken } from './client'

const ok = (body: unknown = {}) => ({ ok: true, status: 200, json: async () => body })

describe('request routing', () => {
    beforeEach(() => setAuthToken('tok123'))
    afterEach(() => { setAuthToken(null); vi.unstubAllGlobals() })

    it('sends API calls under /api', async () => {
        const fetchMock = vi.fn().mockResolvedValue(ok([]))
        vi.stubGlobal('fetch', fetchMock)

        await api.listGames()

        expect(fetchMock.mock.calls[0][0]).toBe('/api/games')
    })

    // The auth routes are NOT under /api — routing them through the shared request() is what makes
    // them share the bearer header and the central 401, so their path must survive the move.
    it('sends auth calls to /auth, with the bearer header', async () => {
        const fetchMock = vi.fn().mockResolvedValue(ok({ id: 'u1' }))
        vi.stubGlobal('fetch', fetchMock)

        await api.me()

        const [url, init] = fetchMock.mock.calls[0]
        expect(url).toBe('/auth/me')
        expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok123')
    })

    it('retries a 429 and returns the retried body', async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce({ ok: false, status: 429, headers: { get: () => '0' }, json: async () => ({}) })
            .mockResolvedValueOnce(ok([{ run_id: 'r1' }]))
        vi.stubGlobal('fetch', fetchMock)

        await expect(api.listGames()).resolves.toEqual([{ run_id: 'r1' }])
        expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    // A rate limit that outlives the retries must arrive as an ApiError like every other failure —
    // callers narrow on `instanceof ApiError`, and a bare Error slips straight past them.
    it('raises an ApiError once the retries are spent', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: false, status: 429, headers: { get: () => '0' }, json: async () => ({}),
        })
        vi.stubGlobal('fetch', fetchMock)

        const err = await api.listGames().catch(e => e)
        expect(err).toBeInstanceOf(ApiError)
        expect(err.status).toBe(429)
        expect(fetchMock).toHaveBeenCalledTimes(3)
    })
})

describe('buildErrorMessage', () => {
    it('spells out the credit gate from the 402 body', () => {
        const err = new ApiError(402, { reason: 'insufficient_credits', balance: 0, cost: 1 })
        expect(buildErrorMessage(err, 'Build failed'))
            .toBe('Out of credits — this build costs 1, your balance is 0.')
    })

    it('passes any other failure through as its own message', () => {
        expect(buildErrorMessage(new ApiError(409, 'build already in progress'), 'Build failed'))
            .toBe('build already in progress')
    })

    it('falls back when what was thrown is not an Error', () => {
        expect(buildErrorMessage('nope', 'Build failed')).toBe('Build failed')
    })
})
