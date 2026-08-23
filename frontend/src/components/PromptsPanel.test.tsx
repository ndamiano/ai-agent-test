import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import PromptsPanel, { bucketLabel, groupTurns, turnLabel } from './PromptsPanel'
import type { IndexedTurn } from './PromptsPanel'
import { api } from '../api/client'
import type { PromptBucket, PromptDetail, PromptTurn } from '../types'

const bucket = (over: Partial<PromptBucket> = {}): PromptBucket => ({
    game_id: 'g1', turns: 2, last_at: 1785115243, exec_seconds: 7,
    title: 'Moon Miner', status: 'built', user_id: 'u1', ...over,
})

const turn = (over: Partial<PromptTurn> = {}): PromptTurn => ({
    id: 'j1', game_id: 'g1', build_id: 'b1', status: 'done', model: 'qwen',
    created_at: 1785115243, started_at: null, finished_at: null, exec_seconds: 3.5, error: null,
    metadata: { stage: 'build' }, payload_chars: 8153, system_hash: 'aaa1',
    system_head: 'You author ONE file.\nmore', system_chars: 1665, n_messages: 2, ...over,
})

const audit = (over: Partial<PromptTurn> = {}): PromptTurn =>
    turn({ id: 'j2', system_hash: 'bbb2', system_head: 'You audit ONE claim.', ...over })

const indexed = (turns: PromptTurn[]): IndexedTurn[] => turns.map((t, i) => ({ ...t, index: i + 1 }))

const detail: PromptDetail = {
    id: 'j1', game_id: 'g1', build_id: 'b1', status: 'done', model: 'qwen', created_at: 1785115243,
    exec_seconds: 3.5, error: null, stage: 'build', reasoning: 'none',
    system: 'You author ONE file.',
    messages: [{ role: 'user', kind: 'text', name: null, text: '# TASK write game.ts' }],
    tools: [], response: { text: 'authoring now', tool_calls: [], usage: {} },
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('labels', () => {
    it('names a turn by the first line of its system prompt', () => {
        expect(turnLabel(turn())).toBe('You author ONE file.')
        expect(turnLabel(turn({ system_head: null }))).toBe('(no system prompt)')
    })

    it('names the ownerless bucket for what it holds', () => {
        expect(bucketLabel(bucket())).toBe('Moon Miner')
        expect(bucketLabel(bucket({ game_id: null, title: null }))).toBe('Platform (chat + spec drafts)')
        expect(bucketLabel(bucket({ title: null }))).toBe('g1')
    })
})

describe('groupTurns', () => {
    it('collapses the log onto one group per system prompt, in first-appearance order', () => {
        const groups = groupTurns(indexed([turn(), audit(), turn({ id: 'j3', exec_seconds: 1.5 })]))

        expect(groups.map(g => [g.hash, g.turns.length])).toEqual([['aaa1', 2], ['bbb2', 1]])
        expect(groups[0].label).toBe('You author ONE file.')
        expect(groups[0].execSeconds).toBe(5)
        expect(groups[0].turns.map(t => t.index)).toEqual([1, 3])
    })

    it('splits turns whose system prompt differs even when their first line matches', () => {
        const groups = groupTurns(indexed([turn(), turn({ id: 'j4', system_hash: 'ccc3' })]))

        expect(groups.map(g => g.hash)).toEqual(['aaa1', 'ccc3'])
    })
})

describe('PromptsPanel', () => {
    it('lists every bucket, groups the turns, and loads one on click', async () => {
        vi.spyOn(api, 'getPromptBuckets').mockResolvedValue([bucket(), bucket({ game_id: null, title: null, turns: 5 })])
        vi.spyOn(api, 'getPromptTurns').mockResolvedValue([turn(), audit()])
        const getOne = vi.spyOn(api, 'getPromptTurn').mockResolvedValue(detail)

        render(<PromptsPanel />)
        // The bucket list and the turn list load on separate promises — wait for both, or the
        // group is still empty when the click below expands it.
        await waitFor(() => expect(screen.getByText('2 of 2 turns · 2 prompts')).toBeTruthy())
        expect(screen.getByText('Moon Miner')).toBeTruthy()
        expect(screen.getByText('Platform (chat + spec drafts)')).toBeTruthy()

        // Two system prompts ⇒ two collapsed headers, no turn rows yet.
        expect(screen.getAllByText('You author ONE file.')).toHaveLength(1)

        fireEvent.click(screen.getByText('You author ONE file.'))          // expand the group
        await waitFor(() => expect(screen.getAllByText('You author ONE file.')).toHaveLength(2))
        const [, row] = screen.getAllByText('You author ONE file.')        // header, then the turn
        fireEvent.click(row)

        await waitFor(() => expect(screen.getByText('# TASK write game.ts')).toBeTruthy())
        expect(getOne).toHaveBeenCalledWith('j1')
        expect(screen.getByText('authoring now')).toBeTruthy()
    })

    it('opens a lone group rather than making the reader click through it', async () => {
        vi.spyOn(api, 'getPromptBuckets').mockResolvedValue([bucket()])
        vi.spyOn(api, 'getPromptTurns').mockResolvedValue([turn(), turn({ id: 'j3' })])

        render(<PromptsPanel />)

        // One header plus both turn rows, all showing the same label.
        await waitFor(() => expect(screen.getAllByText('You author ONE file.')).toHaveLength(3))
    })

    it('drops back to a flat list when grouping is switched off', async () => {
        vi.spyOn(api, 'getPromptBuckets').mockResolvedValue([bucket()])
        vi.spyOn(api, 'getPromptTurns').mockResolvedValue([turn(), audit()])

        render(<PromptsPanel />)
        await waitFor(() => expect(screen.getByText('2 of 2 turns · 2 prompts')).toBeTruthy())

        fireEvent.click(screen.getByText('group'))

        expect(screen.getByText('2 of 2 turns')).toBeTruthy()
        expect(screen.getAllByText('You author ONE file.')).toHaveLength(1)
        expect(screen.getAllByText('You audit ONE claim.')).toHaveLength(1)
    })

    it('scopes the turn fetch to the picked game', async () => {
        vi.spyOn(api, 'getPromptBuckets').mockResolvedValue([bucket(), bucket({ game_id: null, title: null })])
        const getTurns = vi.spyOn(api, 'getPromptTurns').mockResolvedValue([turn()])

        render(<PromptsPanel />)
        await waitFor(() => expect(getTurns).toHaveBeenCalledWith('all', null))

        fireEvent.click(screen.getByText('Moon Miner'))
        await waitFor(() => expect(getTurns).toHaveBeenCalledWith('game', 'g1'))

        fireEvent.click(screen.getByText('Platform (chat + spec drafts)'))
        await waitFor(() => expect(getTurns).toHaveBeenCalledWith('platform', null))
    })

    it('puts the newest turn at the top and keeps every turn its ordinal', async () => {
        vi.spyOn(api, 'getPromptBuckets').mockResolvedValue([bucket()])
        vi.spyOn(api, 'getPromptTurns').mockResolvedValue([turn(), audit()])   // server: oldest first

        render(<PromptsPanel />)
        await waitFor(() => expect(screen.getByText('2 of 2 turns · 2 prompts')).toBeTruthy())

        const labels = () => screen.getAllByText(/^You (author|audit) ONE (file|claim)\.$/)
            .map(n => n.textContent)
        expect(labels()).toEqual(['You audit ONE claim.', 'You author ONE file.'])

        fireEvent.click(screen.getByText('group'))

        expect(labels()).toEqual(['You audit ONE claim.', 'You author ONE file.'])
        const rows = screen.getAllByText(/^You (author|audit) ONE (file|claim)\.$/)
            .map(n => n.closest('button') as HTMLElement)
        expect(within(rows[0]).getByText('2')).toBeTruthy()
        expect(within(rows[1]).getByText('1')).toBeTruthy()
    })

    it('filters the turn list by label', async () => {
        vi.spyOn(api, 'getPromptBuckets').mockResolvedValue([bucket()])
        vi.spyOn(api, 'getPromptTurns').mockResolvedValue([turn(), audit()])

        render(<PromptsPanel />)
        await waitFor(() => expect(screen.getByText('You audit ONE claim.')).toBeTruthy())

        fireEvent.change(screen.getByPlaceholderText('filter turns…'), { target: { value: 'audit' } })

        expect(screen.queryByText('You author ONE file.')).toBeNull()
        // The surviving turn's group is now the only one, so it opens: header + row.
        expect(screen.getAllByText('You audit ONE claim.')).toHaveLength(2)
    })
})
