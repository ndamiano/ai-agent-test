import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import LoginScreen from './LoginScreen'
import { AuthProvider } from '../contexts/AuthContext'
import { RouterProvider } from '../router'
import { getAuthToken, setAuthToken } from '../api/client'

function renderScreen() {
    return render(
        <AuthProvider>
            <RouterProvider>
                <LoginScreen />
            </RouterProvider>
        </AuthProvider>,
    )
}

const fillSignup = (handle = 'alice', password = 'pw', code = 'gs-aaaa-aaaa') => {
    fireEvent.click(screen.getByRole('button', { name: /create an account/i }))
    fireEvent.change(screen.getByPlaceholderText('Handle'), { target: { value: handle } })
    fireEvent.change(screen.getByPlaceholderText('Password'), { target: { value: password } })
    fireEvent.change(screen.getByPlaceholderText(/invite code/i), { target: { value: code } })
    fireEvent.click(screen.getByRole('button', { name: /create account/i }))
}

const jsonResponse = (status: number, body: unknown) => ({
    ok: status < 400, status,
    headers: { get: () => null },
    json: async () => body,
})

describe('signup form', () => {
    beforeEach(() => setAuthToken(null))
    afterEach(() => {
        cleanup()
        setAuthToken(null)
        vi.unstubAllGlobals()
    })

    it('starts on login and reveals the invite-code field on switch', () => {
        renderScreen()
        expect(screen.queryByPlaceholderText(/invite code/i)).toBeNull()
        fireEvent.click(screen.getByRole('button', { name: /create an account/i }))
        expect(screen.getByPlaceholderText(/invite code/i)).toBeTruthy()
        expect(screen.getByRole('button', { name: /create account/i })).toBeTruthy()
    })

    it('signs the new account in on success', async () => {
        const fetchMock = vi.fn().mockImplementation((url: string) =>
            Promise.resolve(String(url).includes('/auth/signup')
                ? jsonResponse(200, { token: 'tok-1', user: { id: 'u1', handle: 'alice', role: 'user' } })
                : jsonResponse(200, { id: 'u1', handle: 'alice', role: 'user', balance: 0 })))
        vi.stubGlobal('fetch', fetchMock)

        renderScreen()
        fillSignup()

        await waitFor(() => expect(getAuthToken()).toBe('tok-1'))
        const [url, init] = fetchMock.mock.calls[0]
        expect(url).toBe('/auth/signup')
        expect(JSON.parse(init.body)).toEqual(
            { handle: 'alice', password: 'pw', invite_code: 'gs-aaaa-aaaa' })
    })

    it('surfaces an invalid code plainly', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
            jsonResponse(403, { detail: 'invalid invite code' })))
        renderScreen()
        fillSignup()
        await waitFor(() => expect(screen.getByText('Invalid invite code.')).toBeTruthy())
        expect(getAuthToken()).toBeNull()
    })

    it('surfaces a taken handle plainly', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
            jsonResponse(409, { detail: 'that handle is already taken' })))
        renderScreen()
        fillSignup()
        await waitFor(() => expect(screen.getByText('That handle is already taken.')).toBeTruthy())
    })

    it('surfaces the throttle plainly', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
            jsonResponse(429, { detail: 'too many signup attempts, try again later' })))
        renderScreen()
        fillSignup()
        await waitFor(() =>
            expect(screen.getByText(/too many attempts/i)).toBeTruthy())
    })
})
