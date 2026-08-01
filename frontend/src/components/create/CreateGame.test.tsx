import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { CreateGame } from './CreateGame'
import { AuthProvider } from '../../contexts/AuthContext'
import { api } from '../../api/client'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const create = (onCreated = () => {}, onCancel = () => {}) => render(
    <AuthProvider>
        <CreateGame onCreated={onCreated} onCancel={onCancel} />
    </AuthProvider>,
)

const box = () => screen.getByPlaceholderText(/snail racing game/i) as HTMLTextAreaElement

describe('CreateGame', () => {
    it('cannot build an empty request', () => {
        create()
        expect(screen.getByRole('button', { name: /build it/i }).hasAttribute('disabled')).toBe(true)
    })

    it('fills the box from an example', () => {
        create()

        fireEvent.click(screen.getByText(/a crab running a small bank/i))

        expect(box().value).toMatch(/^A crab running a small bank/)
        expect(screen.getByRole('button', { name: /build it/i }).hasAttribute('disabled')).toBe(false)
    })

    // The prompt is the artifact: what is in the box is what gets sent, byte for byte.
    it('sends the box exactly as typed', async () => {
        const onCreated = vi.fn()
        const call = vi.spyOn(api, 'createGame').mockResolvedValue({ run_id: 'r9', status: 'building' })
        create(onCreated)

        fireEvent.change(box(), { target: { value: '  a game about tides  ' } })
        fireEvent.click(screen.getByRole('button', { name: /build it/i }))

        await waitFor(() => expect(onCreated).toHaveBeenCalledWith('r9'))
        expect(call).toHaveBeenCalledWith('  a game about tides  ')
    })

    it('says what went wrong when the build cannot start', async () => {
        vi.spyOn(api, 'createGame').mockRejectedValue(new Error('no credits'))
        create()

        fireEvent.change(box(), { target: { value: 'a game about tides' } })
        fireEvent.click(screen.getByRole('button', { name: /build it/i }))

        await waitFor(() => expect(screen.getByText(/no credits/i)).toBeTruthy())
    })
})
