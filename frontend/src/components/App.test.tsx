import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import App from '../App'
import { AuthProvider } from '../contexts/AuthContext'
import { WebSocketProvider } from '../contexts/WebSocketContext'
import { setAuthToken } from '../api/client'

// The WS connects when authed; stub it so mounting the app doesn't open a real socket.
class FakeWebSocket {
    onopen: (() => void) | null = null
    onmessage: ((e: unknown) => void) | null = null
    onclose: (() => void) | null = null
    onerror: (() => void) | null = null
    close() {}
}

function renderApp() {
    return render(
        <AuthProvider>
            <WebSocketProvider>
                <App />
            </WebSocketProvider>
        </AuthProvider>,
    )
}

describe('auth gate', () => {
    beforeEach(() => {
        vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
        Element.prototype.scrollIntoView = vi.fn()
    })
    afterEach(() => {
        cleanup()
        setAuthToken(null)
        vi.unstubAllGlobals()
    })

    it('shows the landing page, not a login form, when there is no token', () => {
        setAuthToken(null)
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] }))
        window.history.pushState(null, '', '/')
        renderApp()
        expect(screen.getByText(/describe a game/i)).toBeTruthy()
        expect(screen.queryByRole('button', { name: /^sign in$/i })).toBeNull()
    })

    it('serves the sign-in form at /login', () => {
        setAuthToken(null)
        window.history.pushState(null, '', '/login')
        renderApp()
        expect(screen.getByRole('button', { name: /sign in/i })).toBeTruthy()
        window.history.pushState(null, '', '/')
    })

    it('shows the app (not the login screen) when a token is present, and renders the balance', async () => {
        setAuthToken('tok')
        // Routed by URL: the games list mounts with the app now, and handing it the /auth/me
        // object would break the render before the balance ever appears.
        vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve({
            ok: true, status: 200,
            json: async () => String(url).includes('/games')
                ? []
                : { id: 'u1', handle: 'alice', role: 'user', balance: 42 },
        })))

        renderApp()

        await waitFor(() => expect(screen.getByText(/42/)).toBeTruthy())
        expect(screen.queryByRole('button', { name: /^sign in$/i })).toBeNull()
        expect(screen.getByText('credits')).toBeTruthy()
    })
})
