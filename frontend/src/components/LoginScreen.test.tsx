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

const fillSignup = (
    handle = 'alice', password = 'a-long-password', code = 'gs-aaaa-aaaa', email = 'alice@example.com',
) => {
    fireEvent.click(screen.getByRole('button', { name: /create an account/i }))
    fireEvent.change(screen.getByPlaceholderText('Handle'), { target: { value: handle } })
    fireEvent.change(screen.getByPlaceholderText('Email'), { target: { value: email } })
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

    it('starts on login and reveals the invite-code and email fields on switch', () => {
        renderScreen()
        expect(screen.queryByPlaceholderText(/invite code/i)).toBeNull()
        expect(screen.queryByPlaceholderText('Email')).toBeNull()
        fireEvent.click(screen.getByRole('button', { name: /create an account/i }))
        expect(screen.getByPlaceholderText(/invite code/i)).toBeTruthy()
        expect(screen.getByPlaceholderText('Email')).toBeTruthy()
        expect(screen.getByText(/at least 10 characters/i)).toBeTruthy()
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
        expect(JSON.parse(init.body)).toEqual({
            handle: 'alice', password: 'a-long-password', invite_code: 'gs-aaaa-aaaa',
            email: 'alice@example.com',
        })
    })

    it('surfaces a taken email plainly', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
            jsonResponse(409, { detail: 'that email already has an account' })))
        renderScreen()
        fillSignup()
        await waitFor(() => expect(screen.getByText('That email already has an account.')).toBeTruthy())
    })

    it('surfaces a rejected password plainly', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
            jsonResponse(400, { detail: 'password must be at least 10 characters' })))
        renderScreen()
        fillSignup()
        await waitFor(() =>
            expect(screen.getByText('Password must be at least 10 characters.')).toBeTruthy())
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

const askForReset = (email = 'alice@example.com') => {
    fireEvent.click(screen.getByRole('button', { name: /forgot password/i }))
    fireEvent.change(screen.getByPlaceholderText('Email'), { target: { value: email } })
    fireEvent.click(screen.getByRole('button', { name: /send reset link/i }))
}

const NEUTRAL = 'If that address has an account, a reset link is on its way.'

describe('forgot password', () => {
    afterEach(() => {
        cleanup()
        setAuthToken(null)
        vi.unstubAllGlobals()
    })

    it('posts the address to /auth/forgot', async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }))
        vi.stubGlobal('fetch', fetchMock)

        renderScreen()
        askForReset()

        await waitFor(() => expect(screen.getByText(NEUTRAL)).toBeTruthy())
        const [url, init] = fetchMock.mock.calls[0]
        expect(url).toBe('/auth/forgot')
        expect(JSON.parse(init.body)).toEqual({ email: 'alice@example.com' })
    })

    // The server answers the same for a known and an unknown address; so must the screen, or the
    // difference between the two responses becomes a user-enumeration oracle.
    it('reads identically for an address with no account', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true })))
        renderScreen()
        askForReset('nobody@example.com')
        await waitFor(() => expect(screen.getByText(NEUTRAL)).toBeTruthy())
        expect(screen.queryByPlaceholderText('Email')).toBeNull()
    })
})
