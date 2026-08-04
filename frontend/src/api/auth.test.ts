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

        await api.listGames()

        const init = fetchMock.mock.calls[0][1]
        expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok123')
    })

    it('parses a 402 body into a surfaceable ApiError instead of swallowing it', async () => {
        // The real wire shape: FastAPI nests the gate payload under `detail`. Asserting the flat
        // object here is what let the UI ship "costs undefined, balance undefined".
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 402,
            json: async () => ({ detail: { reason: 'insufficient_credits', balance: 0, cost: 1 } }),
        }))

        const err = await api.buildGame('run1').catch(e => e)
        expect(err).toBeInstanceOf(ApiError)
        expect(err.status).toBe(402)
        expect(err.body).toEqual({ reason: 'insufficient_credits', balance: 0, cost: 1 })
        expect(err.message).toBe('insufficient_credits')
    })

    it('surfaces a plain-string detail as the error message', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 409, json: async () => ({ detail: 'build already in progress' }),
        }))

        const err = await api.buildGame('run1').catch(e => e)
        expect(err.message).toBe('build already in progress')
    })

    it('clears the token and fires the unauthorized handler on a 401', async () => {
        const handler = vi.fn()
        setUnauthorizedHandler(handler)
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 401, json: async () => ({ detail: 'authentication required' }),
        }))

        await expect(api.listGames()).rejects.toBeInstanceOf(ApiError)
        expect(getAuthToken()).toBeNull()
        expect(handler).toHaveBeenCalledTimes(1)
    })

    // The change revokes every session including this one, so dropping the returned token would
    // log the user out the moment they changed their password.
    it('stores the replacement token a password change returns', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ ok: true, token: 'tok-after' }),
        })
        vi.stubGlobal('fetch', fetchMock)

        await api.changePassword('old-password', 'a-long-password')

        expect(getAuthToken()).toBe('tok-after')
        const [url, init] = fetchMock.mock.calls[0]
        expect(url).toBe('/auth/password')
        expect(JSON.parse(init.body)).toEqual(
            { current_password: 'old-password', new_password: 'a-long-password' })
    })

    it('sends the password alongside a new email', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true, status: 200, json: async () => ({ ok: true, email: 'new@example.com' }),
        })
        vi.stubGlobal('fetch', fetchMock)

        await api.changeEmail('a-long-password', 'new@example.com')

        const [url, init] = fetchMock.mock.calls[0]
        expect(url).toBe('/auth/email')
        expect(JSON.parse(init.body)).toEqual(
            { password: 'a-long-password', email: 'new@example.com' })
    })

    it('revokes the token server-side on logout', async () => {
        const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 })
        vi.stubGlobal('fetch', fetchMock)

        await api.logout()

        const [url, init] = fetchMock.mock.calls[0]
        expect(url).toBe('/auth/logout')
        expect(init.method).toBe('POST')
        expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok123')
    })
})
