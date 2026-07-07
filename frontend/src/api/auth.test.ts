import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, getAuthToken, setAuthToken, setUnauthorizedHandler } from './client'

describe('client auth', () => {
    beforeEach(() => {
        setAuthToken('tok123')
        setUnauthorizedHandler(null)
    })
    afterEach(() => {
        setAuthToken(null)
        setUnauthorizedHandler(null)
        vi.unstubAllGlobals()
    })

    it('injects the bearer token on API requests', async () => {
        const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })
        vi.stubGlobal('fetch', fetchMock)

        await api.getStatus()

        const init = fetchMock.mock.calls[0][1]
        expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok123')
    })

    it('parses a 402 body into a surfaceable ApiError instead of swallowing it', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 402,
            json: async () => ({ reason: 'insufficient_credits', balance: 0, cost: 1 }),
        }))

        const err = await api.buildGame('run1').catch(e => e)
        expect(err).toBeInstanceOf(ApiError)
        expect(err.status).toBe(402)
        expect(err.body).toEqual({ reason: 'insufficient_credits', balance: 0, cost: 1 })
    })

    it('clears the token and fires the unauthorized handler on a 401', async () => {
        const handler = vi.fn()
        setUnauthorizedHandler(handler)
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 401, json: async () => ({ detail: 'authentication required' }),
        }))

        await expect(api.getStatus()).rejects.toBeInstanceOf(ApiError)
        expect(getAuthToken()).toBeNull()
        expect(handler).toHaveBeenCalledTimes(1)
    })

    it('appends the token to browser-driven asset/download URLs (no header channel)', () => {
        setAuthToken('abc def')
        expect(api.downloadGameUrl('r1')).toBe('/api/games/r1/download?token=abc%20def')
        expect(api.assetFileUrl('r1', 'hero.png')).toContain('?token=abc%20def')
    })
})
