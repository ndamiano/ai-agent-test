import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { CreateGame } from './CreateGame'
import { AuthProvider } from '../../contexts/AuthContext'
import { api } from '../../api/client'

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.removeItem('maestro_skip_enhance_notice') })

const create = (onCreated = () => {}, onCancel = () => {}) => render(
    <AuthProvider>
        <CreateGame onCreated={onCreated} onCancel={onCancel} />
    </AuthProvider>,
)

const box = () => screen.getByPlaceholderText(/snail racing game/i) as HTMLTextAreaElement
const buildButton = () => screen.getByRole('button', { name: /build it/i })
const toggle = () => screen.getByLabelText(/plan the build first/i) as HTMLInputElement

describe('CreateGame', () => {
    it('cannot build an empty request', () => {
        create()
        expect(buildButton().hasAttribute('disabled')).toBe(true)
    })

    it('fills the box from an example', () => {
        create()

        fireEvent.click(screen.getByText(/a crab running a small bank/i))

        expect(box().value).toMatch(/^A crab running a small bank/)
        expect(buildButton().hasAttribute('disabled')).toBe(false)
    })

    it('plans first by default', async () => {
        const call = vi.spyOn(api, 'enhancePrompt').mockResolvedValue(
            { run_id: 'r1', prompt: 'a game about tides', stages: ['the tide sim alone', 'add the moon'] })
        create()

        fireEvent.change(box(), { target: { value: 'a game about tides' } })
        fireEvent.click(buildButton())

        await waitFor(() => expect(screen.getByText(/the build plan/i)).toBeTruthy())
        expect(call).toHaveBeenCalledWith('a game about tides')
        expect(screen.getByText(/you asked for/i)).toBeTruthy()
        expect((screen.getAllByRole('textbox')[0] as HTMLTextAreaElement).value).toBe('the tide sim alone')
    })

    // The stage texts are the artifact: what is in the boxes is what builds, edits included.
    it('builds the stages exactly as shown, edits included', async () => {
        const onCreated = vi.fn()
        vi.spyOn(api, 'enhancePrompt').mockResolvedValue(
            { run_id: 'r1', prompt: 'x', stages: ['stage one', 'stage two'] })
        const call = vi.spyOn(api, 'buildStages').mockResolvedValue({ run_id: 'r1', status: 'building' })
        create(onCreated)

        fireEvent.change(box(), { target: { value: 'x' } })
        fireEvent.click(buildButton())
        await waitFor(() => expect(screen.getByText(/the build plan/i)).toBeTruthy())

        const stageBoxes = screen.getAllByRole('textbox')
        fireEvent.change(stageBoxes[1], { target: { value: 'stage two, but cozier' } })
        fireEvent.click(buildButton())

        await waitFor(() => expect(onCreated).toHaveBeenCalledWith('r1'))
        expect(call).toHaveBeenCalledWith('r1', ['stage one', 'stage two, but cozier'])
    })

    it('a one-stage plan skips review and builds plain', async () => {
        const onCreated = vi.fn()
        vi.spyOn(api, 'enhancePrompt').mockResolvedValue(
            { run_id: 'r1', prompt: 'tiny game', stages: ['tiny game'] })
        const call = vi.spyOn(api, 'buildGame').mockResolvedValue({ run_id: 'r1', status: 'building' })
        create(onCreated)

        fireEvent.change(box(), { target: { value: 'tiny game' } })
        fireEvent.click(buildButton())

        await waitFor(() => expect(onCreated).toHaveBeenCalledWith('r1'))
        expect(call).toHaveBeenCalledWith('r1', 'tiny game')
    })

    it('opting out shows the notice once, and building anyway sends the box as typed', async () => {
        const onCreated = vi.fn()
        const call = vi.spyOn(api, 'createGame').mockResolvedValue({ run_id: 'r9', status: 'building' })
        create(onCreated)

        fireEvent.change(box(), { target: { value: '  a game about tides  ' } })
        fireEvent.click(toggle())
        fireEvent.click(buildButton())

        expect(screen.getByText(/build without the plan/i)).toBeTruthy()
        fireEvent.click(screen.getByRole('button', { name: /build anyway/i }))

        await waitFor(() => expect(onCreated).toHaveBeenCalledWith('r9'))
        expect(call).toHaveBeenCalledWith('  a game about tides  ')
    })

    it('the notice can plan instead', async () => {
        vi.spyOn(api, 'enhancePrompt').mockResolvedValue(
            { run_id: 'r1', prompt: 'x', stages: ['one', 'two'] })
        create()

        fireEvent.change(box(), { target: { value: 'x' } })
        fireEvent.click(toggle())
        fireEvent.click(buildButton())
        fireEvent.click(screen.getByRole('button', { name: /plan it first/i }))

        await waitFor(() => expect(screen.getByText(/the build plan/i)).toBeTruthy())
    })

    it('don\'t-show-again is remembered client-side', async () => {
        vi.spyOn(api, 'createGame').mockResolvedValue({ run_id: 'r9', status: 'building' })
        create()

        fireEvent.change(box(), { target: { value: 'a game' } })
        fireEvent.click(toggle())
        fireEvent.click(buildButton())
        fireEvent.click(screen.getByLabelText(/don't show this again/i))
        fireEvent.click(screen.getByRole('button', { name: /build anyway/i }))

        await waitFor(() => expect(localStorage.getItem('maestro_skip_enhance_notice')).toBe('1'))

        cleanup()
        create()
        fireEvent.change(box(), { target: { value: 'another game' } })
        fireEvent.click(toggle())
        fireEvent.click(buildButton())
        expect(screen.queryByText(/build without the plan/i)).toBeNull()
    })

    it('says what went wrong when the plan cannot start', async () => {
        vi.spyOn(api, 'enhancePrompt').mockRejectedValue(new Error('no credits'))
        create()

        fireEvent.change(box(), { target: { value: 'a game about tides' } })
        fireEvent.click(buildButton())

        await waitFor(() => expect(screen.getByText(/no credits/i)).toBeTruthy())
    })
})
