import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
    toSubtaskState,
    VALID_SUBTASK_STATUSES,
} from '../useTaskStage'
import type { Subtask } from '../../types'

const makeSubtask = (overrides: Partial<Subtask> = {}): Subtask => ({
    id: '1',
    agent_id: 'agent-1',
    goal: 'test goal',
    name: 'test name',
    description: 'test description',
    status: 'pending',
    position: 0,
    output_preview: null,
    depends_on: [],
    ...overrides,
})

describe('VALID_SUBTASK_STATUSES', () => {
    it('contains exactly the four expected statuses', () => {
        expect(VALID_SUBTASK_STATUSES).toEqual([
            'pending',
            'in_progress',
            'completed',
            'failed',
        ])
    })
})

describe('toSubtaskState', () => {
    beforeEach(() => {
        vi.restoreAllMocks()
    })

    it.each(['pending', 'in_progress', 'completed', 'failed'] as const)(
        'maps valid status "%s" through unchanged',
        (status) => {
            const result = toSubtaskState(makeSubtask({ status }))
            expect(result.status).toBe(status)
        },
    )

    it('defaults to "pending" and warns for an unrecognized status', () => {
        const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

        const result = toSubtaskState(
            makeSubtask({ status: 'unknown_value' as Subtask['status'] }),
        )

        expect(result.status).toBe('pending')
        expect(warnSpy).toHaveBeenCalledWith(
            expect.stringContaining('unknown_value'),
        )
    })

    it('maps all other fields correctly', () => {
        const subtask = makeSubtask({
            id: '42',
            agent_id: 'agent-x',
            goal: 'my goal',
            name: 'step 1',
            description: 'do the thing',
            output_preview: 'some output',
            position: 3,
        })

        const result = toSubtaskState(subtask)

        expect(result).toMatchObject({
            id: '42',
            agentId: 'agent-x',
            goal: 'my goal',
            name: 'step 1',
            description: 'do the thing',
            outputPreview: 'some output',
            position: 3,
        })
    })

    it('handles null name, description, and output_preview', () => {
        const result = toSubtaskState(
            makeSubtask({
                name: null,
                description: null,
                output_preview: null,
            }),
        )

        expect(result.name).toBeNull()
        expect(result.description).toBeNull()
        expect(result.outputPreview).toBeNull()
    })
})
