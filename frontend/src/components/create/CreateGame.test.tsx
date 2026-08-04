import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { CreateGame } from './CreateGame'
import { AuthProvider } from '../../contexts/AuthContext'
import { api } from '../../api/client'

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.removeItem('maestro_skip_enhance_notice') })

const create = (onCreated = () => {}, onCancel = () => {}) => {
    vi.spyOn(api, 'listGames').mockResolvedValue([])
    return render(
        <AuthProvider>
            <CreateGame onCreated={onCreated} onCancel={onCancel} />
        </AuthProvider>,
    )
}

const box = () => screen.getByPlaceholderText(/snail racing game/i) as HTMLTextAreaElement
const planButton = () => screen.getByRole('button', { name: /plan it/i })
const buildButton = () => screen.getByRole('button', { name: /build it/i })
const toggle = () => screen.getByLabelText(/plan the build first/i) as HTMLInputElement

describe('CreateGame', () => {
    it('cannot plan an empty request', () => {
        create()
        expect(planButton().hasAttribute('disabled')).toBe(true)
    })

    it('fills the box from an example', () => {
        create()

        fireEvent.click(screen.getByText(/a crab running a small bank/i))

        expect(box().value).toMatch(/^A crab running a small bank/)
        expect(planButton().hasAttribute('disabled')).toBe(false)
    })

    it('the primary button charges at PLAN time and says so', async () => {
        const call = vi.spyOn(api, 'enhancePrompt').mockResolvedValue(
            { run_id: 'r1', prompt: 'a game about tides', stages: ['the tide sim alone', 'add the moon'] })
        create()

        expect(planButton().textContent).toMatch(/1 credit/)
        fireEvent.change(box(), { target: { value: 'a game about tides' } })
        fireEvent.click(planButton())

        await waitFor(() => expect(screen.getByText(/the build plan/i)).toBeTruthy())
        expect(call).toHaveBeenCalledWith('a game about tides', undefined)
        expect(screen.getByText(/already paid for/i)).toBeTruthy()
    })

    // The stage texts are the artifact: what is in the boxes is what builds, edits included.
    it('builds the stages exactly as shown, edits included', async () => {
        const onCreated = vi.fn()
        vi.spyOn(api, 'enhancePrompt').mockResolvedValue(
            { run_id: 'r1', prompt: 'x', stages: ['stage one', 'stage two'] })
        const call = vi.spyOn(api, 'buildStages').mockResolvedValue({ run_id: 'r1', status: 'building' })
        create(onCreated)

        fireEvent.change(box(), { target: { value: 'x' } })
        fireEvent.click(planButton())
        await waitFor(() => expect(screen.getByText(/the build plan/i)).toBeTruthy())

        const stageBoxes = screen.getAllByRole('textbox')
        fireEvent.change(stageBoxes[1], { target: { value: 'stage two, but cozier' } })
        fireEvent.click(buildButton())

        await waitFor(() => expect(onCreated).toHaveBeenCalledWith('r1'))
        expect(call).toHaveBeenCalledWith('r1', ['stage one', 'stage two, but cozier'])
    })

    it('a one-stage plan still shows review — nothing builds unseen', async () => {
        vi.spyOn(api, 'enhancePrompt').mockResolvedValue(
            { run_id: 'r1', prompt: 'tiny game', stages: ['tiny game'] })
        const plain = vi.spyOn(api, 'buildGame').mockResolvedValue({ run_id: 'r1', status: 'building' })
        create()

        fireEvent.change(box(), { target: { value: 'tiny game' } })
        fireEvent.click(planButton())

        await waitFor(() => expect(screen.getByText(/the build plan/i)).toBeTruthy())
        expect(screen.getByText(/the build request/i)).toBeTruthy()
        expect(plain).not.toHaveBeenCalled()
    })

    it('going back and planning again reuses the paid run', async () => {
        const call = vi.spyOn(api, 'enhancePrompt').mockResolvedValue(
            { run_id: 'r1', prompt: 'x', stages: ['one', 'two'] })
        create()

        fireEvent.change(box(), { target: { value: 'x' } })
        fireEvent.click(planButton())
        await waitFor(() => expect(screen.getByText(/the build plan/i)).toBeTruthy())

        fireEvent.click(screen.getByRole('button', { name: /back to my words/i }))
        fireEvent.change(box(), { target: { value: 'x, but cozier' } })
        expect(screen.getByRole('button', { name: /plan it again/i })).toBeTruthy()
        fireEvent.click(screen.getByRole('button', { name: /plan it again/i }))

        await waitFor(() => expect(call).toHaveBeenLastCalledWith('x, but cozier', 'r1'))
    })

    it('unchecking after paying builds the PAID run plain — never a second charge', async () => {
        const onCreated = vi.fn()
        vi.spyOn(api, 'enhancePrompt').mockResolvedValue(
            { run_id: 'r1', prompt: 'x', stages: ['one', 'two'] })
        const plain = vi.spyOn(api, 'buildGame').mockResolvedValue({ run_id: 'r1', status: 'building' })
        const fresh = vi.spyOn(api, 'createGame')
        create(onCreated)

        fireEvent.change(box(), { target: { value: 'x' } })
        fireEvent.click(planButton())
        await waitFor(() => expect(screen.getByText(/the build plan/i)).toBeTruthy())
        fireEvent.click(screen.getByRole('button', { name: /back to my words/i }))
        fireEvent.click(toggle())

        const btn = screen.getByRole('button', { name: /^build it$/i })
        fireEvent.click(btn)

        await waitFor(() => expect(onCreated).toHaveBeenCalledWith('r1'))
        expect(plain).toHaveBeenCalledWith('r1', 'x')
        expect(fresh).not.toHaveBeenCalled()
    })

    it('opting out from the start shows the notice once, then builds as typed', async () => {
        const onCreated = vi.fn()
        const call = vi.spyOn(api, 'createGame').mockResolvedValue({ run_id: 'r9', status: 'building' })
        create(onCreated)

        fireEvent.change(box(), { target: { value: '  a game about tides  ' } })
        fireEvent.click(toggle())
        fireEvent.click(screen.getByRole('button', { name: /build it · 1 credit/i }))

        expect(screen.getByText(/build without the plan/i)).toBeTruthy()
        fireEvent.click(screen.getByRole('button', { name: /build anyway/i }))

        await waitFor(() => expect(onCreated).toHaveBeenCalledWith('r9'))
        expect(call).toHaveBeenCalledWith('  a game about tides  ')
    })

    it('an unstarted plan is offered and resumes into review', async () => {
        vi.spyOn(api, 'listGames').mockResolvedValue([
            { run_id: 'r7', title: 'Card RPG', status: 'idle', built: false, building: false, paused: false, unstarted_plan: true }])
        vi.spyOn(api, 'getGame').mockResolvedValue({
            run_id: 'r7', prompt: 'card rpg for ante', plan: ['duel first', 'then world'],
        } as any)
        render(
            <AuthProvider>
                <CreateGame onCreated={() => {}} onCancel={() => {}} />
            </AuthProvider>,
        )

        await waitFor(() => expect(screen.getByText(/unstarted build in progress/i)).toBeTruthy())
        fireEvent.click(screen.getByRole('button', { name: /open it/i }))

        await waitFor(() => expect(screen.getByText(/the build plan/i)).toBeTruthy())
        expect((screen.getAllByRole('textbox')[0] as HTMLTextAreaElement).value).toBe('duel first')
    })

    it('says what went wrong when the plan cannot start', async () => {
        vi.spyOn(api, 'enhancePrompt').mockRejectedValue(new Error('no credits'))
        create()

        fireEvent.change(box(), { target: { value: 'a game about tides' } })
        fireEvent.click(planButton())

        await waitFor(() => expect(screen.getByText(/no credits/i)).toBeTruthy())
    })
})
