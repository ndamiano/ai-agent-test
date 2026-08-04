import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import ResetScreen from './ResetScreen'
import { AuthProvider } from '../contexts/AuthContext'
import { RouterProvider } from '../router'
import { getAuthToken, setAuthToken } from '../api/client'

function renderScreen(search = '?t=reset-token') {
    window.history.pushState(null, '', `/reset${search}`)
    return render(
        <AuthProvider>
            <RouterProvider>
                <ResetScreen />
            </RouterProvider>
        </AuthProvider>,
    )
}

const jsonResponse = (status: number, body: unknown) => ({
    ok: status < 400, status,
    headers: { get: () => null },
    json: async () => body,
})

const fillReset = (password = 'a-long-password', confirm = password) => {
    fireEvent.change(screen.getByPlaceholderText('New password'), { target: { value: password } })
    fireEvent.change(screen.getByPlaceholderText('New password, again'), { target: { value: confirm } })
    fireEvent.click(screen.getByRole('button', { name: /set password/i }))
}

describe('reset screen', () => {
    beforeEach(() => setAuthToken(null))
    afterEach(() => {
        cleanup()
        setAuthToken(null)
        vi.unstubAllGlobals()
        window.history.pushState(null, '', '/')
    })

    it('posts the token from the link and signs the user in', async () => {
        const fetchMock = vi.fn().mockImplementation((url: string) =>
            Promise.resolve(String(url).includes('/auth/reset')
                ? jsonResponse(200, { token: 'tok-new', user: { id: 'u1', handle: 'alice', role: 'user' } })
                : jsonResponse(200, { id: 'u1', handle: 'alice', role: 'user', email: 'a@b.c', balance: 0 })))
        vi.stubGlobal('fetch', fetchMock)

        renderScreen()
        fillReset()

        await waitFor(() => expect(getAuthToken()).toBe('tok-new'))
        const [url, init] = fetchMock.mock.calls[0]
        expect(url).toBe('/auth/reset')
        expect(JSON.parse(init.body)).toEqual({ token: 'reset-token', new_password: 'a-long-password' })
    })

    it('surfaces an expired link with the server sentence', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
            jsonResponse(400, { detail: 'that reset link has expired' })))
        renderScreen()
        fillReset()
        await waitFor(() => expect(screen.getByText('That reset link has expired.')).toBeTruthy())
        expect(getAuthToken()).toBeNull()
    })

    it('refuses a mismatched confirmation without calling the API', () => {
        const fetchMock = vi.fn()
        vi.stubGlobal('fetch', fetchMock)
        renderScreen()
        fillReset('a-long-password', 'a-different-one')
        expect(screen.getByText('The new passwords do not match.')).toBeTruthy()
        expect(fetchMock).not.toHaveBeenCalled()
    })

    it('says so when the link carries no token', () => {
        renderScreen('')
        expect(screen.queryByPlaceholderText('New password')).toBeNull()
        expect(screen.getByText(/missing its token/i)).toBeTruthy()
    })
})
