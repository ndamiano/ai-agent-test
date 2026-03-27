import { describe, it, expect } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useMemo } from 'react'
import { mergeSubtasks, type SubtaskState } from './useTaskStage'
import type { WebSocketMessage } from '../types'

const makeSubtask = (overrides: Partial<SubtaskState> = {}): SubtaskState => ({
    id: 's1',
    agentId: 'agent1',
    goal: 'do something',
    name: 'Task 1',
    description: null,
    status: 'pending',
    outputPreview: null,
    position: 0,
    ...overrides,
})

const makeMsg = (overrides: Record<string, unknown> & { type: WebSocketMessage['type'] }): WebSocketMessage => {
    return overrides as WebSocketMessage
}

describe('mergeSubtasks', () => {
    it('returns empty array when base is empty', () => {
        const result = mergeSubtasks([], [])
        expect(result).toEqual([])
    })

    it('returns base unchanged when no relevant messages', () => {
        const base = [makeSubtask({ id: 's1', status: 'pending' })]
        const result = mergeSubtasks(base, [
            makeMsg({ type: 'task_status', task_id: 't1' }),
        ])
        expect(result).toEqual(base)
    })

    it('sets status to in_progress on subtask_started', () => {
        const base = [makeSubtask({ id: 's1', status: 'pending' })]
        const result = mergeSubtasks(base, [
            makeMsg({ type: 'subtask_started', task_id: 't1', subtask_id: 's1', agent_id: 'agent1', timestamp: '' }),
        ])
        expect(result[0].status).toBe('in_progress')
    })

    it('sets status to completed on subtask_completed', () => {
        const base = [makeSubtask({ id: 's1', status: 'in_progress' })]
        const result = mergeSubtasks(base, [
            makeMsg({ type: 'subtask_completed', task_id: 't1', subtask_id: 's1', agent_id: 'agent1', timestamp: '' }),
        ])
        expect(result[0].status).toBe('completed')
    })

    it('sets status to failed on subtask_failed', () => {
        const base = [makeSubtask({ id: 's1', status: 'in_progress' })]
        const result = mergeSubtasks(base, [
            makeMsg({ type: 'subtask_failed', task_id: 't1', subtask_id: 's1', error: '', timestamp: '' }),
        ])
        expect(result[0].status).toBe('failed')
    })

    it('last message wins when multiple messages for same subtask', () => {
        const base = [makeSubtask({ id: 's1', status: 'pending' })]
        const result = mergeSubtasks(base, [
            makeMsg({ type: 'subtask_started', task_id: 't1', subtask_id: 's1', agent_id: 'agent1', timestamp: '' }),
            makeMsg({ type: 'subtask_completed', task_id: 't1', subtask_id: 's1', agent_id: 'agent1', timestamp: '' }),
        ])
        expect(result[0].status).toBe('completed')
    })

    it('handles multiple subtasks independently', () => {
        const base = [
            makeSubtask({ id: 's1', status: 'pending' }),
            makeSubtask({ id: 's2', status: 'pending' }),
        ]
        const result = mergeSubtasks(base, [
            makeMsg({ type: 'subtask_started', task_id: 't1', subtask_id: 's1', agent_id: 'agent1', timestamp: '' }),
        ])
        expect(result[0].status).toBe('in_progress')
        expect(result[1].status).toBe('pending')
    })

    it('ignores messages for unknown subtask ids', () => {
        const base = [makeSubtask({ id: 's1', status: 'pending' })]
        const result = mergeSubtasks(base, [
            makeMsg({ type: 'subtask_started', task_id: 't1', subtask_id: 's999', agent_id: 'agent1', timestamp: '' }),
        ])
        expect(result[0].status).toBe('pending')
    })

    it('does not mutate the original base array or subtasks', () => {
        const st = makeSubtask({ id: 's1', status: 'pending' })
        const base = [st]
        const original = { ...st }
        mergeSubtasks(base, [
            makeMsg({ type: 'subtask_started', task_id: 't1', subtask_id: 's1', agent_id: 'agent1', timestamp: '' }),
        ])
        expect(st).toEqual(original)
    })

    it('returns the same reference when base is empty', () => {
        const base: SubtaskState[] = []
        const result = mergeSubtasks(base, [])
        expect(result).toBe(base)
    })
})

describe('useMemo with mergeSubtasks', () => {
    it('returns cached result when deps are referentially equal', () => {
        const base = [makeSubtask({ id: 's1', status: 'pending' })]
        const messages: WebSocketMessage[] = [
            makeMsg({ type: 'subtask_started', task_id: 't1', subtask_id: 's1', agent_id: 'agent1', timestamp: '' }),
        ]

        const { result, rerender } = renderHook(
            () => useMemo(() => mergeSubtasks(base, messages), [base, messages]),
        )

        const first = result.current
        rerender()
        expect(result.current).toBe(first)
    })

    it('recomputes when messages reference changes', () => {
        const base = [makeSubtask({ id: 's1', status: 'pending' })]
        const msg = makeMsg({ type: 'subtask_started', task_id: 't1', subtask_id: 's1', agent_id: 'agent1', timestamp: '' })

        const { result, rerender } = renderHook(
            ({ msgs }) => useMemo(() => mergeSubtasks(base, msgs), [base, msgs]),
            { initialProps: { msgs: [msg] as WebSocketMessage[] } },
        )

        const first = result.current
        rerender({ msgs: [msg] })
        expect(result.current).not.toBe(first)
    })

    it('recomputes when baseSubtasks reference changes', () => {
        const make = () => [makeSubtask({ id: 's1', status: 'pending' })]
        const messages: WebSocketMessage[] = []

        const { result, rerender } = renderHook(
            ({ base }) => useMemo(() => mergeSubtasks(base, messages), [base, messages]),
            { initialProps: { base: make() } },
        )

        const first = result.current
        rerender({ base: make() })
        expect(result.current).not.toBe(first)
    })
})
