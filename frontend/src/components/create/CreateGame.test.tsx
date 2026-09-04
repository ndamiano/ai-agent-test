import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { CreateGame } from './CreateGame'
import { AuthProvider } from '../../contexts/AuthContext'
import { api } from '../../api/client'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const create = (onCreated = () => {}, onCancel = () => {}) =>
    render(
        <AuthProvider>
            <CreateGame onCreated={onCreated} onCancel={onCancel} />
        </AuthProvider>,
    )

const box = () => screen.getByPlaceholderText(/snail racing game/i) as HTMLTextAreaElement
const designButton = () => screen.getByRole('button', { name: /summon it/i })

describe('CreateGame', () => {
    it('cannot design an empty request', () => {
        create()
        expect(designButton().hasAttribute('disabled')).toBe(true)
    })

    it('fills the box from an example', () => {
        create()

        fireEvent.click(screen.getByText(/a crab running a small bank/i))

        expect(box().value).toMatch(/^A crab running a small bank/)
        expect(designButton().hasAttribute('disabled')).toBe(false)
    })

    it('sends the words as typed and hands over to the game page', async () => {
        const onCreated = vi.fn()
        const call = vi.spyOn(api, 'createGame').mockResolvedValue({ run_id: 'r9', status: 'designing' })
        create(onCreated)

        expect(designButton().textContent).toMatch(/1 credit/)
        fireEvent.change(box(), { target: { value: '  a game about tides  ' } })
        fireEvent.click(designButton())

        await waitFor(() => expect(onCreated).toHaveBeenCalledWith('r9'))
        expect(call).toHaveBeenCalledWith('  a game about tides  ')
    })

    it('says what went wrong when the design cannot start', async () => {
        vi.spyOn(api, 'createGame').mockRejectedValue(new Error('no credits'))
        create()

        fireEvent.change(box(), { target: { value: 'a game about tides' } })
        fireEvent.click(designButton())

        await waitFor(() => expect(screen.getByText(/no credits/i)).toBeTruthy())
    })
})
