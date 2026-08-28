import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { GameView } from './GameView'
import { AuthProvider } from '../../contexts/AuthContext'
import { api } from '../../api/client'
import type { GameDetail, WebSocketMessage } from '../../types'

let listener: ((msg: WebSocketMessage) => void) | null = null
vi.mock('../../contexts/WebSocketContext', () => ({
    useWebSocket: () => ({
        messages: [], connected: false,
        subscribe: (_runId: string, cb: (msg: WebSocketMessage) => void) => { listener = cb; return () => { listener = null } },
    }),
}))

afterEach(() => { cleanup(); vi.restoreAllMocks(); listener = null })

const ASK = 'a snail racing game over four in-game days'
const DESIGN = 'RACE LOOP: four snails crawl a lane each day.\n\nDAY CYCLE: the sun crosses the track.'

const detail = (over: Partial<GameDetail> = {}): GameDetail => ({
    run_id: 'r1', ask: ASK, prompt: DESIGN, title: '', built: false, building: false, status: 'idle',
    has_game: false, budget_pct_remaining: null, ...over,
})

const mount = (first: GameDetail, ...later: GameDetail[]) => {
    const get = vi.spyOn(api, 'getGame').mockResolvedValueOnce(first)
    for (const d of later) get.mockResolvedValueOnce(d)
    vi.spyOn(api, 'getGameEvents').mockResolvedValue([])
    vi.spyOn(api, 'getGameAssets').mockRejectedValue(new Error('none'))
    render(
        <AuthProvider>
            <GameView runId="r1" onChanged={() => {}} onBack={() => {}} />
        </AuthProvider>,
    )
    return get
}

const buildButton = () => screen.getByRole('button', { name: /build it/i })
const editButton = () => screen.getByRole('button', { name: /^edit$/i })

describe('GameView before the first build', () => {
    it('shows the design as headed sections with the ask above it', async () => {
        mount(detail())

        expect(await screen.findByText('RACE LOOP')).toBeTruthy()
        expect(screen.getByText('DAY CYCLE')).toBeTruthy()
        expect(screen.getByText(/four snails crawl a lane each day/)).toBeTruthy()
        expect(screen.getByText(ASK)).toBeTruthy()
        expect(screen.queryByPlaceholderText(/describe the game/i)).toBeNull()
        expect(buildButton().hasAttribute('disabled')).toBe(false)
    })

    it('edits the same text the sections are read from, and builds exactly that', async () => {
        mount(detail())
        const build = vi.spyOn(api, 'buildGame').mockResolvedValue({ run_id: 'r1', status: 'building' })
        await screen.findByText('RACE LOOP')

        fireEvent.click(editButton())
        const box = screen.getByPlaceholderText(/describe the game/i) as HTMLTextAreaElement
        expect(box.value).toBe(DESIGN)
        fireEvent.change(box, { target: { value: `${DESIGN}\n\nWEATHER: rain slows every snail.` } })
        fireEvent.click(screen.getByRole('button', { name: /done editing/i }))

        expect(screen.getByText('WEATHER')).toBeTruthy()
        expect(screen.getByText(/rebuild uses this text/i)).toBeTruthy()
        fireEvent.click(buildButton())
        await waitFor(() => expect(build).toHaveBeenCalledWith('r1', `${DESIGN}\n\nWEATHER: rain slows every snail.`))
    })

    it('is designing while the spec has no request — even on a reload', async () => {
        mount(detail({ prompt: null }))

        expect(await screen.findByRole('status')).toBeTruthy()
        expect(screen.getByText(/writing the design from your request/i)).toBeTruthy()
        expect(screen.getByText(ASK)).toBeTruthy()
        expect(screen.queryByPlaceholderText(/describe the game/i)).toBeNull()
        expect(buildButton().hasAttribute('disabled')).toBe(true)
        expect(screen.queryByRole('progressbar')).toBeNull()
    })

    it('fills the box when prompt_proposed lands', async () => {
        mount(detail({ prompt: null }), detail())
        await screen.findByRole('status')

        act(() => { listener?.({ type: 'prompt_proposed', run_id: 'r1' }) })

        expect(await screen.findByText('RACE LOOP')).toBeTruthy()
        fireEvent.click(editButton())
        expect((screen.getByPlaceholderText(/describe the game/i) as HTMLTextAreaElement).value).toBe(DESIGN)
        expect(screen.queryByRole('status')).toBeNull()
        expect(buildButton().hasAttribute('disabled')).toBe(false)
    })
})
