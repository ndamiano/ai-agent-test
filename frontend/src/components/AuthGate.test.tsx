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
        // jsdom has no layout engine — ChatPanel scrolls its anchor into view on mount.
        Element.prototype.scrollIntoView = vi.fn()
    })
    afterEach(() => {
        cleanup()
        setAuthToken(null)
        vi.unstubAllGlobals()
    })

    it('shows the login screen when there is no token', () => {
        setAuthToken(null)
        renderApp()
        expect(screen.getByRole('button', { name: /sign in/i })).toBeTruthy()
    })

    it('shows the app (not the login screen) when a token is present, and renders the balance', async () => {
        setAuthToken('tok')
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({ id: 'u1', handle: 'alice', role: 'user', balance: 42 }),
        }))

        renderApp()

        await waitFor(() => expect(screen.getByText(/42/)).toBeTruthy())
        expect(screen.queryByRole('button', { name: /^sign in$/i })).toBeNull()
        expect(screen.getByText('credits')).toBeTruthy()
    })
})
